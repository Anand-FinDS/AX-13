"""Draft: merchant messages in the merchant's language (AX13_MODEL_REASON).

- Fee messages are the §6.2 templates filled in code with the merchant's numbers; the LLM only
  translates them (English merchants get the template verbatim, no LLM call).
- Other messages are written from the cited section's text and the merchant's own numbers.
- A blocked draft is redrafted once with the gate's reasons (Compliance rules §7.3).
- Demo: the first loan draft for a merchant with an eval fixture can be the fixture itself, so the
  gate is seen blocking it. The draft is labelled with its source.
"""
from __future__ import annotations

import json
import re

from common.llm import complete
from common.settings import REPO_ROOT

PROMPT = (REPO_ROOT / "agent" / "prompts" / "draft.md").read_text(encoding="utf-8")
FIXTURES = {f["merchant_id"]: f for f in (json.loads(p.read_text(encoding="utf-8"))
                                          for p in (REPO_ROOT / "eval" / "fixtures").glob("*.json"))}


def fixture_for(merchant_id: str, kind: str) -> dict | None:
    f = FIXTURES.get(merchant_id)
    return f if f and f["draft_kind"] == kind else None


def template(spec: dict, source: str = "template") -> dict:
    """The code-filled English message, no LLM (merchants outside the demo set, or a draft not cached)."""
    t = spec.get("english_template") or spec.get("template")
    return {"text": t, "english": t, "source": source}


def write(spec: dict, language: str, feedback: dict | None = None) -> dict:
    """One draft: {'text', 'english', 'source'}."""
    if spec.get("template") and language == "English":
        return {"text": spec["template"], "english": spec["template"], "source": "template (Retention playbook §6.2)"}
    system = PROMPT.format(language=language, max_words=spec["max_words"])
    body = {"purpose": spec["purpose"], "facts": spec["facts"],
            "policy_section": {"ref": spec["section"]["ref"], "text": spec["section"]["text"]}}
    if spec.get("template"):
        n = len([x for x in re.split(r"(?<=[.!?])\s+", spec["template"]) if x.strip()])
        body = {"purpose": f"Translate this message faithfully, every sentence in order: it has {n} sentences and "
                           f"the translation must have all {n}. Add only a greeting and the sign-off. Keep every "
                           "number and ₹ amount in digits exactly as written, and the meaning of 'proposed framework'.",
                "message_to_translate": spec["template"], "facts": spec["facts"]}
    if feedback:
        body["previous_draft_blocked"] = feedback
        body["instruction"] = "Write a new draft that fixes every reason the previous one was blocked."
    out = complete([{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(body, ensure_ascii=False, indent=1)}],
                   role="reason", json_mode=True)
    src = "template, translated by LLM" if spec.get("template") else "LLM"
    return {"text": str(out.get("text", "")).strip(), "english": str(out.get("english", "")).strip(),
            "source": src + (" (redraft)" if feedback else "")}
