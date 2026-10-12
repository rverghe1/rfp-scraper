"""Opt-in LLM fallback for facility matching, layered on TOP of the
deterministic matcher in core/matcher.py - not a replacement for it.

Why a fallback, not a full LLM replacement: the deterministic matcher's
state-detection gate is cheap, fast, and has already been battle-tested
against many real false-positive cases (see matcher.py's docstring/history).
Its remaining weakness is pure name variation - "Madison Co. Nursing
Facility" vs. "Madison County Nursing Home", abbreviations, punctuation,
word order - not systematic bugs. Handing the WHOLE 314-facility list to an
LLM for open-ended matching invites hallucination (an LLM asked to find a
match is prone to finding one) and runs on every scraped row, which is by
far the highest-volume call in the pipeline.

So this module only runs when:
  1. The deterministic matcher already returned no match, AND
  2. A US state was detected for the posting (so there's a short,
     state-narrowed candidate list to disambiguate within - typically
     3-90 facilities, never the full 314).

The LLM is given a closed list of candidate facility names for that one
state plus "NONE_OF_THESE", and must pick exactly one - a constrained
choice, not an open-ended search, which sharply cuts hallucination risk.
A wrong answer here degrades to "we miss a real match" (same failure mode
as the deterministic matcher missing it), never "we invent a fake one",
because facility name is still validated against the real candidate list
after the call returns.

Provider: OpenAI (model "gpt-5.6-luna"), same as core/classifier.py.
Requires OPENAI_API_KEY in the environment.
"""
import os
from typing import Literal, Optional

from pydantic import BaseModel

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

MODEL = "gpt-5.6-luna"

PROMPT_TEMPLATE = """You are matching a government RFP/bid posting to a specific nursing \
facility from a fixed list, or determining that it doesn't match any of them.

Posting title: {title}
Posting agency/jurisdiction: {org}
Detected state: {state}

Candidate facilities in {state} (choose exactly one, or NONE_OF_THESE if the posting \
doesn't clearly name/reference one of these specific facilities):
{candidates}

Only pick a facility if the posting's title or agency text plausibly refers to that \
specific facility (e.g. shares its name, a recognizable abbreviation of it, or its \
exact street address/campus name). A posting that's merely in the same state or county \
as a facility, without naming it, should get NONE_OF_THESE - don't guess based on \
category or general geography alone."""


class FacilityMatchResult(BaseModel):
    facility_name: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    reasoning: str


_client = None


def _get_client():
    global _client
    if OpenAI is None:
        raise RuntimeError("The 'openai' package isn't installed. Run: pip3 install openai")
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "No OPENAI_API_KEY found in the environment. Set it before using the "
                "LLM facility matcher, e.g.: export OPENAI_API_KEY=sk-..."
            )
        # See core/classifier.py's _get_client for why: the default client
        # has no timeout and can hang indefinitely on a stalled connection.
        _client = OpenAI(timeout=30.0, max_retries=1)
    return _client


def llm_match_facility(title, org, state, candidate_names, model=MODEL):
    """candidate_names: list of facility NAME strings, already narrowed to
    one state by the caller. Returns the matched facility name (guaranteed
    to be one of candidate_names, never invented) or None."""
    if not candidate_names:
        return None

    client = _get_client()
    candidates_block = "\n".join(f"- {n}" for n in candidate_names) + "\n- NONE_OF_THESE"
    prompt = PROMPT_TEMPLATE.format(title=title, org=org, state=state, candidates=candidates_block)

    response = client.responses.parse(
        model=model,
        input=prompt,
        text_format=FacilityMatchResult,
    )
    result = response.output_parsed

    if not result.facility_name or result.facility_name == "NONE_OF_THESE":
        return None
    # Hard validation: never trust a name the LLM invented or slightly
    # reworded - only an exact match against the real candidate list counts.
    # This is what keeps a bad LLM call safe (miss a match) instead of
    # dangerous (fabricate one).
    if result.facility_name not in candidate_names:
        return None
    return result.facility_name


def llm_match_facility_row(row_title, row_org, facilities):
    """Convenience wrapper: facilities is the full list from
    core.matcher.load_facilities() (the `rows` half of the tuple). Detects
    the posting's state the same way the deterministic matcher does (so
    this fallback only ever fires when a state was actually found - no
    state means no narrowed candidate list, so it's skipped rather than
    risking a match against all 314 facilities), then narrows to that
    state and returns the matched facility dict (from `facilities`), or
    None."""
    from core.matcher import detect_state

    row_state = detect_state(f"{row_title} {row_org}", org_text=row_org)
    if not row_state:
        return None
    candidates = [f for f in facilities if f.get("STATE", "").upper() == row_state.upper()]
    if not candidates:
        return None
    name_to_fac = {f["NAME"]: f for f in candidates}
    matched_name = llm_match_facility(row_title, row_org, row_state, list(name_to_fac.keys()))
    return name_to_fac.get(matched_name)
