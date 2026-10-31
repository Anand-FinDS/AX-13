"""Retrieval hit@1 / hit@3 on 12 policy questions, via Qdrant and via the in-memory fallback.

    python -m eval.retrieval_eval

Pure retrieval (no cause, so no safety net). Strict = the intended section; lenient also accepts
a listed alternative. The safety net is checked separately, one generic query per cause.
"""
from __future__ import annotations

import sys

from agent import retrieval
from common.llm import embed

# (query, acceptable sections: the first is the intended answer)
QUERIES = [
    ("Soundbox offline, hardware fault", ["Retention playbook §3.1"]),
    ("loan offer disclosure", ["Compliance rules §1"]),
    ("payments failing only during rush hours, device hardware is fine",
     ["Retention playbook §4.2", "Retention playbook §4.1"]),
    ("customer was debited but the merchant never got the payment, how long until reversal",
     ["Retention playbook §5.2"]),
    ("maintenance charge deducted without telling the merchant, should we refund",
     ["Retention playbook §6.3", "Retention playbook §6.1"]),
    ("estimate the merchant's monthly UPI fee under the proposed framework", ["Retention playbook §6.2"]),
    ("merchant says a rival offered a free device and lower charges",
     ["Retention playbook §8.2", "Retention playbook §8.1"]),
    ("shop has had no transactions for two weeks", ["Retention playbook §9.1"]),
    ("merchant holds back UPI near one lakh; can we promise a loan if they cross it", ["Compliance rules §3.2"]),
    ("when is a QR-only merchant ready for a Soundbox", ["Product catalogue §1.2"]),
    ("volume dropped after Diwali, is it just seasonal", ["Retention playbook §2.3"]),
    ("angry merchant disputing a charge, who handles it", ["Autonomy policy §4", "Retention playbook §6.3"]),
]
SHORT = {"Retention playbook": "Playbook", "Product catalogue": "Catalogue", "Compliance rules": "Compliance",
         "Autonomy policy": "Autonomy"}


def short(ref: str) -> str:
    for long, s in SHORT.items():
        ref = ref.replace(long, s)
    return ref


def run(search) -> tuple[list[dict], dict]:
    rows = []
    for q, ok in QUERIES:
        refs = [h["ref"] for h in search(q)]
        rows.append({"q": q, "intended": ok[0], "refs": refs,
                     "strict@1": refs[:1] == ok[:1], "strict@3": ok[0] in refs[:3],
                     "lenient@1": bool(refs) and refs[0] in ok, "lenient@3": any(r in ok for r in refs[:3])})
    keys = ("strict@1", "strict@3", "lenient@1", "lenient@3")
    return rows, {k: sum(r[k] for r in rows) / len(rows) for k in keys}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    rows, qd = run(lambda q: retrieval.retrieve(q, k=3))
    vectors = dict(zip([q for q, _ in QUERIES], embed([q for q, _ in QUERIES])))
    _, mem = run(lambda q: retrieval._search_memory(vectors[q], None, 3))

    print(f"{'#':<3}{'query':<62}{'intended':<18}{'top-3 retrieved (Qdrant)':<60}{'@1':>4}{'@3':>4}")
    print("-" * 151)
    for i, r in enumerate(rows, 1):
        print(f"{i:<3}{r['q'][:60]:<62}{short(r['intended']):<18}{', '.join(short(x) for x in r['refs']):<60}"
              f"{'✓' if r['strict@1'] else '✗':>4}{'✓' if r['strict@3'] else '✗':>4}")
    print("(✓/✗ are strict: the intended section)")
    print(f"\n{'backend':<22}{'strict@1':>10}{'strict@3':>10}{'lenient@1':>11}{'lenient@3':>11}")
    for name, m in (("Qdrant (local)", qd), ("in-memory fallback", mem)):
        print(f"{name:<22}{m['strict@1']:>10.0%}{m['strict@3']:>10.0%}{m['lenient@1']:>11.0%}{m['lenient@3']:>11.0%}")

    print("\nSafety net (cause -> expected section), one generic query per cause:")
    retrieval.MISMATCHES.clear()
    for cause in retrieval.EXPECTED_SECTION:
        top = retrieval.retrieve(f"merchant problem: {cause}", k=3, cause=cause)[0]
        print(f"  {cause:<11} -> {short(top['ref']):<18}" + ("  (top hit corrected by the safety net)" if top.get("safety_net") else ""))
    print(f"  {len(retrieval.MISMATCHES)} mismatch(es) logged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
