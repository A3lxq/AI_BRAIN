"""The Huey worker entry point (design doc §2.10).

Resolves the placeholder `deployment/systemd/ai-brain-huey-worker.service`'s
`ExecStart=` has referenced since Phase 1: run via
`huey_consumer.py ai_brain.worker.huey`.

Constraint this module relies on and does not itself enforce: the filesystem
watcher is started from a `@huey.on_startup()` hook, which Huey's consumer
calls once per worker thread/process (verified against the installed huey
API, `huey/consumer.py`'s `Worker.initialize()`). `huey_consumer`'s default
worker count is 1, matching `deployment/systemd/ai-brain-huey-worker.service`
(no `-w` flag), so the hook fires exactly once in the deployed configuration.
Running the consumer with `-w N` for N > 1 would start N redundant `Observer`
instances watching the same vault -- do not do that without first adding a
singleton guard here.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from huey import SqliteHuey, crontab
from huey.serializer import SignedSerializer
from qdrant_client import QdrantClient

from ai_brain.config import AIBrainConfig, load_config
from ai_brain.db.connection import open_connection
from ai_brain.db.repository import events as events_repo
from ai_brain.hardening.permissions import ensure_private_dir
from ai_brain.hardening.serializer import SerializerMisconfigured, assert_safe_job_serializer
from ai_brain.indexing.index_note import IndexBootstrapSummary, index_bootstrap, index_note
from ai_brain.indexing.qdrant_store import ensure_collection
from ai_brain.safety.paths import VaultRoot
from ai_brain.vault.bootstrap import BootstrapSummary, bootstrap_ingest_vault
from ai_brain.vault.ingest import ingest_note
from ai_brain.vault.reconcile import ReconciliationSummary, reconcile_vault
from ai_brain.vault.watcher import VaultWatcher

__all__ = [
    "build_huey",
    "huey",
    "start_watcher",
    "ingest_note_task",
    "index_note_task",
    "reconcile_vault_task",
]

logger = logging.getLogger(__name__)


def build_huey(config: AIBrainConfig) -> SqliteHuey:
    """Construct the one Huey instance every part of AI_BRAIN's job/lock
    machinery must share -- CLI commands that need `huey.lock_task` (e.g.
    `ai-brain ingest bootstrap`) call this with the same `config` rather than
    constructing their own, so locks are taken against the same underlying
    storage (`name`/`filename` pair) the real worker process uses.

    Hard-fails via `assert_safe_job_serializer` (Phase 1, unchanged) if
    misconfigured -- a worker (or CLI command sharing its lock store) must
    never run with an unauthenticated or empty-secret serializer. Checked
    before ever constructing `SignedSerializer`, not after: `SignedSerializer`
    itself raises huey's own `ConfigurationError` on an empty secret, which
    would otherwise leak a different, less specific exception type than the
    one every other misconfiguration in this codebase raises.
    """
    if not config.huey_serializer_secret:
        raise SerializerMisconfigured(
            "AI_BRAIN_HUEY_SECRET is not set; refusing to construct a job queue "
            "with an unauthenticated or empty-secret serializer (ADR-0002)."
        )
    ensure_private_dir(config.data_dir)
    instance = SqliteHuey(
        name="ai-brain",
        filename=str(config.huey_db_path),
        serializer=SignedSerializer(secret=config.huey_serializer_secret),
    )
    assert_safe_job_serializer(instance)
    return instance


_config = load_config()
huey = build_huey(_config)


_qdrant_client: QdrantClient | None = None


def _get_qdrant_client(config: AIBrainConfig) -> QdrantClient:
    """Lazy, process-lifetime singleton -- constructed (and `ensure_collection`
    run) on first use, not at import time, matching `ai_brain.indexing.
    embedding`'s lazy-model pattern. Requires a reachable Qdrant server
    (`AI_BRAIN_QDRANT_URL`, default matching ADR-0006's binding); currently
    blocked in this development environment (design doc §0/§8) -- calling
    this in that environment fails cleanly with a connection error, which is
    the correct, expected behavior until Docker access is restored.
    """
    global _qdrant_client
    if _qdrant_client is None:
        client = QdrantClient(url=config.qdrant_url)
        ensure_collection(client, huey)
        _qdrant_client = client
    return _qdrant_client


def _require_vault_root(config: AIBrainConfig) -> VaultRoot:
    if config.vault_root is None:
        raise RuntimeError(
            "AI_BRAIN_VAULT_DIR is not set -- the worker cannot ingest without a configured vault"
        )
    return VaultRoot.initialize(config.vault_root)


def _on_settle(path: str) -> None:
    """`VaultWatcher`'s injected settle callback (design doc §2.3). Appends
    `fs.path_changed` (the root of a new correlation chain, per
    docs/EVENT_MODEL.md §3.2) and enqueues `ingest_note_task` -- synchronous,
    no asyncio bridge at this layer, per ADR-0009 decision 3: Huey's SQLite
    enqueue is just a parameterized DB write.
    """

    async def _append_and_get_ids() -> tuple[str, str]:
        correlation_id = str(uuid4())
        async with open_connection(_config.db_path) as conn:
            event_id = await events_repo.append_event(
                conn,
                event_type="fs.path_changed",
                source="filesystem_watcher",
                correlation_id=correlation_id,
                causation_id=None,
                payload={"path": path, "raw_event_kinds": []},
            )
        return correlation_id, event_id

    try:
        correlation_id, causation_id = asyncio.run(_append_and_get_ids())
    except Exception:
        logger.exception("failed to record fs.path_changed for %s", path)
        return
    ingest_note_task(path, correlation_id, causation_id)


@huey.task(retries=3, retry_delay=10)  # type: ignore[untyped-decorator]  # huey ships no py.typed
def ingest_note_task(path: str, correlation_id: str, causation_id: str | None) -> None:
    async def _run() -> tuple[str, int | None]:
        vault_root = _require_vault_root(_config)
        async with open_connection(_config.db_path) as conn:
            result = await ingest_note(
                conn,
                huey,
                vault_root,
                path,
                correlation_id=correlation_id,
                causation_id=causation_id,
                block_on_high_confidence_secrets=_config.secret_scanner_block_on_high_confidence,
            )
            return result.outcome, result.note_id

    outcome, note_id = asyncio.run(_run())
    # Chained via a normal (queued, not call_local) invocation -- design doc
    # §2.6. Enqueueing is a cheap SQLite write; the chained job's own
    # execution (and its Qdrant dependency) happens independently, whenever
    # the consumer next picks it up.
    if outcome in {"created", "updated"} and note_id is not None:
        index_note_task(note_id, correlation_id, causation_id)


@huey.task(retries=3, retry_delay=10)  # type: ignore[untyped-decorator]  # huey ships no py.typed
def index_note_task(note_id: int, correlation_id: str, causation_id: str | None) -> None:
    async def _run() -> None:
        vault_root = _require_vault_root(_config)
        qdrant_client = _get_qdrant_client(_config)
        async with open_connection(_config.db_path) as conn:
            await index_note(
                conn,
                qdrant_client,
                vault_root,
                note_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

    asyncio.run(_run())


def _try_get_qdrant_client(config: AIBrainConfig) -> QdrantClient | None:
    """Best-effort: bootstrap/reconcile must remain usable for metadata-only
    ingestion when Qdrant isn't reachable (this development environment's
    current Docker-access blocker, design doc §0/§8, included) -- a
    connection failure here degrades to "skip indexing this run" rather than
    aborting the whole CLI command."""
    try:
        return _get_qdrant_client(config)
    except Exception:
        logger.warning(
            "Qdrant unreachable at %s -- proceeding with metadata-only ingestion, "
            "no indexing this run.",
            config.qdrant_url,
        )
        return None


def run_bootstrap(config: AIBrainConfig | None = None) -> BootstrapSummary:
    """Synchronous entry point for `ai-brain ingest bootstrap` -- calls
    `bootstrap_ingest_vault` directly rather than through the task queue
    (see `ai_brain.vault.bootstrap`'s own module docstring for why)."""
    active_config = config or _config
    vault_root = _require_vault_root(active_config)
    qdrant_client = _try_get_qdrant_client(active_config)

    async def _run() -> BootstrapSummary:
        async with open_connection(active_config.db_path) as conn:
            return await bootstrap_ingest_vault(
                conn,
                huey,
                vault_root,
                block_on_high_confidence_secrets=active_config.secret_scanner_block_on_high_confidence,
                qdrant_client=qdrant_client,
            )

    return asyncio.run(_run())


def run_index_bootstrap(config: AIBrainConfig | None = None) -> IndexBootstrapSummary:
    """Synchronous entry point for `ai-brain index bootstrap` (design doc
    §6) -- unlike `run_bootstrap`/`run_reconcile`, this genuinely requires
    Qdrant to be reachable (there is no meaningful "metadata-only" mode for
    an indexing-only command), so a connection failure here propagates
    rather than degrading silently."""
    active_config = config or _config
    vault_root = _require_vault_root(active_config)
    qdrant_client = _get_qdrant_client(active_config)

    async def _run() -> IndexBootstrapSummary:
        async with open_connection(active_config.db_path) as conn:
            return await index_bootstrap(
                conn, qdrant_client, vault_root, correlation_id=str(uuid4())
            )

    return asyncio.run(_run())


def run_reconcile(config: AIBrainConfig | None = None) -> ReconciliationSummary:
    """Synchronous entry point for `ai-brain ingest reconcile` (an on-demand
    pass outside the periodic schedule below)."""
    active_config = config or _config
    vault_root = _require_vault_root(active_config)
    qdrant_client = _try_get_qdrant_client(active_config)

    async def _run() -> ReconciliationSummary:
        async with open_connection(active_config.db_path) as conn:
            return await reconcile_vault(
                conn,
                huey,
                vault_root,
                block_on_high_confidence_secrets=active_config.secret_scanner_block_on_high_confidence,
                qdrant_client=qdrant_client,
            )

    return asyncio.run(_run())


@huey.periodic_task(crontab(minute="0"))  # type: ignore[untyped-decorator]  # huey ships no py.typed
def reconcile_vault_task() -> None:  # pragma: no cover -- exercised via run_reconcile in tests
    run_reconcile(_config)


def start_watcher(config: AIBrainConfig | None = None) -> VaultWatcher:
    """Start the filesystem watcher and run one reconciliation pass inline
    before the periodic schedule takes over (design doc §2.7's "both
    startup and periodic" resolution). Called from `_worker_startup` below,
    not from `huey_consumer` itself (which only knows about `@huey.task`-
    decorated functions)."""
    active_config = config or _config
    vault_root = _require_vault_root(active_config)

    watcher = VaultWatcher(str(vault_root.path), on_settle=_on_settle)
    watcher.start()

    try:
        run_reconcile(active_config)
    except Exception:
        logger.exception("startup reconciliation pass failed")

    return watcher


_watcher: VaultWatcher | None = None


@huey.on_startup()  # type: ignore[untyped-decorator]  # huey ships no py.typed
def _worker_startup() -> None:
    global _watcher
    _watcher = start_watcher(_config)
