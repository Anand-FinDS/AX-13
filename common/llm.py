"""LLM and embedding calls through LiteLLM, cache first. Model ids and keys come from common/settings.py.

Two modes (AX13_LLM_MODE, switchable at runtime with set_mode):
- replay (default): every call is served from the on-disk cache. A miss raises LLMNotCached and
  makes no API call; callers fall back (template, rules-only gate) and label the step "not cached".
- live: a miss calls the API and stores the answer.

Cache keys hash the model and the prompt only (messages for completions, text for embeddings).
Prompts never carry timestamps, run ids or wall-clock values, so the same simulated day replays
from the cache exactly. STATS counts hits, misses and API calls in this process.
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
Mode = Literal["replay", "live"]

STATS = {"hits": 0, "misses": 0, "api_calls": 0, "embed_hits": 0, "embed_misses": 0, "embed_api_calls": 0}
_mode: Mode = get_settings().ax13_llm_mode


class LLMNotCached(RuntimeError):
    """Replay mode and the prompt is not in the cache: no API call was made."""


def set_mode(mode: Mode) -> None:
    global _mode
    if mode not in ("replay", "live"):
        raise ValueError("mode must be 'replay' or 'live'")
    _mode = mode


def get_mode() -> Mode:
    return _mode


def _key_for(model: str) -> str | None:
    s = get_settings()
    key = {"openai": s.openai_api_key, "groq": s.groq_api_key}.get(model.split("/", 1)[0])
    return key.get_secret_value() if key else None


def model_for(role: Role) -> str:
    s = get_settings()
    model = {"reason": s.ax13_model_reason, "extract": s.ax13_model_extract, "fallback": s.ax13_model_fallback}[role]
    if not model:
        raise RuntimeError(f"AX13_MODEL_{role.upper()} is not set in .env")
    return model


def cache_key(model: str, messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps({"model": model, "messages": messages}, ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


def _path(key: str):
    return get_settings().llm_cache_dir / key[:2] / f"{key}.json"


def is_cached(messages: list[dict], role: Role = "reason") -> bool:
    return _path(cache_key(model_for(role), messages)).exists()


def _call(model: str, messages: list[dict], json_mode: bool) -> str:
    path = _path(cache_key(model, messages))
    if path.exists():
        STATS["hits"] += 1
        return json.loads(path.read_text(encoding="utf-8"))["content"]
    STATS["misses"] += 1
    if _mode != "live":
        raise LLMNotCached(f"{model}: prompt not in cache (replay mode, no API call)")
    STATS["api_calls"] += 1
    resp = litellm.completion(model=model, messages=messages, temperature=0, api_key=_key_for(model),
                              response_format={"type": "json_object"} if json_mode else None)
    content = resp.choices[0].message.content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "messages": messages, "content": content}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return content


def complete(messages: list[dict], role: Role = "reason", json_mode: bool = False) -> str | dict:
    """Chat completion (temperature 0). With json_mode the parsed object is returned.
    In live mode a failing model is retried once on AX13_MODEL_FALLBACK."""
    model = model_for(role)
    try:
        content = _call(model, messages, json_mode)
    except LLMNotCached:
        raise
    except Exception as e:  # rate limit, outage, bad id: one retry on the fallback model
        fb = model_for("fallback")
        if fb == model:
            raise
        log.warning("%s failed (%s); retrying on %s", model, type(e).__name__, fb)
        content = _call(fb, messages, json_mode)
    return json.loads(content) if json_mode else content


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed texts with AX13_EMBED_MODEL, cached per text. In replay a miss raises LLMNotCached."""
    model = model or get_settings().ax13_embed_model
    root = get_settings().data_out_dir / "embed_cache"
    paths = [root / f"{hashlib.sha256((model + t).encode()).hexdigest()}.json" for t in texts]
    todo = [i for i, p in enumerate(paths) if not p.exists()]
    STATS["embed_hits"] += len(texts) - len(todo)
    STATS["embed_misses"] += len(todo)
    if todo and _mode != "live":
        raise LLMNotCached(f"{len(todo)} embedding(s) not in cache (replay mode, no API call)")
    for i in range(0, len(todo), 64):
        batch = todo[i:i + 64]
        resp = litellm.embedding(model=model, input=[texts[j] for j in batch], api_key=_key_for(model))
        STATS["embed_api_calls"] += 1
        root.mkdir(parents=True, exist_ok=True)
        for j, d in zip(batch, resp.data):
            paths[j].write_text(json.dumps(d["embedding"]), encoding="utf-8")
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]
