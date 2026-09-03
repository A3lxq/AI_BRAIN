"""Dense (BGE-M3) and sparse (miniCOIL) embedding generation.

See `docs/design/indexing-pipeline.md` §2.3/§3 and ADR-0008 for the accepted
model choices. Both models are loaded lazily, once per process, on first use
(not at import time) since not every process importing this module needs
them (e.g. the CLI's `--help`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastembed import SparseTextEmbedding
from sentence_transformers import SentenceTransformer

__all__ = [
    "SparseVector",
    "EMBEDDING_MODEL_VERSION",
    "embed_dense",
    "embed_sparse",
]

# Resolved via `huggingface_hub.HfApi().model_info("BAAI/bge-m3").sha`
# (cross-checked against `curl https://huggingface.co/api/models/BAAI/bge-m3`
# directly) on 2026-09-02. Pinned per SECURITY_MODEL.md P1 item 15 -- never
# "main". Re-resolve the same way on any future BGE-M3 version bump.
_BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"

EMBEDDING_MODEL_VERSION = f"bge-m3@{_BGE_M3_REVISION[:12]}"

# `Qdrant/minicoil-v1`, loaded via fastembed's `SparseTextEmbedding`.
#
# No revision-pinning mechanism exists for this load, verified against the
# installed fastembed 0.8.0 source (not assumed): `SparseTextEmbedding.
# __init__`/`MiniCOIL.__init__` (fastembed/sparse/sparse_text_embedding.py,
# fastembed/sparse/minicoil.py) accept no `revision` parameter, and
# `MiniCOIL.__init__` calls `self.download_model(..., specific_model_path=...)`
# without forwarding `**kwargs` at all -- so even smuggling a `revision=`
# kwarg through the constructor never reaches the download path. Internally,
# `ModelManagement.download_model` -> `download_files_from_huggingface`
# (fastembed/common/model_management.py) always resolves
# `model_info(hf_source_repo).sha`, i.e. whatever is current HEAD at call
# time, before calling `huggingface_hub.snapshot_download`. The only
# available override is `specific_model_path`, which points at an
# already-downloaded local directory the caller must populate and verify
# themselves -- not a first-class pin. This is a real gap relative to
# sentence-transformers' `revision=` kwarg, not a fake pin: SECURITY_MODEL.md
# P1 item 15 is unresolved for the sparse leg until fastembed adds one, or
# until ATHENA AI-BRAIN builds its own pre-downloaded-snapshot wrapper (out of scope
# here; flagged for a follow-up ADR/design note, not silently worked around).
_MINICOIL_MODEL_NAME = "Qdrant/minicoil-v1"


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


_dense_model: SentenceTransformer | None = None
_sparse_model: SparseTextEmbedding | None = None


def _get_dense_model() -> SentenceTransformer:
    global _dense_model
    if _dense_model is None:
        _dense_model = SentenceTransformer("BAAI/bge-m3", revision=_BGE_M3_REVISION)
    return _dense_model


def _get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name=_MINICOIL_MODEL_NAME)
    return _sparse_model


def embed_dense(texts: list[str]) -> list[list[float]]:
    model = _get_dense_model()
    embeddings = model.encode(texts)
    return cast(list[list[float]], embeddings.tolist())


def embed_sparse(texts: list[str]) -> list[SparseVector]:
    model = _get_sparse_model()
    return [
        SparseVector(
            indices=cast(list[int], embedding.indices.tolist()),
            values=cast(list[float], embedding.values.tolist()),
        )
        for embedding in model.embed(texts)
    ]
