"""Filesystem watcher and debounce layer for the ATHENA AI-BRAIN vault.

See `docs/design/migration-runner-and-vault-ingestion.md` §2.3,
`docs/adr/0009-filesystem-event-architecture.md` (decisions 1-3), and
`docs/EVENT_MODEL.md` §3.1-§3.3 for the design this module implements.

This module knows nothing about Huey, the `events` table, or any database
(CLAUDE.md rule 15: internal modules stay decoupled from what consumes
them). It does exactly one thing: watch a directory tree, collapse bursts
of raw `watchdog` events per path into a single "path settled" signal once
that path has gone quiet for a configurable window, and call an
injected `on_settle` callback exactly once per settled change. Semantic
classification (created/modified/moved/deleted) is deliberately *not*
performed here — ADR-0009 treats "path X changed" as a trigger to
re-derive truth from disk at job execution time, never as a diff to apply.

Everything here runs on plain OS threads: the `watchdog` `Observer`'s own
thread, plus one `threading.Timer` thread per in-flight debounce window.
No `asyncio` is used anywhere in this module, per ADR-0009 decision 3.

Deviation from ADR-0009's assumed API, found against the installed
``watchdog==6.0.0`` (not assumed from the ADR's own research, which may
have targeted an older release): the ADR's research phase evaluated
``watchdog.utils.event_debouncer.EventDebouncer`` and rejected it as
"outside watchdog's documented stable API." In 6.0.0 that module still
exists in the same undocumented-internal form, so the rejection still
holds and this module's hand-written last-seen/`threading.Timer` debounce
is implemented as originally decided. Separately, 6.0.0's
``FileSystemEvent`` is a ``@dataclass(unsafe_hash=True)`` with
``event_type``/``is_directory`` as non-init fields set by each concrete
subclass, and ``FileSystemEventHandler.dispatch`` still calls
``on_created``/``on_modified``/``on_moved``/``on_deleted`` exactly as
older documentation describes, so no adaptation was needed there. Nothing
else about the API surface this module depends on
(``Observer.schedule(handler, path, recursive=...)``, ``Observer.start``/
``stop``/``join``, ``FileSystemEventHandler.on_*``) differs from what
ADR-0009 and the design doc assumed.

One genuine behavioral discrepancy *was* found and is handled explicitly
(see ``_DebouncingEventHandler``'s docstring): watchdog 6.0.0 synthesizes
a ``DirModifiedEvent`` for a directory whenever a child inside it
changes, in addition to the child's own event. Neither ADR-0009 nor the
design doc mention this, and forwarding it would double-signal every
single file change with a spurious parent-directory "path changed" event.
This module filters out every ``is_directory=True`` event rather than
forwarding it, since ``ingest_note()`` (design doc §2.4) only ever
operates on file paths.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Final

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_EXCLUDED_SUBTREES",
    "VaultWatcher",
]

#: Default debounce/settle window, per `docs/design/migration-runner-and-
#: vault-ingestion.md` §2.3 (splits ADR-0009's suggested 1-2s range). An
#: empirical Phase 2 tuning input, not a researched-and-final value -- see
#: that design's §8 open questions.
DEFAULT_DEBOUNCE_SECONDS: Final[float] = 1.5

#: Subtrees the watcher must never surface as "path settled" signals
#: (ADR-0009 decision 1). Each entry is a tuple of path components that
#: must appear as a contiguous run somewhere in a candidate path's parts
#: for that path to be excluded. `.obsidian` is excluded in full -- not
#: just a `.obsidian/plugins` subpattern -- since Obsidian's entire config
#: directory is app-internal, never vault content, and excluding it whole
#: already covers any plugin-cache subtree living under it (the design
#: doc/ADR-0009 do not nail down an exact plugin-cache pattern beyond
#: this). Extend this tuple -- not the matching logic in `_is_excluded` --
#: if a deployment's plugin cache or another app's scratch directory ever
#: needs excluding from somewhere else in the tree.
DEFAULT_EXCLUDED_SUBTREES: Final[tuple[tuple[str, ...], ...]] = (
    (".git",),
    (".obsidian",),
)


def _to_str_path(raw_path: bytes | str) -> str:
    """Normalize a watchdog event path (`bytes | str`, depending on how the
    watched root's path was originally supplied) to `str`.
    """
    if isinstance(raw_path, bytes):
        return os.fsdecode(raw_path)
    return raw_path


def _is_excluded(path: str, excluded_subtrees: tuple[tuple[str, ...], ...]) -> bool:
    """True if `path` passes through any of `excluded_subtrees`."""
    parts = PurePath(path).parts
    for subtree in excluded_subtrees:
        width = len(subtree)
        for start in range(len(parts) - width + 1):
            if parts[start : start + width] == subtree:
                return True
    return False


class _DebouncingEventHandler(FileSystemEventHandler):
    """Normalizes every raw watchdog *file* event to one or more "path
    changed" signals (a move yields two independent signals, per ADR-0009
    decision 2), dropping anything under an excluded subtree before it
    ever reaches the debounce map.

    Directory-level events (`is_directory=True`) are dropped here too, not
    forwarded as their own "path changed" signal. This is a deliberate
    addition beyond what ADR-0009/the design doc spell out explicitly:
    watchdog 6.0.0 (verified directly -- see this module's top-of-file
    note) synthesizes a `DirModifiedEvent` for a directory every time one
    of its *children* changes, in addition to the child's own
    create/modify event. Forwarding those would mean every single file
    change also "settles" its containing directory as a second, spurious
    path -- a path `ingest_note()` (§2.4 of the design doc) has no use for,
    since that job only ever reads and hashes a file. Folder-level renames
    consequently produce no signal of their own under this policy; that is
    an accepted gap of the same shape ADR-0009 already accepts for
    cross-boundary moves (delete+create instead of a paired move), not a
    new one this module introduces silently.
    """

    def __init__(
        self,
        on_path_changed: Callable[[str], None],
        excluded_subtrees: tuple[tuple[str, ...], ...],
    ) -> None:
        super().__init__()
        self._on_path_changed = on_path_changed
        self._excluded_subtrees = excluded_subtrees

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._signal(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._signal(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._signal(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._signal(event.src_path)
            self._signal(event.dest_path)

    def _signal(self, raw_path: bytes | str) -> None:
        path = _to_str_path(raw_path)
        if _is_excluded(path, self._excluded_subtrees):
            return
        self._on_path_changed(path)


@dataclass(frozen=True)
class _PendingPath:
    """One path's in-flight debounce state. Replaced wholesale (never
    mutated) on every new event for that path -- `sequence` is what lets a
    superseded timer's fire callback recognize it is stale (see
    `VaultWatcher._fire`).
    """

    sequence: int
    timer: threading.Timer


class VaultWatcher:
    """Watches `vault_root` for filesystem changes and calls `on_settle`
    exactly once per path, once that path has gone quiet for
    `debounce_seconds` with no newer event since it was (re)scheduled.

    Pure filesystem -> debounce -> callback plumbing, testable in complete
    isolation (CLAUDE.md rule 15). Not thread-per-instance-safe for
    concurrent `start`/`stop` calls from multiple threads -- callers are
    expected to drive the lifecycle from one thread, same as `Observer`
    itself.
    """

    def __init__(
        self,
        vault_root: str,
        on_settle: Callable[[str], None],
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        excluded_subtrees: tuple[tuple[str, ...], ...] = DEFAULT_EXCLUDED_SUBTREES,
    ) -> None:
        if debounce_seconds <= 0:
            raise ValueError(f"debounce_seconds must be positive, got {debounce_seconds!r}")

        self._vault_root = vault_root
        self._on_settle = on_settle
        self._debounce_seconds = debounce_seconds
        self._excluded_subtrees = excluded_subtrees

        self._lock = threading.Lock()
        # Per-path last-seen-timestamp map (EVENT_MODEL.md §3.2), guarded
        # by `_lock` alongside the pending-timer bookkeeping below.
        self._last_seen: dict[str, float] = {}
        self._pending: dict[str, _PendingPath] = {}
        self._next_sequence = 0

        self._handler = _DebouncingEventHandler(self._record_event, excluded_subtrees)
        self._observer: BaseObserver = Observer()
        self._started = False

    def start(self) -> None:
        """Start the single `Observer` thread watching `vault_root`
        recursively. Idempotent -- calling `start` more than once is a
        no-op.
        """
        if self._started:
            return
        self._observer.schedule(self._handler, self._vault_root, recursive=True)
        self._observer.start()
        self._started = True

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the `Observer` thread and cancel every pending debounce
        timer. No `on_settle` call for a path already in flight when
        `stop` is called will fire afterward: every pending timer is both
        cancelled and dropped from the internal state under lock, and
        `_fire` re-checks that state before ever invoking the callback.
        Idempotent -- calling `stop` before `start`, or twice in a row, is
        a no-op.
        """
        if not self._started:
            return
        self._observer.stop()
        self._observer.join(timeout=timeout)
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            self._last_seen.clear()
        for entry in pending:
            entry.timer.cancel()
        self._started = False

    def __enter__(self) -> VaultWatcher:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _record_event(self, path: str) -> None:
        """Runs on the watchdog `Observer` thread. Updates the last-seen
        timestamp for `path` and (re)schedules its settle timer,
        superseding whatever timer was previously pending for this path.
        """
        now = time.monotonic()
        previous: _PendingPath | None
        with self._lock:
            self._last_seen[path] = now
            self._next_sequence += 1
            sequence = self._next_sequence
            previous = self._pending.get(path)
            timer = threading.Timer(self._debounce_seconds, self._fire, args=(path, sequence))
            timer.daemon = True
            self._pending[path] = _PendingPath(sequence=sequence, timer=timer)

        # Cancel the superseded timer -- and start the new one -- outside
        # the lock: neither Timer.cancel() nor Timer.start() needs to hold
        # it, and holding it here would serialize unrelated paths' events
        # behind this path's timer bookkeeping for no benefit.
        if previous is not None:
            previous.timer.cancel()
        timer.start()

    def _fire(self, path: str, sequence: int) -> None:
        """Runs on a `threading.Timer` thread. Fires `on_settle(path)`
        exactly once, and only if no newer event superseded this timer
        after it was scheduled -- guards against the race where
        `Timer.cancel()` is called after the timer has already started
        running (cancel() is a no-op at that point per the stdlib's own
        documented behavior).
        """
        with self._lock:
            current = self._pending.get(path)
            if current is None or current.sequence != sequence:
                return  # superseded by a newer event, or already settled/stopped
            del self._pending[path]
            self._last_seen.pop(path, None)

        # Call the caller's callback outside the lock -- if `on_settle`
        # itself touches this watcher (e.g. inspects state, or is called
        # from a test that also calls stop()), holding the lock here would
        # risk deadlock.
        self._on_settle(path)
