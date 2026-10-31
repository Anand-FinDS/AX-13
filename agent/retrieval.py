"""AlignForce 2.0 retrieval over the four policy docs, one chunk per numbered section.

    python -m agent.retrieval --build                 # chunk, embed, (re)load Qdrant collection ax13_docs
    python -m agent.retrieval "Soundbox offline"      # quick query

retrieve(query, doc=None, k=3, cause=None) -> [{ref, doc, section_id, title, text, score}]
- Dense search in local Qdrant; if Qdrant is unavailable, the same function falls back to
  in-memory cosine similarity over cached section embeddings.
- Safety net: with a known cause, the expected section (EXPECTED_SECTION) must come first; if
  the top hit disagrees, the expected section is used and the mismatch is logged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import uuid

from common.llm import embed
from common.settings import REPO_ROOT, get_settings

log = logging.getLogger("ax13.retrieval")
COLLECTION = "ax13_docs"
DOCS = {
    "Retention playbook": "retention_playbook.md",
    "Product catalogue": "product_catalogue.md",
    "Compliance rules": "compliance_rules.md",
    "Autonomy policy": "autonomy_policy.md",
}
# Scorer cause -> section the action must cite (safety net for the demo).
EXPECTED_SECTION = {
    "device": "Retention playbook §3.1",
    "network": "Retention playbook §4.2",
    "charge": "Retention playbook §6.3",
    "fee": "Retention playbook §6.2",
    "competitor": "Retention playbook §8.2",
    "closed": "Retention playbook §9.1",
    "threshold": "Retention playbook §6.2",   # honest cost comparison with the merchant's own numbers
    "activation": "Product catalogue §1.2",
}
# Cause -> section the compliance check on the drafted message cites.
COMPLIANCE_SECTION = {
    "threshold": "Compliance rules §3.2",     # never link credit to the ₹1 lakh threshold
}


def citations(cause: str | None) -> dict:
    """Sections a case must cite: the action's playbook section and, where set, the compliance check's."""
    return {"action": EXPECTED_SECTION.get(cause), "compliance": COMPLIANCE_SECTION.get(cause)}
MISMATCHES: list[dict] = []   # safety-net corrections in this process (for the eval / traces)
_CACHE = get_settings().data_out_dir / "doc_embeddings.json"


# ---------------------------------------------------------------- chunking

def chunk_docs() -> list[dict]:
    """One chunk per numbered section: '**N.M ...' sub-sections where they exist, else '## N.'."""
    chunks = []
    for doc, fname in DOCS.items():
        text = (REPO_ROOT / "docs" / fname).read_text(encoding="utf-8")
        for sec in re.split(r"^(?=## \d+\.)", text, flags=re.MULTILINE)[1:]:
            head, _, body = sec.partition("\n")
            num, title = re.match(r"## (\d+)\.\s*(.+)", head).groups()
            parts = re.split(r"^(?=\*\*\d+\.\d+ )", body, flags=re.MULTILINE)
            intro, subs = parts[0].strip(), parts[1:]
            if not subs:
                chunks.append(_chunk(doc, num, title, body.strip()))
                continue
            for sub in subs:
                m = re.match(r"\*\*(\d+\.\d+) ([^*]+?)\.?\*\*", sub)
                sub_body = sub.strip() if not intro else f"{intro}\n\n{sub.strip()}"
                chunks.append(_chunk(doc, m.group(1), f"{title}: {m.group(2).strip()}", sub_body))
    return chunks


def _chunk(doc: str, sid: str, title: str, body: str) -> dict:
    body = re.sub(r"\n-{3,}\n", "\n", body).strip()
    return {"ref": f"{doc} §{sid}", "doc": doc, "section_id": sid, "title": title,
            "text": f"{doc} §{sid} {title}\n\n{body}"}


# ---------------------------------------------------------------- embeddings (cached)

def _hash(text: str) -> str:
    return hashlib.sha256((get_settings().ax13_embed_model + text).encode()).hexdigest()[:16]


def _embedded_chunks() -> list[dict]:
    chunks = chunk_docs()
    cache = json.loads(_CACHE.read_text(encoding="utf-8")) if _CACHE.exists() else {}
    todo = [c for c in chunks if _hash(c["text"]) not in cache]
    if todo:
        for c, v in zip(todo, embed([c["text"] for c in todo])):
            cache[_hash(c["text"])] = v
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return [{**c, "vector": cache[_hash(c["text"])]} for c in chunks]


# ---------------------------------------------------------------- Qdrant

def _client():
    from qdrant_client import QdrantClient
    s = get_settings()
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None,
                        timeout=5, check_compatibility=False)


def build_index() -> int:
    from qdrant_client.models import Distance, PointStruct, VectorParams
    chunks = _embedded_chunks()
    qc = _client()
    if qc.collection_exists(COLLECTION):
        qc.delete_collection(COLLECTION)
    qc.create_collection(COLLECTION, vectors_config=VectorParams(size=len(chunks[0]["vector"]), distance=Distance.COSINE))
    qc.upsert(COLLECTION, points=[
        PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, c["ref"])), vector=c["vector"],
                    payload={k: c[k] for k in ("ref", "doc", "section_id", "title", "text")}) for c in chunks])
    return len(chunks)


def _search_qdrant(qv: list[float], doc: str | None, k: int) -> list[dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    flt = Filter(must=[FieldCondition(key="doc", match=MatchValue(value=doc))]) if doc else None
    res = _client().query_points(COLLECTION, query=qv, query_filter=flt, limit=k, with_payload=True)
    return [{**p.payload, "score": round(p.score, 4), "backend": "qdrant"} for p in res.points]


def _search_memory(qv: list[float], doc: str | None, k: int) -> list[dict]:
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    pool = [c for c in _embedded_chunks() if doc is None or c["doc"] == doc]
    ranked = sorted(pool, key=lambda c: cos(qv, c["vector"]), reverse=True)[:k]
    return [{**{x: c[x] for x in ("ref", "doc", "section_id", "title", "text")},
             "score": round(cos(qv, c["vector"]), 4), "backend": "memory"} for c in ranked]


def section(ref: str) -> dict:
    return next({**c, "score": None} for c in chunk_docs() if c["ref"] == ref)


# ---------------------------------------------------------------- public API

def retrieve(query: str, doc: str | None = None, k: int = 3, cause: str | None = None) -> list[dict]:
    if doc is not None and doc not in DOCS:
        raise ValueError(f"doc must be one of {list(DOCS)}")
    qv = embed([query])[0]
    try:
        hits = _search_qdrant(qv, doc, k)
    except Exception as e:  # noqa: BLE001  any Qdrant failure (down, missing collection): answer from memory
        log.warning("Qdrant unavailable (%s); using in-memory search", type(e).__name__)
        hits = _search_memory(qv, doc, k)
    expected = EXPECTED_SECTION.get(cause) if cause else None
    if expected and (not hits or hits[0]["ref"] != expected):
        MISMATCHES.append({"query": query, "cause": cause, "top1": hits[0]["ref"] if hits else None, "used": expected})
        log.warning("Retrieval top-1 %s disagrees with expected %s for cause %r; using expected",
                    hits[0]["ref"] if hits else None, expected, cause)
        hits = [{**section(expected), "safety_net": True}] + [h for h in hits if h["ref"] != expected][:k - 1]
    return hits


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--doc")
    ap.add_argument("--cause")
    args = ap.parse_args()
    if args.build:
        print(f"Indexed {build_index()} sections into Qdrant collection {COLLECTION!r}")
    if args.query:
        for h in retrieve(args.query, args.doc, cause=args.cause):
            print(f"{h['score']}  {h['ref']}  {h['title']}" + ("  [safety net]" if h.get("safety_net") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
