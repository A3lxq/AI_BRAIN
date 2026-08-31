"""Tests for ai_brain.vault.watcher.

These are necessarily timing-sensitive integration tests against a real
`watchdog.observers.Observer` and a real temp directory, not pure unit
tests -- per the task's own framing. A short debounce window keeps them
fast; waits are bounded polling loops, not fixed sleeps, to avoid flaking.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ai_brain.vault.watcher import VaultWatcher

# Short enough to keep the suite fast, long enough that a burst of writes
# performed in a tight test loop reliably lands inside one debounce window
# even under CI scheduling jitter.
TEST_DEBOUNCE_SECONDS = 0.25

# Generous multiple of the debounce window used as the upper bound for
# "settle should have happened by now" polling loops below.
WAIT_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.02


class _SettleRecorder:
    """Thread-safe collector for `on_settle` calls, usable directly as the
    `on_settle` callable.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._settled: list[str] = []

    def __call__(self, path: str) -> None:
        with self._lock:
            self._settled.append(path)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._settled)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SECONDS,
    interval: float = POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll `predicate` until it's true or `timeout` elapses. Returns
    whether it became true -- callers still assert explicitly so a
    timeout failure shows a useful message rather than just "False".
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def recorder() -> _SettleRecorder:
    return _SettleRecorder()


@pytest.fixture
def watcher(tmp_path: Path, recorder: _SettleRecorder) -> Iterator[VaultWatcher]:
    w = VaultWatcher(
        str(tmp_path),
        recorder,
        debounce_seconds=TEST_DEBOUNCE_SECONDS,
    )
    w.start()
    try:
        yield w
    finally:
        w.stop()


def test_single_file_creation_settles_once(
    tmp_path: Path, watcher: VaultWatcher, recorder: _SettleRecorder
) -> None:
    target = tmp_path / "note.md"
    target.write_text("hello", encoding="utf-8")

    assert _wait_until(lambda: len(recorder.snapshot()) >= 1)

    # Give any spurious extra callback a chance to show up before asserting
    # the final count.
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)
    settled = recorder.snapshot()
    assert settled == [str(target)]


def test_burst_of_writes_settles_exactly_once(
    tmp_path: Path, watcher: VaultWatcher, recorder: _SettleRecorder
) -> None:
    target = tmp_path / "note.md"
    target.write_text("v0", encoding="utf-8")

    # Rapid writes within the debounce window should collapse to one
    # settle callback, not one per write.
    for i in range(10):
        target.write_text(f"v{i}", encoding="utf-8")
        time.sleep(TEST_DEBOUNCE_SECONDS / 5)

    assert _wait_until(lambda: len(recorder.snapshot()) >= 1)
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)
    settled = recorder.snapshot()
    assert settled == [str(target)]


def test_move_produces_two_settle_callbacks(
    tmp_path: Path, watcher: VaultWatcher, recorder: _SettleRecorder
) -> None:
    source = tmp_path / "old.md"
    dest = tmp_path / "new.md"
    source.write_text("content", encoding="utf-8")

    assert _wait_until(lambda: len(recorder.snapshot()) >= 1)
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)
    recorder.snapshot()  # drain the creation's settle before the move

    source.rename(dest)

    assert _wait_until(lambda: len(recorder.snapshot()) >= 2)
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)
    settled = set(recorder.snapshot())
    assert settled == {str(source), str(dest)}


def test_git_and_obsidian_subtrees_never_settle(
    tmp_path: Path, watcher: VaultWatcher, recorder: _SettleRecorder
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    obsidian_dir = tmp_path / ".obsidian" / "plugins" / "some-plugin"
    obsidian_dir.mkdir(parents=True)

    (git_dir / "COMMIT_EDITMSG").write_text("x", encoding="utf-8")
    (obsidian_dir / "cache.json").write_text("{}", encoding="utf-8")

    # Also write a normal file so we have a positive signal that the
    # watcher is alive and events are being processed at all -- otherwise
    # "nothing settled" could mean "the watcher is broken", not "exclusion
    # worked".
    control = tmp_path / "control.md"
    control.write_text("hi", encoding="utf-8")

    assert _wait_until(lambda: str(control) in recorder.snapshot())
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)

    settled = recorder.snapshot()
    assert str(control) in settled
    assert not any(".git" in path for path in settled)
    assert not any(".obsidian" in path for path in settled)


def test_two_independent_paths_settle_independently(
    tmp_path: Path, watcher: VaultWatcher, recorder: _SettleRecorder
) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"

    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")

    assert _wait_until(lambda: len(recorder.snapshot()) >= 2)
    time.sleep(TEST_DEBOUNCE_SECONDS * 2)

    settled = recorder.snapshot()
    assert settled.count(str(first)) == 1
    assert settled.count(str(second)) == 1


def test_stop_prevents_further_settle_callbacks(
    tmp_path: Path, recorder: _SettleRecorder
) -> None:
    w = VaultWatcher(str(tmp_path), recorder, debounce_seconds=TEST_DEBOUNCE_SECONDS)
    w.start()

    target = tmp_path / "note.md"
    target.write_text("hello", encoding="utf-8")

    # Stop well before the debounce window elapses, so the pending timer
    # is still in flight when we cancel it.
    time.sleep(TEST_DEBOUNCE_SECONDS / 5)
    w.stop()

    assert w._observer.is_alive() is False

    # Wait comfortably past the original debounce window and confirm no
    # settle callback ever fired.
    time.sleep(TEST_DEBOUNCE_SECONDS * 4)
    assert recorder.snapshot() == []


def test_context_manager_starts_and_stops(tmp_path: Path, recorder: _SettleRecorder) -> None:
    with VaultWatcher(str(tmp_path), recorder, debounce_seconds=TEST_DEBOUNCE_SECONDS) as w:
        assert w._observer.is_alive() is True
        target = tmp_path / "note.md"
        target.write_text("hello", encoding="utf-8")
        assert _wait_until(lambda: len(recorder.snapshot()) >= 1)

    assert w._observer.is_alive() is False
