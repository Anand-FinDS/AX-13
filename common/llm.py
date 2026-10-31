"""LLM and embedding calls through LiteLLM. Model ids and keys come from common/settings.py.

complete(messages, role="reason") is cached on disk by prompt hash (model + messages + options),
so reruns are deterministic and free. Roles map to AX13_MODEL_REASON / _EXTRACT / _FALLBACK;
if the role's model fails, the call is retried once on AX13_MODEL_FALLBACK.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Literal

import litellm

from common.settings import get_settings

litellm.suppress_debug_info = True
log = logging.getLogger("ax13.llm")
Role = Literal["reason", "extract", "fallback"]


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


def model_for(role: Role) -> str:
    s = get_settings()
    model = {"reason": s.ax13_model_reason, "extract": s.ax13_model_extract, "fallback": s.ax13_model_fallback}[role]
    if not model:
        raise RuntimeError(f"AX13_MODEL_{role.upper()} is not set in .env")
    return model


def _call(model: str, messages: list[dict], json_mode: bool) -> str:
    cache_dir = get_settings().llm_cache_dir
    key = hashlib.sha256(json.dumps({"model": model, "messages": messages, "json": json_mode, "temperature": 0},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = cache_dir / f"{key[:2]}" / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["content"]
    resp = litellm.completion(model=model, messages=messages, temperature=0, api_key=_key_for(model),
                              response_format={"type": "json_object"} if json_mode else None)
    content = resp.choices[0].message.content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "messages": messages, "content": content}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return content


def complete(messages: list[dict], role: Role = "reason", json_mode: bool = False) -> str | dict:
    """Chat completion (temperature 0). With json_mode the parsed object is returned."""
    model = model_for(role)
    try:
        content = _call(model, messages, json_mode)
    except Exception as e:
        fb = model_for("fallback")
        if fb == model:
            raise
        log.warning("%s failed (%s); retrying on %s", model, type(e).__name__, fb)
        content = _call(fb, messages, json_mode)
    return json.loads(content) if json_mode else content
