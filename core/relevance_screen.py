"""A dedicated, strict topic-only screen for whether a posting is actually
nursing/clinical/home-health/hospice STAFFING - separate from
core/classifier.py's go/no-go framework, which judges business-model fit
(employment type, insurance, contract structure) and assumes topic
relevance has already been established. That assumption doesn't always
hold: ESCALATE is deliberately permissive by design ("real signal, needs
human review"), and rows scraped before a framework_prompt tightening never
get retroactively reclassified unless the same posting is scraped again.
Both leave non-staffing postings sitting in "Scraper Results" long after
they should've been excluded.

This screen is intentionally a single yes/no topic judgment, nothing else -
it does not re-litigate business fit, lifecycle, or facility match. It only
answers "is the thing being procured here nursing/clinical/home-health/
hospice staffing itself" - used to build the dashboard-facing filtered tab
(see push_healthcare_screen.py and run_all.py's per-run screening step)."""
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Optional

from pydantic import BaseModel

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

MODEL = "gpt-5.6-luna"

SCREEN_PROMPT = """You are screening government RFP/bid postings for Clipboard Health, a nursing and \
clinical staffing marketplace. Answer ONLY one question: is the thing being procured here NURSING, \
CLINICAL, HOME HEALTH, or HOSPICE STAFFING itself (the people/professionals being staffed - RNs, LPNs, \
CNAs, or equivalent per-diem/contract clinical staff)?

Answer "yes" ONLY when the core subject of the posting is staffing nursing/clinical/home-health/hospice \
professionals. Answer "no" for everything else, including other healthcare-adjacent procurement that is \
NOT staffing itself - medical equipment or supplies, EHR/IT systems, facility construction or maintenance, \
dietary/food service, non-clinical staffing (e.g. administrative, janitorial, security), consulting or \
rate studies, training programs, or MSP/vendor-management vehicles that aren't a direct staffing bid. \
Say "no" even when "health", "hospital", "medical", or "nursing home" appears in the title or agency name, \
if the actual thing being bought is not clinical staffing.

This is a narrow topic filter, not a business-fit judgment - ignore employment model (W-2 vs independent \
contractor), insurance/bonding requirements, contract exclusivity, or deadline timing entirely; those are \
judged elsewhere. If the title alone makes the topic unambiguous, you do not need a description to decide. \
If the title is genuinely ambiguous about topic (not business fit - topic), lean "no": this filter only \
needs to admit postings confidently about clinical staffing, not catch every possible edge case."""


class HealthcareScreen(BaseModel):
    is_healthcare_staffing: bool
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
                "healthcare relevance screen, e.g.: export OPENAI_API_KEY=sk-..."
            )
        # Same hard-timeout belt-and-suspenders as core/classifier.py's
        # _get_client - see that module for why the client-level timeout=
        # alone isn't trusted.
        _client = OpenAI(timeout=30.0, max_retries=1)
    return _client


def screen_healthcare_staffing(title, agency="", description="", model=MODEL):
    """Returns a HealthcareScreen (is_healthcare_staffing: bool, reasoning: str)."""
    client = _get_client()

    context_lines = [f"Title: {title}", f"Agency: {agency}"]
    context_lines.append(f"Description: {description or '(no description available - title only)'}")

    def _call():
        return client.responses.parse(
            model=model,
            instructions=SCREEN_PROMPT,
            input="\n".join(context_lines),
            text_format=HealthcareScreen,
        )

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        response = future.result(timeout=35.0)
    except FuturesTimeout:
        pool.shutdown(wait=False)
        raise TimeoutError(
            "screen_healthcare_staffing: hard 35s timeout - client-level timeout did not fire"
        )
    pool.shutdown(wait=False)
    return response.output_parsed


def screen_batch(rows, title_key="Title", agency_key="Agency", description_key="Notes"):
    """rows: list of dicts. Returns the subset that screened "yes", unchanged
    (no columns added/removed) - callers that want the reasoning or want to
    keep failures visible should call screen_healthcare_staffing directly
    per row instead. Screening failures are treated as "no" (exclude from
    the dashboard-facing tab) rather than raising, since one bad row
    shouldn't block the rest of the batch and a false exclusion here is far
    cheaper than a false inclusion reaching the dashboard."""
    passed = []
    for r in rows:
        try:
            result = screen_healthcare_staffing(
                title=r.get(title_key, ""),
                agency=r.get(agency_key, ""),
                description=r.get(description_key, ""),
            )
            if result.is_healthcare_staffing:
                passed.append(r)
        except Exception:
            continue
    return passed
