"""AX-13 connectivity smoke test.

Usage:
    python scripts/check_connections.py               # check every endpoint
    python scripts/check_connections.py --list-models # also print model ids per provider
    python scripts/check_connections.py --llm-call    # also make one tiny call per model role

Exit code 1 if any required check fails. Optional checks (LangSmith, HF) never fail the run.
"""
from __future__ import annotations

import argparse
import os
import socket
import time
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()
RESULTS: list[tuple[str, bool, str, bool]] = []


def env(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    return val if val else default


def record(name: str, ok: bool, detail: str, required: bool = True) -> None:
    RESULTS.append((name, ok, detail, required))


def check_env() -> None:
    keys = ["OPENAI_API_KEY", "GROQ_API_KEY", "POSTGRES_HOST", "QDRANT_URL",
            "OTEL_EXPORTER_OTLP_ENDPOINT", "AX13_MODEL_REASON", "AX13_MODEL_EXTRACT"]
    missing = [k for k in keys if not env(k)]
    placeholders = [k for k in keys if (env(k) or "").find("<pin") >= 0]
    problems = missing + [f"{k} (placeholder)" for k in placeholders]
    record("env vars", not problems, "all set" if not problems else ", ".join(problems))


def check_postgres() -> None:
    try:
        import psycopg
        dsn = (f"host={env('POSTGRES_HOST')} port={env('POSTGRES_PORT', '5432')} "
               f"dbname={env('POSTGRES_DB')} user={env('POSTGRES_USER')} "
               f"password={env('POSTGRES_PASSWORD')}")
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            version = conn.execute("select version()").fetchone()[0].split(",")[0]
            has_schema = conn.execute(
                "select 1 from information_schema.schemata where schema_name='ax13'").fetchone()
        record("postgres", bool(has_schema),
               version + (" · schema ax13 ok" if has_schema else " · schema ax13 MISSING"))
    except Exception as e:
        record("postgres", False, str(e)[:200])


def check_qdrant() -> None:
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=env("QDRANT_URL"), api_key=env("QDRANT_API_KEY"), timeout=10)
        n = len(client.get_collections().collections)
        where = "cloud" if "cloud.qdrant.io" in (env("QDRANT_URL") or "") else "local"
        record("qdrant", True, f"{where} · {n} collections")
    except Exception as e:
        msg = str(e)[:200]
        if "Application Control" in msg:
            msg = "Windows blocked grpcio DLL (Smart App Control / policy) — Qdrant service itself may be fine"
        record("qdrant", False, msg)


def check_provider(name: str, base_url: str, key_env: str, show: bool) -> None:
    import httpx
    key = env(key_env)
    if not key:
        record(name, False, f"{key_env} not set")
        return
    try:
        r = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json()["data"])
        record(name, True, f"key valid · {len(ids)} models visible")
        if show:
            print(f"\n{name} models:\n  " + "\n  ".join(ids))
    except Exception as e:
        record(name, False, str(e)[:200])


def check_llm_roles() -> None:
    import litellm
    litellm.suppress_debug_info = True
    for role in ("AX13_MODEL_REASON", "AX13_MODEL_EXTRACT", "AX13_MODEL_FALLBACK"):
        model = env(role)
        if not model or "<pin" in model:
            record(role, False, "not pinned yet")
            continue
        try:
            t0 = time.time()
            litellm.completion(model=model, max_tokens=512,  # reasoning models spend tokens before answering
                               messages=[{"role": "user", "content": "Reply with OK"}])
            record(role, True, f"{model} · {time.time() - t0:.1f}s")
        except Exception as e:
            record(role, False, f"{model}: {str(e)[:160]}")


def check_otel() -> None:
    endpoint = env("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
    parsed = urlparse(endpoint)
    try:
        socket.create_connection((parsed.hostname, parsed.port or 4318), timeout=3).close()
    except OSError as e:
        record("otel collector", False, f"{endpoint} unreachable: {e}")
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "ax13-smoke"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
        with provider.get_tracer("ax13.smoke").start_as_current_span("connection-check") as span:
            span.set_attribute("ax13.check", "otel")
        provider.force_flush()
        provider.shutdown()
        record("otel collector", True, "test span sent → Grafana › Explore › Tempo › service ax13-smoke")
    except Exception as e:
        record("otel collector", False, str(e)[:200])


def check_grafana() -> None:
    import httpx
    url = env("GRAFANA_URL", "http://localhost:3000")
    try:
        r = httpx.get(f"{url}/api/health", timeout=5)
        r.raise_for_status()
        record("grafana", True, f"{url} · db {r.json().get('database')}")
    except Exception as e:
        record("grafana", False, str(e)[:200])


def check_langsmith() -> None:
    if not env("LANGSMITH_API_KEY"):
        record("langsmith", True, "skipped (no key)", required=False)
        return
    try:
        from langsmith import Client
        next(iter(Client().list_projects(limit=1)), None)
        state = "tracing ON" if env("LANGSMITH_TRACING") == "true" else "tracing off"
        record("langsmith", True, f"key valid · {state}", required=False)
    except Exception as e:
        record("langsmith", False, str(e)[:200], required=False)


def check_hf() -> None:
    if not env("HF_TOKEN"):
        record("huggingface", True, "skipped (needed at Phase 4)", required=False)
        return
    try:
        from huggingface_hub import whoami
        record("huggingface", True, f"user {whoami(token=env('HF_TOKEN'))['name']}", required=False)
    except Exception as e:
        record("huggingface", False, str(e)[:200], required=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--llm-call", action="store_true")
    args = ap.parse_args()

    check_env()
    check_postgres()
    check_qdrant()
    check_provider("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", args.list_models)
    check_provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", args.list_models)
    if args.llm_call:
        check_llm_roles()
    check_otel()
    check_grafana()
    check_langsmith()
    check_hf()

    print(f"\n{'check':<20}{'status':<8}detail")
    print("-" * 90)
    for name, ok, detail, required in RESULTS:
        status = "OK" if ok else ("FAIL" if required else "warn")
        print(f"{name:<20}{status:<8}{detail}")
    failed = [r for r in RESULTS if not r[1] and r[3]]
    print(f"\n{'All required checks passed.' if not failed else f'{len(failed)} required check(s) failed.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())