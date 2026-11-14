"""Reflection: two-layer check that every number in an answer is grounded in tool outputs.

Layer 1 (code): extract every number from the draft and look for it in the tool outputs,
allowing for rounding and formatting. Layer 2 (LLM): only when layer 1 flags something,
a critique prompt approves the draft or returns a corrected answer (one revision max).
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from prompts.prompts import REFLECTION_PROMPT, prompt_to_trace

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


class ReflectionVerdict(BaseModel):
    approved: bool = Field(description="True if every figure is supported by the tool outputs")
    revised_answer: Optional[str] = Field(None, description="Corrected answer when approved is false")
    notes: str = Field("", description="One sentence explaining the verdict")


def extract_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _is_trivial(n: float) -> bool:
    if n < 10:  # counts, list numbering, "top 5"
        return True
    if float(n).is_integer() and 1990 <= n <= 2100:  # years
        return True
    return False


def ungrounded_numbers(draft: str, sources: list[str]) -> list[str]:
    grounded = set()
    for s in sources:
        for g in extract_numbers(s):
            grounded.add(g)
            grounded.add(abs(g))
    flagged = []
    for n in extract_numbers(draft):
        if _is_trivial(n):
            continue
        ok = any(abs(n - g) <= max(0.51, 0.005 * abs(g)) for g in grounded)
        if not ok and n not in flagged:
            flagged.append(n)
    return [f"{x:,.2f}".rstrip("0").rstrip(".") for x in flagged]


def reflect(llm, question: str, draft: str, sources: list[str], trace: dict) -> str:
    """Return the final answer (draft or corrected) and write the verdict into the trace."""
    flags = ungrounded_numbers(draft, sources + [question])
    info = {"code_check_flags": flags, "llm_used": False, "verdict": "approved", "notes": "", "revised": False}
    if not flags:
        info["notes"] = "Every number in the answer appears in the tool outputs."
        trace["reflection"] = info
        return draft

    info["llm_used"] = True
    variables = {"question": question, "draft": draft, "tool_outputs": "\n".join(sources)[:12000],
                 "flags": ", ".join(flags)}
    trace["prompts"].append(prompt_to_trace("reflection", REFLECTION_PROMPT, variables))
    try:
        verdict: ReflectionVerdict = (REFLECTION_PROMPT | llm.with_structured_output(ReflectionVerdict)).invoke(variables)
    except Exception as exc:
        info.update(verdict="unverified", notes=f"Reflection call failed: {exc}"[:300])
        trace["reflection"] = info
        return draft + "\n\n> ⚠️ Some figures could not be verified against tool results: " + ", ".join(flags)

    info["notes"] = verdict.notes
    if verdict.approved or not verdict.revised_answer:
        info["verdict"] = "approved by critique"
        trace["reflection"] = info
        return draft
    revised = verdict.revised_answer.strip()
    remaining = ungrounded_numbers(revised, sources + [question])
    info.update(verdict="revised", revised=True, final_flags=remaining)
    trace["reflection"] = info
    if remaining:  # one revision only: be transparent about anything still unverified
        revised += "\n\n> ⚠️ Not verified against your data: " + ", ".join(remaining)
    return revised
