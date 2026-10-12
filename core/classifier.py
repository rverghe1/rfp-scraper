"""LLM-based Go/Escalate/No-Go classification for scraped RFP postings,
using Mike Kosinski's go/no-go framework (see CLAUDE.md), PLUS lifecycle
classification (is this posting still open for response, or already
closed/awarded?) - both done in the same call.

Why lifecycle moved here instead of staying as hardcoded per-portal status-
string matching in run_all.py: every portal spells "open" and "closed"
differently in raw scraped text - Bonfire uses numeric IDs, Wisconsin says
"Posted/Awaiting Responses" vs "Award Phase", PASSPort NYC says "Released"
vs "Selections Made", BidBuy/NJSTART say "Sent" and don't reliably keep
that in sync with the actual deadline. Hardcoding each portal's exact
vocabulary was fragile (confirmed missing/wrong for several portals in
testing) and doesn't generalize to new portals. An LLM reading the raw
status text plus a computed "has the deadline passed" fact handles this
robustly without per-portal maintenance.

Separate from core/matcher.py: the matcher is deterministic (does this
posting name one of our 314 facilities?); this module makes the two
judgment calls the matcher can't - is this RFP actually worth pursuing, and
is it still open - by sending the posting's title/agency/status/description
to an LLM.

Provider: OpenAI (model "gpt-5.6-luna" - not an Anthropic/Claude model).
Requires OPENAI_API_KEY in the environment.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Literal, Optional

from dateutil import parser as date_parser
from pydantic import BaseModel

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

MODEL = "gpt-5.6-luna"

FRAMEWORK_PROMPT = """You are triaging incoming government RFP/bid postings for Clipboard Health, \
a nursing and clinical staffing marketplace that connects healthcare facilities with independent \
contractor nursing professionals (RNs, LPNs, CNAs) on a per-diem/shift basis. Use this go/no-go \
framework, written by the RFP program owner:

STEP 1 - AUTOMATIC NO-GO (any of these -> NO-GO, even with no description):
- The title or agency plainly names a service line that is NOT nursing, clinical staffing, home
  health, or hospice — examples: construction, renovation, cemetery work, food/dietary, janitorial,
  concession operations, IT infrastructure, real estate, land/park/trail management, training
  programs for students, facility appraisals or rate studies, event staffing for non-clinical
  purposes. If the title makes this unambiguous, classify NO-GO immediately. You do NOT need a
  description to confirm a clear mismatch — absence of description is not a reason to ESCALATE
  something that is obviously off-topic.
- Requires W-2 employment for all staff and prohibits independent contractors/marketplace model.
- Requires a single exclusive vendor in a way that blocks multiple independent professionals from
  filling shifts concurrently.
- Insurance/bonding requirements well beyond a standard COI package (e.g. a large performance bond)
  with no pre-approved exception path.
- Not enough time before the deadline to build a compliant response (use your judgment on the
  posting's close date relative to today if given; otherwise don't penalize for this).

STEP 2 - GO SIGNALS (favor GO if enough of these are present):
- Government-owned or hospital-district-owned facility — including county hospitals, public
  hospital districts, VA facilities, and government-run acute care or long-term care hospitals —
  with a documented need for per diem or contract nursing staff.
- Home health or hospice agency with staffing needs.
- Commodity codes align with NIGP 948-46, 948-74, 962-69, or NAICS 561320, 623110, or
  UNSPSC 80111601/85101601.
- Structure allows independent contractor/marketplace model (non-exclusive, multiple awarded
  vendors, "secondary"/"supplemental" staffing language, or per diem fill language).
- Achievable insurance/licensing/credentialing requirements (standard COI, standard background
  checks, standard licensure verification).

STEP 3 - ESCALATE (real signal, but needs human review before committing):
Use ESCALATE ONLY when the title genuinely could describe nursing/clinical/hospital staffing but
you lack the specifics to resolve it — for example, "Nursing Services RFP" with no further detail,
or a title that names a qualifying facility type but doesn't clarify the staffing model.
Do NOT use ESCALATE as a catch-all for any title-only posting. If the title is off-topic, that
is a NO-GO; if the title is clearly on-topic but details are missing, that is ESCALATE.

Specific ESCALATE triggers:
- Hybrid ownership: government-owned facility operated under a private management contract.
- Ambiguous employment-model language (unclear whether independent contractors are allowed).
- High-value, multi-year, or portfolio-wide awards.
- New state licensing exposure (a Nurse Agency license the company may not currently hold).
- MSP/contract labor management structure (where a county wants ONE company to manage ALL staffing
  vendors — this is a subcontractor vehicle, not a direct prime bid opportunity).

Classify the posting into exactly one bucket: GO, ESCALATE, or NO-GO. Give a one-to-two sentence
reasoning grounded in the specific text — do not invent facts not in the posting.

