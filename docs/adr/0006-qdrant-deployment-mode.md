# ADR-0006: Qdrant Deployment Mode for ATHENA AI-BRAIN

- **ID:** ADR-0006
- **Title:** Qdrant Deployment Mode for ATHENA AI-BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-24
- **Date accepted:** 2026-08-24
- **Depends on:** ADR-0003 (RAG orchestration, committed to Qdrant native hybrid dense+sparse fusion)

## Context

ATHENA AI-BRAIN already committed to Qdrant as its vector database, including native hybrid dense+sparse fusion (ADR-0003). This ADR resolves how Qdrant actually runs on a local-first, single-user Kali machine. Three modes were researched: Docker server, a native (no-Docker) binary, and `qdrant-client`'s embedded "local mode." Full findings: [`docs/research/2026-08-24_qdrant_deployment.md`](../research/2026-08-24_qdrant_deployment.md).

Key finding: local mode is a **from-scratch Python reimplementation** of the server's query logic, not a wrapper around the real engine — capped at ~20,000 points (uncomfortably close to ATHENA AI-BRAIN's own stated scale), brute-force search with no HNSW, single-process-only, and with a documented fusion/prefetch parity bug (qdrant-client#713) in exactly the hybrid feature ADR-0003 committed to. No official Debian/Kali package exists for a native binary.

## Decision

**Accepted:** Run Qdrant as a **Docker server, bound to `127.0.0.1` only**, managed via `docker run --restart unless-stopped` on a systemd-managed Docker daemon, as the sole deployment mode for both development and the running application.

- Pin the image tag to a specific version (never `:latest`).
- Snapshot before every version upgrade, stepping through minor versions per Qdrant's documented no-skip policy.
- `QdrantClient(":memory:")` remains permitted narrowly as a unit-test fixture for non-fusion-critical logic; any test exercising hybrid RRF/DBSF fusion must run against a real (even ephemeral) Qdrant server.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-24.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Native binary (no Docker) | Rejected — no official Debian/Kali package exists; the only path is compiling from source (Rust toolchain, matching Dockerfile build deps), an unmaintained ongoing-burden path inconsistent with "low operational complexity." |
| Embedded/local mode as the primary deployment | Rejected — Qdrant's own docs position it only for development/testing/CI, never production; its ~20,000-point ceiling is too close to ATHENA AI-BRAIN's own stated scale for comfortable headroom, and it has a documented parity bug in the hybrid fusion feature ADR-0003 already committed to. Retained narrowly as a test fixture only. |
| Qdrant Cloud | Rejected as default — directly at odds with ATHENA AI-BRAIN's local-first constitution; noted only as a possible future optional backup/sync target, never the primary store. |

## Rationale

1. **Feature completeness is non-negotiable**: hybrid dense+sparse fusion (RRF/DBSF) is a mature, server-only guarantee. Using local mode as the actual deployment would risk ATHENA AI-BRAIN silently depending on behavior that diverges from what ADR-0003 tested and assumed.
2. **Docker's resource footprint is trivial at ATHENA AI-BRAIN's scale** (~230MB RAM for 50,000 × 768-dim vectors per Qdrant's own memory formula) — there is no operational-simplicity trade-off being made by choosing the server mode; it's strictly better-fitting at no meaningful cost.
3. **Docker gives a scriptable, first-class backup story** (snapshot API) that local mode does not offer (just a SQLite-backed file with no backup tooling) — directly relevant to CLAUDE.md's "every session must leave a recoverable project state" principle applied to the running system, not just the codebase.
4. **Security is addressable with minimal, explicit effort**: binding to `127.0.0.1` rather than Docker's default all-interfaces closes the main risk for a single-user local threat model, with API-key/TLS available later without a redesign if ATHENA AI-BRAIN's architecture is ever extended to a networked scenario.
5. **The upgrade constraint (no version-skip, no downgrade) is manageable but must be planned for**, not discovered later — pinning image tags and requiring pre-upgrade snapshots directly serves the project's recoverability principle.

## Consequences

- ATHENA AI-BRAIN's deployment configuration must explicitly bind Qdrant's Docker port mapping to `127.0.0.1` (e.g. `-p 127.0.0.1:6333:6333`), not rely on Docker's default all-interfaces binding.
- A Qdrant upgrade runbook (snapshot → upgrade one minor version → verify → repeat) must be documented before ATHENA AI-BRAIN's first production upgrade — this can be written as part of Phase 1 setup or folded into the Git-backup job (ADR-0005) as a natural extension.
- Collection schema design (vector size/distance, sparse vector configuration, payload indexes for tags/folders/status) is deferred to the embeddings-model-choice research, since vector dimensionality depends on the chosen embedding model.
- `QdrantClient(":memory:")` is permitted in ATHENA AI-BRAIN's test suite only for non-fusion-critical logic; hybrid-fusion tests must run against a real server instance.
- Docker Compose/systemd unit authoring is deferred to Phase 1 implementation, not this ADR.

## References

See [`docs/research/2026-08-24_qdrant_deployment.md`](../research/2026-08-24_qdrant_deployment.md) §12 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-24, with no modifications requested.

Remaining open item, carried forward as an implementation-time decision: should the snapshot-before-upgrade runbook be a standalone documented procedure, or automated as part of ATHENA AI-BRAIN's Git-backup job? Flagged for Phase 1 design.
