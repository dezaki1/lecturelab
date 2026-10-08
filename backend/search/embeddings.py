"""Text embeddings (bge-m3 via Ollama), cached in the ``embedding_cache`` table.

bge-m3 is multilingual, so English questions find German lecture passages and vice versa.
The ``mock`` provider (tests) hashes words into a sparse vector: no model, deterministic, and
texts that share words still come out similar.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import struct

import httpx2 as httpx
from sqlmodel import col, select

from backend.core.config import get_settings
from backend.core.db import session_scope
from backend.core.models import EmbeddingCache

log = logging.getLogger(__name__)
BATCH = 32


class EmbeddingError(Exception):
    pass


def available() -> bool:
    return get_settings().search.embed_provider != "none"


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _mock_embed(texts: list[str], dim: int) -> list[list[float]]:
    out = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"\w+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    cfg = get_settings()
    url = f"{cfg.llm.ollama_url.rstrip('/')}/api/embed"
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        body = {
            "model": cfg.search.embed_model,
            "input": texts[i : i + BATCH],
            "keep_alive": cfg.search.embed_keep_alive,
        }
        try:
            r = httpx.post(url, json=body, timeout=300)
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama is not reachable at {cfg.llm.ollama_url}") from e
        if r.status_code == 404:
            name = cfg.search.embed_model
            raise EmbeddingError(f"Embedding model {name!r} missing: ollama pull {name}")
        if r.status_code >= 400:
            raise EmbeddingError(f"Ollama embedding error {r.status_code}: {r.text[:200]}")
        out.extend(r.json()["embeddings"])
    return out


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts, reusing cached vectors. Raises EmbeddingError if the backend is down."""
    cfg = get_settings().search
    if cfg.embed_provider == "none":
        raise EmbeddingError("Semantic search is disabled (search.embed_provider = none)")
    model = f"{cfg.embed_provider}:{cfg.embed_model}"
    keys = [_key(model, t) for t in texts]
    with session_scope() as s:
        rows = s.exec(select(EmbeddingCache).where(col(EmbeddingCache.key).in_(set(keys)))).all()
        cached = {r.key: unpack(r.vector) for r in rows}
    todo = [i for i, k in enumerate(keys) if k not in cached]
    if todo:
        new_texts = [texts[i] for i in todo]
        if cfg.embed_provider == "mock":
            vectors = _mock_embed(new_texts, cfg.embed_dim)
        else:
            vectors = _ollama_embed(new_texts)
        with session_scope() as s:
            for i, vec in zip(todo, vectors, strict=True):
                if keys[i] not in cached:
                    s.merge(
                        EmbeddingCache(key=keys[i], model=model, dim=len(vec), vector=pack(vec))
                    )
                cached[keys[i]] = vec
            s.commit()
        log.info("Embedded %d texts (%d from cache)", len(todo), len(texts) - len(todo))
    return [cached[k] for k in keys]
