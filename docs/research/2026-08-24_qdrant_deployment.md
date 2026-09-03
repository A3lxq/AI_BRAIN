# Research: Qdrant Deployment Specifics for ATHENA AI-BRAIN

- **Research date:** 2026-08-24
- **Researcher:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0006 (Qdrant deployment mode)
- **Depends on:** ADR-0003 (RAG orchestration, committed to Qdrant native hybrid dense+sparse fusion)

## 1. Executive Summary

Three deployment modes were evaluated: Qdrant server via Docker, a native (no-Docker) binary, and `qdrant-client`'s embedded "local mode." The decisive finding is that local mode — despite its appealing same-API dev/prod code path — is a **from-scratch Python reimplementation** of the server's query logic, not a wrapper around the real engine: it caps out around ~20,000 points (uncomfortably close to ATHENA AI-BRAIN's own stated scale), uses brute-force search with no HNSW index, is single-process-only, and has a documented fusion/prefetch parity bug (qdrant-client#713) in exactly the hybrid dense+sparse feature ADR-0003 already committed to. No official Debian/Kali package exists for a native binary. **Docker-run Qdrant, bound to localhost only, is recommended as the sole deployment mode** for both development and the running application, with local mode reserved narrowly for non-fusion-critical unit tests.

## 2. Problem Being Solved

ATHENA AI-BRAIN needs a concrete deployment mode for Qdrant (already chosen as the vector database) that supports its committed hybrid dense+sparse fusion design (ADR-0003), fits a local-first single-user Kali machine with low operational complexity, and has a sane backup/upgrade story for a long-lived local tool.

## 3. Technology Overview

Qdrant server is at v1.19.0 (2026-08-04/05); `qdrant-client` ships on a matched versioning scheme, also v1.19.0. Release cadence is roughly every 3–6 weeks — an actively, frequently maintained project. REST API on port 6333, gRPC on 6334.

## 4. Architecture Fit

- **Hybrid fusion is a server-only guarantee.** RRF (rank-based, configurable `k` since v1.16.0, per-prefetch weights since v1.17.0) and DBSF (score-distribution normalization, since v1.11.0) are mature, documented, server-side Query API features — exactly what ADR-0003 committed to. Local mode's fusion support is real but lags in parity, illustrated concretely by a documented bug (qdrant-client#713: shared query objects between prefetch and main query get mutated in place, corrupting sparse-vector resolution — closed, but reproducible evidence of the broader "new Query API features land in local mode late and with parity bugs" pattern).
- **Collection configuration matches ADR-0003's design directly**: vector size/distance must match the embedding model's output dimensionality (Cosine is the standard recommendation for modern sentence embeddings), sparse vectors configure as a separate named vector within the same collection (fixed Dot distance), and payload indexing (`keyword` for tags/folders/status, `text` for full-text over tags) should be created **before** bulk ingestion since filterable-HNSW edges benefit from indexes existing up front.
- **Single-collection guidance**: Qdrant's own docs recommend one collection with payload-based partitioning rather than one collection per vault section — matches ATHENA AI-BRAIN's small-scale, non-sharded design intent.

## 5. Alternatives Considered

| Option | Summary |
|---|---|
| Docker server | Standard documented method; persistence via bind-mount; resource footprint trivial at ATHENA AI-BRAIN's scale (~230MB RAM for 50,000 × 768-dim vectors per Qdrant's own `n_vectors × dim × 4 bytes × 1.5` formula); full snapshot API for scriptable backup. |
| Native binary (no Docker) | No official Debian/Kali package exists; only path is compiling from source (Rust toolchain, matching Dockerfile build deps) — an unmaintained, ongoing-burden path inconsistent with "low operational complexity." Third-party binary-fetcher packages exist but aren't official Qdrant releases. |
| Embedded/local mode via `qdrant-client` | Same client API surface as remote mode, appealing for a dev→prod path, but explicitly positioned by Qdrant's own docs for "development, prototyping and testing... CI/CD... Colab/Jupyter" — never described as a production target. Capped at ~20,000 points, brute-force search only (no HNSW), single-process-only (concurrent access raises `RuntimeError`), and has the documented fusion parity bug noted above. |
| Qdrant Cloud | Qdrant's own docs recommend Cloud/Private Cloud for production, directly at odds with ATHENA AI-BRAIN's local-first constitution. Ruled out as default; noted only as a possible future optional backup/sync target. |

## 6. Comparison Against Evaluation Criteria

| Criterion | Docker server | Native binary | Local mode |
|---|---|---|---|
| Feature completeness (hybrid fusion) | Full, first-class, tested by Qdrant itself | Full (same engine) but unmaintained distribution path | Documented parity gaps and a real fusion bug |
| Operational simplicity | One `docker run`/`docker stop`, bind-mount persistence, scriptable snapshot API | Requires Rust toolchain, manual build/update process | Simplest to start, but no first-class backup tooling (just a SQLite-backed file) |
| Security | Default no-auth, but addressable — bind to `127.0.0.1` explicitly rather than Docker's default all-interfaces | Same underlying binary, same considerations, no packaging to help | N/A (in-process) |
| Long-term maintainability | Pin image tag, snapshot-before-upgrade runbook, well-documented upgrade path | No official upgrade path/package to track | No documented upgrade story; storage format is Qdrant-internal, less scrutinized at this scale |
| Resource footprint | Trivial at ATHENA AI-BRAIN's scale (~hundreds of MB) | Same footprint, same engine | Lightest, but the scale ceiling (~20,000 points) is close to ATHENA AI-BRAIN's own stated target, not comfortable headroom |

