"""OpenLLMetry (Traceloop SDK) start-up: LLM, MCP and LangGraph spans to the local OTLP/HTTP
collector (grafana/otel-lgtm). Call init_tracing() once, before the first LLM call."""
from __future__ import annotations

import logging
import os

from common.settings import get_settings

log = logging.getLogger("ax13.telemetry")
_done = False


def init_tracing() -> bool:
    global _done
    if _done:
        return True
    s = get_settings()
    # The SDK also reads these from the environment; set them from settings so .env is the one source.
    os.environ["TRACELOOP_BASE_URL"] = s.traceloop_base_url
    os.environ["TRACELOOP_TELEMETRY"] = str(s.traceloop_telemetry).lower()
    # Known-harmless noise: SQLAlchemy 2.1 is outside the instrumentor's range (we do not use it at
    # runtime), and the LangChain handler has no on_interrupt hook (fired at every approval pause).
    logging.getLogger("opentelemetry.instrumentation.instrumentor").setLevel(logging.CRITICAL)
    logging.getLogger("langchain_core.callbacks.manager").setLevel(logging.ERROR)
    try:
        from traceloop.sdk import Traceloop
        Traceloop.init(app_name=s.otel_service_name,
                       api_endpoint=s.traceloop_base_url, telemetry_enabled=s.traceloop_telemetry,
                       disable_batch=False)
        _done = True
    except Exception as e:  # noqa: BLE001  tracing must never stop the agent
        log.warning("OpenLLMetry not started (%s: %s)", type(e).__name__, e)
    return _done
