"""LLM and embedding calls through LiteLLM. Model ids and keys come from common/settings.py."""
from __future__ import annotations

import litellm

from common.settings import get_settings

litellm.suppress_debug_info = True


def _key_for(model: str) -> str | None:
    s = get_settings()
    key = {"openai": s.openai_api_key, "groq": s.groq_api_key}.get(model.split("/", 1)[0])
    return key.get_secret_value() if key else None


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed texts with AX13_EMBED_MODEL (batched)."""
    model = model or get_settings().ax13_embed_model
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        resp = litellm.embedding(model=model, input=texts[i:i + 64], api_key=_key_for(model))
        out += [d["embedding"] for d in resp.data]
    return out