## 7. ATHENA AI-BRAIN Relevance

Local mode's ~20,000-point ceiling sits uncomfortably close to ATHENA AI-BRAIN's own stated "thousands to low tens of thousands" scale target — using it as the actual deployment risks depending on behavior that diverges from what ADR-0003 already tested and assumed for hybrid fusion. Docker's resource footprint is trivial at this same scale, so there's no operational-simplicity trade-off being made by choosing the server mode — it's strictly better-fitting for ATHENA AI-BRAIN's committed architecture at no meaningful cost.

## 8. Security

Qdrant's self-hosted default is **no authentication and no encryption** on every interface. Docker's `-p 6333:6333` binds to all interfaces (`0.0.0.0`) by default, not localhost — this must be written explicitly as `-p 127.0.0.1:6333:6333` in ATHENA AI-BRAIN's deployment config rather than left to Docker's default, since the single-user local threat model depends on the service not being reachable from the network. An API key (`QDRANT__SERVICE__API_KEY`) and TLS (`QDRANT__SERVICE__ENABLE_TLS`, supported since 1.2+) remain available if ATHENA AI-BRAIN's architecture is ever extended to a networked scenario, without requiring a redesign now.

## 9. Performance

Not a meaningful differentiator at ATHENA AI-BRAIN's scale — the Docker server's resource footprint (~hundreds of MB RAM) is trivial on any modern machine, and local mode's brute-force search would actually be the slower option at scale despite the lower baseline overhead of skipping a container.

## 10. Operational Concerns

- **Upgrade constraint**: storage format compatibility is guaranteed only across one minor version step — skipping minor versions is unsupported, migration on upgrade is automatic but **not reversible** (no downgrade path). Pre-upgrade snapshots are a hard operational requirement, not optional, directly relevant to CLAUDE.md's "every session must leave a recoverable project state" rule. This should become an explicit runbook.
- **Backup**: Qdrant's snapshot API (collection-level and full-storage) is REST/Python-scriptable (`create_snapshot()`, `recover_snapshot()`, etc.) and folds cleanly into a cron/systemd-timer backup job — a capability local mode does not offer.
- **Service management**: `docker run --restart unless-stopped` (rather than `always`, to respect a deliberate manual stop) with Docker itself systemd-managed is the standard single-machine pattern; Qdrant's own docs don't prescribe this, it's standard Docker operational practice.

## 11. Recommendation

**Docker-run Qdrant server, bound to `127.0.0.1` only, managed via Docker restart policy (`unless-stopped`) on a systemd-managed Docker daemon — as the sole deployment mode for both development and the running application**, not a two-mode dev/prod split.

- Pin the image tag to a specific version (never `:latest`); snapshot before each upgrade; step through minor versions per Qdrant's documented policy.
- `QdrantClient(":memory:")` remains useful narrowly as a **unit-test fixture** for non-fusion-critical logic (fast, no Docker needed for CI-style tests) — but any test exercising the hybrid RRF/DBSF fusion path itself should run against a real (even ephemeral, testcontainers-style) Qdrant server, since that's precisely the part local mode doesn't reliably mirror.
- Native binary and Qdrant Cloud are both not recommended as the default, for the reasons in §5.

## 12. References

- [Qdrant Quickstart](https://qdrant.tech/documentation/quickstart/) · [Installation](https://qdrant.tech/documentation/installation/) · [Snapshots](https://qdrant.tech/documentation/snapshots/) · [Upgrades](https://qdrant.tech/documentation/upgrades/) · [Production Checklist](https://qdrant.tech/documentation/production-checklist/) · [Secure a Self-Hosted Qdrant Instance](https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/)
- [Collections concept](https://qdrant.tech/documentation/concepts/collections/) · [Payload Indexing](https://qdrant.tech/documentation/concepts/indexing/) · [Hybrid Queries (Query API, RRF/DBSF)](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Minimal RAM to Serve 1M Vectors (memory formula)](https://qdrant.tech/articles/memory-consumption/)
- [Qdrant GitHub repo & releases](https://github.com/qdrant/qdrant) (verified via GitHub API for dates)
- [qdrant-client (Python) README/local mode](https://github.com/qdrant/qdrant-client) · [Local mode issue #713](https://github.com/qdrant/qdrant-client/issues/713) · [python-client.qdrant.tech local package docs](https://python-client.qdrant.tech/qdrant_client.local)
- [Qdrant Pricing (Cloud free tier)](https://qdrant.tech/pricing/)

## 13. Open Questions

- Should ATHENA AI-BRAIN's Docker Compose/systemd unit definition be authored now as part of this ADR's implementation, or deferred to Phase 1 setup? Recommend Phase 1, once the exact collection schema (from the embeddings-model-choice research) is settled.
- Should the snapshot-before-upgrade runbook be a documented operational procedure in `docs/`, or automated as part of ATHENA AI-BRAIN's own Git-backup job (ADR-0005)? Worth considering as a natural extension once both subsystems exist.
