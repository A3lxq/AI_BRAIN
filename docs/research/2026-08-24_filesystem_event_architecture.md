# Research: Filesystem Event Architecture for AI_BRAIN

- **Research date:** 2026-08-24
- **Researcher:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0009 (filesystem event architecture)
- **Depends on:** ADR-0001 (Python/asyncio), ADR-0002 (Huey/SQLite job queue)

## 1. Executive Summary

`watchdog` (v6.0.0, current stable) provides recursive vault watching via userspace-emulated per-directory inotify watches, but has no built-in time-window debouncing beyond adjacent-duplicate suppression, and its `EventDebouncer` utility is undocumented/unstable API. Editor save patterns (temp-file+rename) and inotify's own guarantees (non-atomic move-pairing, cross-boundary moves degrading to delete+create — a confirmed open watchdog issue) mean precise "one true save" detection at the filesystem layer is fighting an inherently unreliable substrate. Since AI_BRAIN's Testing Strategy explicitly tolerates duplicate events and repeated jobs, the recommended design biases toward **light debouncing at the event layer plus idempotent Huey jobs as the real safety net** — over-triggering costs a cheap no-op job; under-triggering costs a silently stale index, an asymmetry the design should lean against. A residual gap — events dropped during process downtime or queue overflow — requires a periodic reconciliation/full-scan job as a backstop, not solved by the event pipeline alone.

## 2. Problem Being Solved

AI_BRAIN needs to translate raw, noisy filesystem events from watching the Obsidian vault into meaningful, durable triggers for Huey jobs (reindexing, etc.), without missing real changes or double-processing, bridging `watchdog`'s thread-based callback model into AI_BRAIN's asyncio/Huey architecture.

## 3. Technology Overview

`watchdog` v6.0.0 (released 2024-11-01, still current as of this research) uses Linux's inotify API directly. In 6.0.0 the inotify emitter switched from deprecated `select.select()` to `select.poll()`. `Observer` runs on a background `threading.Thread`, not the main thread or any asyncio event loop — event handler callbacks execute synchronously on that thread.

## 4. Architecture Fit

