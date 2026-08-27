# ADR-0009: Filesystem Event Architecture for AI_BRAIN

- **ID:** ADR-0009
- **Title:** Filesystem Event Architecture for AI_BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-24
- **Date accepted:** 2026-08-24
- **Depends on:** ADR-0001 (Python/asyncio), ADR-0002 (Huey/SQLite job queue)

## Context

AI_BRAIN needs to translate raw, noisy filesystem events (from `watchdog` watching the Obsidian vault) into durable Huey job triggers, without missing real changes or double-processing, and safely bridging watchdog's thread-based callback model into AI_BRAIN's asyncio/Huey architecture. Full findings: [`docs/research/2026-08-24_filesystem_event_architecture.md`](../research/2026-08-24_filesystem_event_architecture.md).

Key findings: `watchdog` has no built-in time-window debouncing beyond adjacent-duplicate suppression; editor save patterns (temp-file+rename) and inotify's own guarantees (non-atomic move-pairing, cross-boundary moves degrading to delete+create — a confirmed open watchdog issue, #308) mean precise "one true save" detection at the filesystem layer is fighting an inherently unreliable substrate. AI_BRAIN's own Testing Strategy already tolerates duplicate events and repeated jobs — a direct design signal.

## Decision

**Accepted:** Adopt a light-debouncing-plus-idempotent-jobs filesystem event architecture:

1. A single `Observer` with `schedule(handler, vault_root, recursive=True)`, excluding `.git`/`.obsidian`/plugin-cache subtrees in the handler.
2. **Light, non-semantic event-layer debouncing**: normalize every raw event to "path P changed" (moves = two path-changed signals), tracked via a per-path last-seen-timestamp map with a short fixed quiet-window (~1–2 seconds, tuned empirically in Phase 1).
3. The debounce layer's "path settled" callback calls the Huey enqueue function **directly and synchronously** (no asyncio bridge needed, since Huey's SQLite enqueue is just a DB write); `asyncio.run_coroutine_threadsafe`/`loop.call_soon_threadsafe` reserved only for AI_BRAIN's own asyncio-native coordination/status/logging layer.
4. **Job-layer idempotency as the real safety net**: every triggered job compares current file mtime/content-hash against indexed state and no-ops if unchanged, uses `huey.lock_task` to prevent concurrent reindexing of the same path, and treats "path X changed" as a trigger to re-derive current truth from disk, never as a diff to apply.
5. A **periodic/startup reconciliation (full-scan) job**, independent of the event stream, comparing vault-on-disk state vs. index state, as a backstop for events dropped during downtime or queue overflow.
6. Document a `fs.inotify.max_user_watches` sysctl raise (e.g. to 262144) as a Debian/Kali deployment prerequisite.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-24.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Precise event-layer "one true save" detection | Rejected — fights an inherently unreliable substrate (non-atomic cookie pairing, cross-boundary move degradation to delete+create); buys an efficiency gain the Testing Strategy's own tolerance for duplicates says isn't needed. |
| `watchdog.utils.event_debouncer.EventDebouncer` as a hard dependency | Rejected — lives outside watchdog's documented stable API; the accumulate-and-callback pattern is worth borrowing conceptually, but a simpler hand-written last-seen-timestamp check is preferred per "small composable modules." |
| Routing every raw event through `asyncio.run_coroutine_threadsafe` to enqueue jobs | Rejected as unnecessary complexity — Huey's SQLite enqueue is a synchronous DB write, safe to call directly from the debounce thread. |
| Skipping the reconciliation/full-scan backstop | Rejected — the master specification's "events should be durable enough to recover from failures" requirement isn't met by the event pipeline alone, given documented gaps (queue overflow, process downtime, cross-boundary move degradation). |

## Rationale

1. **The Testing Strategy's explicit tolerance for duplicate events and repeated jobs is a direct design signal**, not an incidental allowance — it tells us the architecture should bias toward "when in doubt, enqueue" rather than trying to achieve filesystem-layer precision the underlying substrate (inotify) cannot reliably deliver.
2. **The asymmetry between false positives and false negatives favors light debouncing**: over-triggering costs a cheap no-op job (given job-layer idempotency); under-triggering costs a silently stale index, which is unrecoverable without an explicit reconciliation pass. This directly matches the master specification's durability requirement.
3. **Avoiding the asyncio bridge for the enqueue step is a legitimate simplification**, not a shortcut — Huey's SQLite-backed design (ADR-0002) makes this safe, and adding an unnecessary asyncio round-trip would be complexity without benefit, contrary to "do not optimize prematurely" and "small composable modules."
4. **The reconciliation backstop is required, not optional**, because the event-driven pipeline has documented, confirmed gaps (cross-boundary move degradation, potential `IN_Q_OVERFLOW` under burst load, process-downtime blind spots) that no amount of event-layer engineering fully closes.

## Consequences

- The exact quiet-window duration (1–2 seconds suggested) must be tuned empirically during Phase 1 prototyping against real Obsidian save behavior — not hardcoded from this research alone.
- The reconciliation/full-scan job's exact trigger (every startup, periodic schedule, or both) is deferred to job/queue implementation design, not decided here.
- Every filesystem-event-triggered Huey job must be designed and tested for idempotency (content-hash/mtime comparison, path-level locking) as a first-class requirement, not an afterthought — this directly shapes the indexing subsystem's design.
- The `fs.inotify.max_user_watches` sysctl raise must be documented as a Phase 1 deployment prerequisite.
- `.git`, `.obsidian`, and plugin-cache subtrees must be excluded from the watch scope in the event handler.

## References

See [`docs/research/2026-08-24_filesystem_event_architecture.md`](../research/2026-08-24_filesystem_event_architecture.md) §12 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-24, with no modifications requested.

Remaining open item, carried forward as an implementation-time decision: should `FileClosedEvent` (`IN_CLOSE_WRITE`, Linux-only) be used as an additional settle signal alongside the timestamp-based debouncer? Low-stakes, decide during implementation.