SEPARATELY, also classify the posting's LIFECYCLE STAGE: is it still open for a new response, or
already closed/awarded? You're given the portal's raw status text verbatim (wording varies wildly
by portal - "Open", "Sent", "Posted/Awaiting Responses", "Award Phase", "Selections Made", numeric
codes, etc.) plus a computed fact about whether the stated deadline has already passed. Use both
together, since neither alone is fully reliable (a status label can be stale; a missing or
malformed deadline shouldn't be read as passed):
  - OPEN: the status text describes an active, not-yet-decided solicitation (posted, sent,
    published, awaiting responses, under advertisement) AND the deadline (if known) has not passed.
  - CLOSED_OR_AWARDED: the status text describes a completed/decided one (closed, awarded, selected,
    approved, converted to a purchase order, cancelled) OR the deadline has clearly passed with no
    contrary signal.
  - UNCLEAR: the status text and deadline genuinely don't resolve it either way - don't guess.

IMPORTANT: bucket and lifecycle are INDEPENDENT judgments. Do not let an expired or past deadline
pull your bucket toward NO-GO - that belongs only in the LIFECYCLE classification. If the service
line and structure would otherwise qualify as GO or ESCALATE, keep that bucket even when lifecycle
is CLOSED_OR_AWARDED (e.g. a real, on-topic per-diem nursing RFP whose deadline has already passed
is still GO/CLOSED_OR_AWARDED, not NO-GO - it was a genuine staffing opportunity, just not one you
can respond to today. That distinction matters: these rows feed a tracker of past staffing awards
used to predict when a facility's next contract cycle opens, so miscategorizing a real staffing
award as NO-GO erases it from that history).

CONTRACT END DATE: Only when you classified LIFECYCLE as CLOSED_OR_AWARDED, also check whether the
title, description, or status text mentions when the resulting contract itself ends (as opposed to
the bid submission deadline) - e.g. "2-year contract", "expires December 2027", "contract term
through FY2028", "5-year contract effective 2024". If you find one, extract it as plain text (don't
reformat or compute a real date - just report what's stated, e.g. "5 years from award" or "December
2027" as written). If no contract end/term date is mentioned anywhere in the text you were given,
return null - do not guess or infer one, and do not confuse it with the bid submission deadline
covered above."""


# Currently identical to FRAMEWORK_PROMPT - this slot is for the NEXT
# candidate prompt revision to A/B test via --test-prompt against the
# separate "Scraper Results (Test Prompt)" tab, without touching production.
# History: this is where the 2026-09-15 rewrite (tightened NO-GO/ESCALATE
# guidance, explicit hospital/VA callout in Step 2) was tested before being
# promoted into FRAMEWORK_PROMPT above on 2026-09-15, along with a fix for
# a bucket/lifecycle conflation bug found during that test (a real, on-topic
# nursing RFP with a merely-expired deadline was getting NO-GO'd instead of
# GO + CLOSED_OR_AWARDED - see the "IMPORTANT" paragraph above).
TEST_FRAMEWORK_PROMPT = FRAMEWORK_PROMPT


class RFPClassification(BaseModel):
    bucket: Literal["GO", "ESCALATE", "NO-GO"]
    reasoning: str
    matched_disqualifier: Optional[str] = None
    lifecycle: Literal["OPEN", "CLOSED_OR_AWARDED", "UNCLEAR"]
    # Only meaningful when lifecycle == "CLOSED_OR_AWARDED" - the contract's
    # own end/term date (not the bid submission deadline), extracted as
    # plain text verbatim from whatever the posting says, e.g. "5 years
    # from award" or "December 2027". Null when nothing is mentioned - see
    # the CONTRACT END DATE section of FRAMEWORK_PROMPT. Never hallucinated:
    # the model is told explicitly to return null rather than guess.
    contract_end_date: Optional[str] = None


_client = None


def _get_client():
    global _client
    if OpenAI is None:
        raise RuntimeError("The 'openai' package isn't installed. Run: pip3 install openai")
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "No OPENAI_API_KEY found in the environment. Set it before using the "
                "LLM classifier, e.g.: export OPENAI_API_KEY=sk-..."
            )
        # Explicit timeout: the default client has hung for 30+ min on a
        # stalled connection in production, blocking the whole run behind
        # one row. max_retries=1 keeps a transient failure from compounding
        # that wait across multiple attempts.
        _client = OpenAI(timeout=30.0, max_retries=1)
    return _client


def _deadline_passed_fact(close_date_text):
    """Computed in Python (reliable date math), handed to the LLM as a
    fact rather than asking it to do date arithmetic itself. Uses
    dateutil's flexible parser since every portal formats dates
    differently (see run_all.py's old per-portal strptime calls, now
    unnecessary here). Returns a short string for the prompt, never raises."""
    if not close_date_text or not close_date_text.strip():
        return "no deadline given"
    try:
        import datetime
        dt = date_parser.parse(close_date_text, fuzzy=True)
        now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
        return "deadline has passed" if dt < now else "deadline has NOT passed"
    except (ValueError, OverflowError, TypeError):
        return "deadline text unparseable"


def classify_rfp(title, agency, description="", state="", facility_match="",
                  status="", close_date="", model=MODEL, framework_prompt=None):
    """Returns an RFPClassification (bucket, reasoning, matched_disqualifier,
    lifecycle). Pass framework_prompt=TEST_FRAMEWORK_PROMPT (or any other
    candidate prompt string) to A/B test a revision without touching the
    production FRAMEWORK_PROMPT default."""
    client = _get_client()

    context_lines = [f"Title: {title}", f"Agency: {agency}"]
    if state:
        context_lines.append(f"State: {state}")
    if facility_match:
        context_lines.append(
            f"Matched against our county-owned SNF list as: {facility_match} "
            f"(this is one of our known target facilities)"
        )
    context_lines.append(f"Portal's raw status text: {status or '(none given)'}")
    context_lines.append(f"Portal's raw closing/deadline date text: {close_date or '(none given)'}")
    context_lines.append(f"Computed fact about that deadline: {_deadline_passed_fact(close_date)}")
    context_lines.append(f"Description: {description or '(no description available - title only)'}")

    # Belt-and-suspenders on top of the client's own timeout= (see
    # _get_client): confirmed in production that a single .responses.parse()
    # call can sit on one unbroken connection for 29+ minutes without the
    # client-level timeout ever firing - so this forces a hard abort via a
    # worker thread instead of trusting the SDK to enforce it. The worker
    # thread leaks (Python has no safe way to kill a thread blocked in C/
    # socket code) but that's one dangling thread per stuck row, not a
    # growing hang - the row gets marked ERROR and classify_batch moves on.
    def _call():
        return client.responses.parse(
            model=model,
            instructions=framework_prompt or FRAMEWORK_PROMPT,
            input="\n".join(context_lines),
            text_format=RFPClassification,
        )

    # No `with` block / no blocking shutdown() - Python can't kill a thread
    # stuck in C/socket code, so a wait=True shutdown here would just
    # reproduce the exact hang this is meant to prevent. shutdown(wait=False)
    # lets the orphaned worker thread finish (or never finish) on its own
    # while this call returns immediately.
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        response = future.result(timeout=35.0)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        raise TimeoutError(
            "classify_rfp: hard 35s timeout - client-level timeout did not fire"
        )
    pool.shutdown(wait=False)
    return response.output_parsed


def classify_batch(rows, title_key="title", agency_key="agency", state_key="state",
                    description_key="description", facility_key="facility_match",
                    status_key="status", close_date_key="close_date", framework_prompt=None):
    """rows: list of dicts. Returns a new list of dicts with 'llm_bucket',
    'llm_reasoning', 'llm_disqualifier', and 'llm_lifecycle' added.
    Classification failures (network errors, etc.) are caught per-row so
    one bad call doesn't abort the whole batch - that row gets
    bucket='ERROR', lifecycle='UNCLEAR', and the exception text as
    reasoning, and processing continues. Pass framework_prompt to A/B test
    a candidate prompt (see TEST_FRAMEWORK_PROMPT) instead of production."""
    out = []
    for r in rows:
        r2 = dict(r)
        try:
            result = classify_rfp(
                title=r.get(title_key, ""),
                agency=r.get(agency_key, ""),
                description=r.get(description_key, ""),
                state=r.get(state_key, ""),
                facility_match=r.get(facility_key, ""),
                status=r.get(status_key, ""),
                close_date=r.get(close_date_key, ""),
                framework_prompt=framework_prompt,
            )
            r2["llm_bucket"] = result.bucket
            r2["llm_reasoning"] = result.reasoning
            r2["llm_disqualifier"] = result.matched_disqualifier or ""
            r2["llm_lifecycle"] = result.lifecycle
            # Enforced in code, not just prompted: never carry a contract end
            # date for anything other than CLOSED_OR_AWARDED, regardless of
            # what the model returned - belt-and-suspenders against the
            # field leaking onto OPEN/UNCLEAR rows.
            r2["llm_contract_end_date"] = (
                result.contract_end_date or "" if result.lifecycle == "CLOSED_OR_AWARDED" else ""
            )
        except Exception as e:
            r2["llm_bucket"] = "ERROR"
            r2["llm_reasoning"] = f"{type(e).__name__}: {e}"
            r2["llm_disqualifier"] = ""
            r2["llm_lifecycle"] = "UNCLEAR"
            r2["llm_contract_end_date"] = ""
        out.append(r2)
    return out