- **Recursive watching is userspace-emulated, not kernel-native**: inotify has no kernel-level recursion; watchdog walks the tree at schedule time and adds one inotify watch per subdirectory, dynamically adding/removing watches as subdirectories change. Watch count scales with **directory count, not file count** — a vault with 10,000 notes in a few hundred folders costs a few hundred watches, comfortably under Debian's default `fs.inotify.max_user_watches` of 8192. Still, documenting a sysctl raise (e.g. to 262144) is cheap insurance worth doing regardless of current vault size.
- **No built-in debouncing beyond adjacent-duplicate suppression**: the public `Observer` only skips enqueueing an event literally identical to the one already at the queue tail — not time-windowed debouncing. `watchdog.utils.event_debouncer.EventDebouncer` exists and is used internally by `watchmedo`, but lives outside the documented stable API — usable as a borrowed pattern, not a hard dependency.
- **Move/rename detection has a confirmed, open limitation**: cross-boundary moves (out of a watched directory into an unwatched one, or vice versa) degrade to delete-only or create-only events — tracked as an open, unfixed issue (gorakhargosh/watchdog#308). Within a single recursively-watched vault root, moves reliably show as clean `FileMovedEvent`s; only moves crossing the vault boundary degrade — which is actually the semantically correct interpretation for indexing purposes anyway (a note leaving the vault should be treated as removed from the index).
- **Thread-to-asyncio bridge**: the documented, correct mechanism is `asyncio.run_coroutine_threadsafe(coro, loop)` (to schedule a coroutine from another thread) or `loop.call_soon_threadsafe(callback)` (for a plain callback). However, since Huey's SQLite-backed enqueue (ADR-0002) is just a synchronous DB write, **the asyncio bridge is not required for the enqueue step itself** — it's only needed for parts of AI_BRAIN's own asyncio-native coordination/status/logging layer that need to observe "an event was seen."

## 5. Alternatives Considered

Not a multi-library comparison (watchdog was already established as the standard choice in earlier Phase 0 research) — this research instead evaluated **integration pattern alternatives**:

| Pattern | Verdict |
|---|---|
| Precise event-layer "one true save" detection (parsing temp-file+rename semantics, distinguishing autosave bursts from deliberate saves) | Rejected — fights an inherently unreliable substrate (non-atomic cookie pairing, cross-boundary move degradation); buys an efficiency gain the architecture's own Testing Strategy says it doesn't need. |
| `watchdog.utils.event_debouncer.EventDebouncer` (time-window coalescing) | Not adopted as a hard dependency — undocumented/unstable API location; the pattern (accumulate events for N seconds, callback on quiet) is worth borrowing, but a simpler hand-written last-seen-timestamp check is preferred per "small composable modules." |
| Routing every raw event through `asyncio.run_coroutine_threadsafe` to enqueue Huey jobs | Rejected as unnecessary — Huey's SQLite enqueue is a synchronous DB write, safe to call directly from the watchdog/debounce thread without an asyncio round-trip. |
| **Light debouncing + idempotent job design (recommended)** | Biases toward "when in doubt, enqueue" — matches the Testing Strategy's explicit tolerance for duplicate events/repeated jobs. |

## 6. Comparison Against Evaluation Criteria

| Criterion | Finding |
|---|---|
| Reliability (avoid missing changes over avoiding over-triggering) | Light debouncing + idempotent jobs directly serves this — the Testing Strategy's tolerance for duplicates is effectively a design signal favoring this bias. |
| Correctness for editor-save patterns | Confirmed watchdog surfaces `FileClosedEvent` (Linux-only, via `IN_CLOSE_WRITE`) as an extra signal, but this isn't cross-platform and isn't required as a foundation; light debouncing handles multi-write saves without needing to interpret save semantics. |
| Clean asyncio/Huey integration | Confirmed safe pattern: watchdog thread → light debounce (thread-safe, no asyncio needed) → direct synchronous Huey enqueue; asyncio bridge reserved only for AI_BRAIN's own coordination layer. |
| Resource/scale appropriateness | Watch count scales with directory count; personal-vault scale (thousands of files) is well within inotify's designed envelope; event *storms* (bulk git pull/import) are the real scale risk, not steady-state watch count. |
| Simplicity | Recommended design uses a simple fixed quiet-window check, not the more elaborate `EventDebouncer` machinery — least custom logic that meets the reliability bar. |

## 7. AI_BRAIN Relevance

The recommended architecture maps directly onto AI_BRAIN's event model requirements (note created/modified/deleted/moved) while respecting the master specification's "events should be durable enough to recover from failures" requirement — met not by the event pipeline alone but by pairing it with job-level idempotency (content-hash/mtime comparison before real work) and a periodic reconciliation/full-scan job as backstop for events dropped during downtime or `IN_Q_OVERFLOW` bursts.

## 8. Security

Excluding `.git`, `.obsidian`, and plugin-cache subtrees from the watch scope both reduces noise and avoids wasting watches on non-content directories — a minor but relevant hygiene practice. No significant security differentiator among the integration patterns evaluated; this is primarily a reliability/architecture research topic, not a security-sensitive one, though the reconciliation-job backstop is relevant to the constitution's "durable enough to recover from failures" requirement.

## 9. Performance

Not a meaningful concern at AI_BRAIN's vault scale — inotify/watchdog is well within its designed envelope for thousands of files. The one real risk is event storms (bulk git pull, bulk import) rather than steady-state resource use, mitigated by the light-debouncing design absorbing bursts into settled per-path triggers.

## 10. Operational Concerns

- Document the `fs.inotify.max_user_watches` sysctl raise as a Debian/Kali deployment prerequisite, even though current vault scale doesn't require it — cheap insurance against a silent-failure class.
- A periodic or startup-time reconciliation/full-scan job (comparing vault-on-disk state vs. index state) is required as a backstop independent of the event stream — this is a residual gap not solved by the event-driven pipeline alone, and should be scoped as its own design item, not assumed away.
- Exclude `.git`, `.obsidian`, and plugin-cache subtrees from the watch scope.

## 11. Recommendation

- **Watching**: one `Observer`, single `schedule(handler, vault_root, recursive=True)` call; exclude `.git`/`.obsidian`/plugin-cache subtrees in the handler (watchdog has no native path-exclusion at `schedule()` for recursive watches).
- **Event-layer debouncing**: light, not semantic — normalize every raw event to "path P changed" (moves treated as two path-changed signals: old and new path), tracked via a per-path last-seen-timestamp map with a short fixed quiet-window (~1–2 seconds, tuned empirically), collapsing temp-write+rename pairs and autosave bursts into a single settle-point per path without needing to distinguish event types.
- **Thread-to-job bridge**: the debounce layer's "path settled" callback calls the Huey enqueue function directly (synchronous SQLite write, safe off-thread) — no asyncio bridge needed for this step; `asyncio.run_coroutine_threadsafe`/`loop.call_soon_threadsafe` reserved only for AI_BRAIN's own asyncio-native coordination/status/logging layer.
- **Job-layer idempotency does the heavy lifting**: every triggered Huey job (`reindex_note(path)`, etc.) must compare current file mtime/content-hash against what's indexed and no-op if unchanged, use `huey.lock_task` where a path must not be reindexed concurrently, and treat "path X changed" as a trigger to re-derive current truth from disk — never as a diff to apply.
- **Reconciliation backstop**: a periodic/startup full-scan job comparing vault-on-disk state vs. index state, independent of the event stream, to guarantee eventual consistency despite dropped events or process downtime.

## 12. References

- [watchdog GitHub repository](https://github.com/gorakhargosh/watchdog) · [Releases](https://github.com/gorakhargosh/watchdog/releases) · [PyPI](https://pypi.org/project/watchdog/)
- [watchdog docs — API reference](https://python-watchdog.readthedocs.io/en/stable/api.html) · [Installation/Supported Platforms & Caveats](https://python-watchdog.readthedocs.io/en/stable/installation.html)
- [watchdog source — inotify observer](https://github.com/gorakhargosh/watchdog/blob/master/src/watchdog/observers/inotify.py) · [`EventDebouncer` utility](https://github.com/gorakhargosh/watchdog/blob/master/src/watchdog/utils/event_debouncer.py)
- [watchdog issue #308 — cross-boundary move bug](https://github.com/gorakhargosh/watchdog/issues/308) · [Issue #46](https://github.com/gorakhargosh/watchdog/issues/46)
- [Linux inotify(7) man page](https://man7.org/linux/man-pages/man7/inotify.7.html)
- [LWN.net — "Filesystem notification, part 2"](https://lwn.net/Articles/605128/)
- [inotify watch-limit tuning guidance](https://sleeplessbeastie.eu/2023/04/19/how-to-increase-the-maximum-number-of-file-watches-that-are-allowed-for-each-user/) · [JetBrains inotify watches limit doc](https://intellij-support.jetbrains.com/hc/en-us/articles/15268113529362-Inotify-Watches-Limit-Linux)
- [Python docs — asyncio thread integration](https://docs.python.org/3/library/asyncio-dev.html)
- [Huey documentation — task locking/guide](https://huey.readthedocs.io/en/latest/guide.html) · [Huey GitHub/issues](https://github.com/coleifer/huey)

## 13. Open Questions

- What exact quiet-window duration (1–2 seconds suggested) should be used for the light debouncer? Recommend tuning empirically during Phase 1 prototyping against real Obsidian save behavior.
- Should the reconciliation/full-scan job run on every startup, on a periodic schedule, or both? Deferred to job/queue implementation design.
- Should `FileClosedEvent` (`IN_CLOSE_WRITE`, Linux-only) be used as an additional settle signal alongside the timestamp-based debouncer, or is the quiet-window alone sufficient? Low-stakes, decide during implementation.
