"""GAAG semantic embedding.

两种嵌入模式:
- **FeatureEmbedder**（确定性，离线可用）: 将 card 描述文本 hash 到固定维向量，
  无需任何外部服务，用于本地检索/原型。
- **LLM embedder**（可选）: 通过 ``GEONEXUS_LLM_API_KEY`` 调用 OpenAI 兼容
  embeddings 接口。默认关闭，未配置时回退到 FeatureEmbedder。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

from ..geocard import GeoCard

logger = logging.getLogger(__name__)

_DIM = 64  # 默认向量维度（无外部依赖的确定性嵌入）


class FeatureEmbedder:
    """Deterministic bag-of-words style embedding.

    Maps each distinct token in the text to a fixed pseudorandom direction
    (signed ±1 pattern derived from the token hash) and sums them up.
    Cosine similarity of such embeddings is a cheap lexical similarity —
    adequate for prototyping the GAAG semantic gate without network.
    """

    dim: int

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim

    def _token_unit(self, token: str) -> list[float]:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        out = [0.0] * self.dim
        for i in range(self.dim):
            # Deterministic pseudo-random ±1 from the digest bytes.
            byte = digest[i % len(digest)]
            out[i] = 1.0 if (byte & (1 << (i % 8))) else -1.0
        return out

    def embed_text(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        if not tokens:
            return [0.0] * self.dim
        vec = [0.0] * self.dim
        for token in tokens:
            unit = self._token_unit(token)
            for i in range(self.dim):
                vec[i] += unit[i]
        norm = (sum(v * v for v in vec) ** 0.5) or 1.0
        return [v / norm for v in vec]

    def embed_card(self, card: GeoCard) -> list[float]:
        parts = [
            card.id,
            card.name,
            card.description,
            " ".join(card.tags),
            " ".join(card.capability_names()),
            " ".join(card.band_names()),
        ]
        if card.spatial:
            parts.append(card.spatial.crs or "")
        return self.embed_text(" ".join(x for x in parts if x))


def embed_text(text: str) -> list[float]:
    """Embed arbitrary text deterministically (no LLM required)."""
    return FeatureEmbedder().embed_text(text)


def embed_card(card: GeoCard) -> list[float]:
    """Embed a GeoCard deterministically."""
    return FeatureEmbedder().embed_card(card)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (used by the semantic gate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)


def llm_embedding_available() -> bool:
    """True when an LLM API key is configured (optional semantic embedding)."""
    return bool(os.environ.get("GEONEXUS_LLM_API_KEY"))