#!/usr/bin/env python3
"""
RFP Scraper - scrapes portals for nursing/clinical/hospital staffing RFPs
relevant to the services we provide, and separately tracks CLOSED/AWARDED
postings at our known facilities to help predict when their next contract
cycle opens. Two Google Sheet tabs, two different jobs, both decided by a
SINGLE LLM call per posting (core/classifier.py) - not by hardcoded
per-portal status-string parsing, which used to live here and was fragile
(every portal spells "open" and "closed" differently, and several were
confirmed missing/wrong in testing before this was moved into the prompt):

  - "Scraper Results": postings the LLM says are both OPEN (lifecycle) and
    GO/ESCALATE (relevance). NOT gated by facility match - a real staffing
    RFP at a hospital or facility we don't have on file yet is just as
    valid a result here as one that matches our 314-facility list. Facility
    Match is still shown as an informational column when it happens to hit.
  - "Awarded-Historical RFPs": postings the LLM says are CLOSED_OR_AWARDED
    (lifecycle) and GO/ESCALATE (relevance) AND that DO match one of our
    314 known facilities (facility match IS the gate here, since the whole
    point is tracking a specific facility's contract cadence - the LLM
    relevance check on top of that exists because a facility's own name
    containing "nursing"/"hospital" makes unrelated county business at that
    address look like a keyword hit otherwise - confirmed happening in
    testing, e.g. parking lot repaving, HVAC, audits).

Because lifecycle is now an LLM judgment (grounded in a computed "has the
deadline passed" fact - see core/classifier.py - not the LLM doing date
math itself), every portal's scraped hits flow through the exact same
scrape -> dedupe -> build -> classify -> route pipeline, regardless of how
that portal spells its status text. No more per-portal status vocabulary
to maintain.

Local CSV backups always get written first under output/, and only get
moved to output/pushed/ once the Sheet write succeeds - so a failed Sheet
write never loses data, just leaves it queued for push_pending.py.

Usage:
    python3 run_all.py                 # run every portal, with LLM classification
    python3 run_all.py bonfire wisconsin   # run just these two
    python3 run_all.py --no-llm        # skip LLM classification - NOTHING gets written to
                                        # either tab without it now, since the LLM is the only
                                        # way to determine both relevance and open-vs-closed
                                        # status (facility match alone never gated either tab).
    python3 run_all.py --llm-facility-match  # also run the opt-in LLM facility-match fallback
                                              # (see core/llm_facility_matcher.py) - only fires on
                                              # rows the deterministic matcher missed, and only
                                              # when a state was detected. Off by default -
                                              # unproven at volume, test before relying on it.
    python3 run_all.py --test-prompt indiana wisconsin  # classify with the candidate prompt
                                              # (core.classifier.TEST_FRAMEWORK_PROMPT) instead of
                                              # production, and write EVERY classified row
                                              # (unfiltered - all buckets/lifecycles) to the
                                              # separate "Scraper Results (Test Prompt)" tab.
                                              # Production tabs are untouched while this is on.

Requires:
    - secrets.json in this directory (portal credentials - gitignored, chmod 600)
    - data/facilities.csv (the 314-facility list)
    - Google service account key at the path set in core/sheets.py, and that
      service account must be shared as Editor on the target spreadsheet.
    - OPENAI_API_KEY in the environment (uses OpenAI's gpt-5.6-luna) - required
      for anything to be written to either tab now.
    - python-dateutil (pip3 install python-dateutil) - used by
      core/classifier.py to compute the "has the deadline passed" fact.
"""
import csv
import datetime
import os
import shutil
import sys
from pathlib import Path

from core.browser import new_page
from core.matcher import load_facilities, match_facility
from core.sheets import (
    append_rows, append_tracker_rows, append_test_prompt_rows, append_dashboard_rows,
    HEADER, TRACKER_HEADER,
)
from adapters import (
    bonfire, demandstar, wisconsin, njstart, passaicbids, indiana, passportnyc,
    mississippi, bidbuy, pa_emarketplace, scbo, utah, bidnet, magic_ms, michigan,
    nh_dhhs, commbuys, oregonbuys, emma_md, nyscr, edison_tn, arbuy, movers_mo,
    instantmarkets_mo, cal_eprocure, kentucky_vss, colorado_vss, nhprocure,
    arkansas_osp,
)
import requests

OUTPUT_DIR = Path(__file__).parent / "output"
PUSHED_DIR = OUTPUT_DIR / "pushed"

# Broadened beyond SNF/nursing-home staffing to also catch the hospital
# staffing vertical we're now expanding into. Multi-word terms are safe to
# use as-is - adapters/ivalua.py and adapters/mississippi.py URL-encode the
# keyword themselves; every other adapter fills a real search box, which
# never needed encoding.
KEYWORDS = [
    # "rn" used to be in this list and got dropped: as a bare 2-letter
    # substring, portals that don't do word-boundary search (most of them -
    # only bonfire.filter_keyword applies one) matched it inside ordinary
    # words like "governance", "warning", "learning", "northern" - confirmed
    # pulling in unrelated postings in testing.
    "nursing", "staffing", "cna", "lpn", "clinical",
    "hospital staffing", "travel nursing", "per diem nursing",
    "supplemental staffing", "agency nursing", "skilled nursing",
    "long term care",
]

# Set by main() from --no-llm / the OPENAI_API_KEY check. Now controls
# BOTH tabs, not just "Scraper Results" - see _classify_and_route.
USE_LLM = True

# Opt-in only (--llm-facility-match) - off by default even when USE_LLM go/no-
# go classification is on. See core/llm_facility_matcher.py for why this is a
# fallback layered on the deterministic matcher, not a replacement for it.
USE_LLM_FACILITY_MATCH = False

# Opt-in only (--test-prompt) - when on, EVERY classified row (regardless of
# bucket/lifecycle) gets written to the "Scraper Results (Test Prompt)" tab
# using core.classifier.TEST_FRAMEWORK_PROMPT instead of the normal
# route-into-two-tabs behavior. Purely an A/B testing surface; production
# tabs are untouched while this is on.
USE_TEST_PROMPT = False

_llm_facility_match_stats = {"attempted": 0, "matched": 0, "errors": 0}


def _match_facility(title, org, facilities):
    """Deterministic match_facility() first, always. Only when that returns
    no match AND --llm-facility-match is on, falls back to the LLM
    disambiguation pass (core/llm_facility_matcher.py) - which only fires
    itself when a state was detected, narrowing candidates to that state's
    facilities rather than ever risking a match against all 314."""
    fac = match_facility(title, org, facilities)
    if fac or not USE_LLM_FACILITY_MATCH:
        return fac

    from core.llm_facility_matcher import llm_match_facility_row
    _llm_facility_match_stats["attempted"] += 1
    try:
        fac = llm_match_facility_row(title, org, facilities[0])
    except Exception as e:
        _llm_facility_match_stats["errors"] += 1
        print(f"  [llm-facility-match] error on {title[:50]!r}: {type(e).__name__}: {e}")
        return None
    if fac:
        _llm_facility_match_stats["matched"] += 1
        print(f"  [llm-facility-match] matched {title[:50]!r} -> {fac['NAME']} "
              f"(deterministic matcher missed this one)")
    return fac


def _build_row(portal, title, agency, state, status, close_date, link, notes, now, fac):
    """One row shape shared by both tabs - includes both "Closing Date" and
    "Award/Close Date" pointing at the same value so it slots into whichever
    of HEADER/TRACKER_HEADER ends up being used to write it; extra keys not
    in a given header are just ignored by core.sheets.append_rows.

    "Facility" holds the plain matched name here (classify_batch reads this
    as context - see _apply_llm_classification's facility_key), not a
    hyperlink - _linkify_facility converts it to a clickable Salesforce link
    right before writing, using "_facility_sfdc_id" (an extra key, dropped
    silently by the CSV/sheet writers since it's not in HEADER)."""
    return {
        "Portal": portal, "Facility": fac["NAME"] if fac else "No Matched Facility",
        "_facility_sfdc_id": fac["SALESFORCE_ACCOUNT_ID"] if fac else None,
        "Bucket": "FACILITY MATCH" if fac else "NEW LEAD",
        "Title": title, "Agency": agency, "State": (fac["STATE"] if fac else state),
        "Status": status, "Closing Date": close_date, "Award/Close Date": close_date,
        "Link": link, "Notes": notes, "Scraped At": now,
    }


def _linkify_facility(rows):
    """Turns "Facility" from a plain matched name into a Google Sheets
    HYPERLINK formula pointing at the Salesforce Account page, for rows
    that had a real facility match. Call this AFTER classification (which
    needs the plain name as context), right before writing to a sheet -
    web/app.py separately extracts the underlying URL via a FORMULA-mode
    read for the dashboard's clickable rendering."""
    for r in rows:
        sfdc_id = r.get("_facility_sfdc_id")
        if sfdc_id:
            name = r["Facility"].replace('"', "'")
            url = f"https://clipboardhealth.lightning.force.com/lightning/r/Account/{sfdc_id}/view"
            r["Facility"] = f'=HYPERLINK("{url}","{name}")'
    return rows


def _apply_llm_classification(rows, framework_prompt=None):
    """Adds 'LLM Bucket' / 'LLM Reasoning' / 'LLM Disqualifier' / 'LLM
    Lifecycle' to each row using Mike's go/no-go framework plus lifecycle
    classification (core/classifier.py). This is the ONLY filter/router
    left in the whole pipeline - see the module docstring. Pass
    framework_prompt to A/B test a candidate prompt (see --test-prompt)
    instead of the production default."""
    if not rows:
        return rows
    from core.classifier import classify_batch
    classified = classify_batch(
        rows,
        title_key="Title", agency_key="Agency", state_key="State",
        description_key="Notes", facility_key="Facility",
        status_key="Status", close_date_key="Closing Date",
        framework_prompt=framework_prompt,
    )
    for r in classified:
        r["LLM Bucket"] = r.pop("llm_bucket", "")
        r["LLM Reasoning"] = r.pop("llm_reasoning", "")
        r["LLM Disqualifier"] = r.pop("llm_disqualifier", "")
        r["LLM Lifecycle"] = r.pop("llm_lifecycle", "")
        # Only ever populated for CLOSED_OR_AWARDED rows (enforced in
        # core/classifier.py, not just prompted) - harmless on rows headed
        # for the "relevant" tab too, since HEADER doesn't include this
        # column and the CSV writer's extrasaction="ignore" (see
        # _write_csv_then_sheet) and append_rows' per-header .get() both
        # already drop keys a given header doesn't ask for.
        r["Contract End Date (LLM extracted)"] = r.pop("llm_contract_end_date", "")
    return classified


def _classify_and_route(portal_name, rows):
    """rows: ALL keyword-matched postings for this portal, regardless of
    status/lifecycle - no pre-filtering. The LLM determines both relevance
    (GO/ESCALATE/NO-GO) and lifecycle (OPEN/CLOSED_OR_AWARDED/UNCLEAR) in
    one call per row, then this splits into:
      - "Scraper Results": lifecycle OPEN + bucket in (GO, ESCALATE)
      - "Awarded-Historical RFPs": lifecycle CLOSED_OR_AWARDED + bucket in
        (GO, ESCALATE) + a real facility match
    Requires OPENAI_API_KEY - without it, NOTHING is written to either tab,
    since there's no more hardcoded fallback for either relevance or
    lifecycle (see the module docstring).

    If USE_TEST_PROMPT is on, this instead classifies with
    core.classifier.TEST_FRAMEWORK_PROMPT and writes EVERY row (unfiltered -
    all buckets, all lifecycles) to the separate "Scraper Results (Test
    Prompt)" tab, so the candidate prompt's full output distribution can be
    reviewed and compared against production without touching either real
    tab."""
    if not rows:
        print(f"[{portal_name}] nothing scraped.")
        return

    if not USE_LLM:
        print(f"[{portal_name}] skipping classification: LLM is off, and it's now the only way "
              f"to determine relevance AND open-vs-closed status. {len(rows)} keyword-matched "
              f"row(s) NOT written to either tab - rerun with the LLM enabled.")
        return

    if USE_TEST_PROMPT:
        from core.classifier import TEST_FRAMEWORK_PROMPT
        classified = _apply_llm_classification(rows, framework_prompt=TEST_FRAMEWORK_PROMPT)
        errors = sum(1 for r in classified if r.get("LLM Bucket") == "ERROR")
        if errors:
            print(f"[{portal_name}] test-prompt classification failed on "
                  f"{errors}/{len(classified)} row(s).")
        counts = {}
        for r in classified:
            k = (r.get("LLM Bucket", ""), r.get("LLM Lifecycle", ""))
            counts[k] = counts.get(k, 0) + 1
        print(f"[{portal_name}] {len(classified)} classified with TEST prompt -> {counts}")
        _linkify_facility(classified)
        _write_csv_then_sheet(portal_name, "test_prompt", classified, HEADER, append_test_prompt_rows)
        return

    classified = _apply_llm_classification(rows)
    errors = sum(1 for r in classified if r.get("LLM Bucket") == "ERROR")
    if errors:
        print(f"[{portal_name}] LLM classification failed on {errors}/{len(classified)} row(s) "
              f"(see 'LLM Reasoning' column for the error).")

    relevant = [r for r in classified
                if r.get("LLM Lifecycle") == "OPEN" and r.get("LLM Bucket") in ("GO", "ESCALATE")]
    _linkify_facility(relevant)

    # Awarded/closed RFP tracking ("Awarded-Historical RFPs" tab) is
    # disabled per request - only open+relevant postings get written now.
    print(f"[{portal_name}] {len(classified)} classified -> {len(relevant)} open+relevant.")

    if relevant:
        _write_csv_then_sheet(portal_name, "relevant", relevant, HEADER, append_rows)

        # Dashboard-facing second pass: "relevant" already passed the go/no-go
        # framework, but ESCALATE is deliberately permissive ("real signal,
        # needs human review") and can admit postings that aren't actually
        # clinical staffing - plus rows scraped before a prompt tightening
        # never get retroactively reclassified. This is a narrow topic-only
        # screen (core/relevance_screen.py) on top of that, feeding a
        # separate tab the dashboard reads, so non-staffing postings that
        # cleared the first pass don't show up there.
        from core.relevance_screen import screen_batch
        dashboard_relevant = screen_batch(relevant)
        print(f"[{portal_name}] {len(relevant)} open+relevant -> "
              f"{len(dashboard_relevant)} healthcare-staffing-screened for dashboard.")
        if dashboard_relevant:
            _write_csv_then_sheet(portal_name, "dashboard", dashboard_relevant, HEADER, append_dashboard_rows)


def _write_csv_then_sheet(portal_name, kind, rows, header, append_fn):
    """Shared write path for both tabs: local CSV backup first, then try
    the Sheet, only moving the CSV to pushed/ on success."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    out_path = OUTPUT_DIR / f"{portal_name}_{kind}_{now.replace(':', '')}.csv"
    with open(out_path, "w", newline="") as f:
        # extrasaction="ignore": _build_row() always includes BOTH "Closing
        # Date" and "Award/Close Date" keys so a row slots into whichever
        # tab's header ends up using it - but plain DictWriter raises on any
        # key not in `header`, which crashed every write this shared path
        # touched (confirmed happening in testing: killed 4 portals' writes
        # outright, header-only CSVs, real GO rows lost with no recovery
        # short of re-scraping). Ignoring extras is exactly the intended
        # behavior here, not a bug being papered over.
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[{portal_name}] saved {len(rows)} {kind} row(s) -> {out_path}")

    try:
        n = append_fn(rows)
        print(f"[{portal_name}] wrote {n} {kind} row(s) to the Sheet.")
        PUSHED_DIR.mkdir(exist_ok=True)
        shutil.move(str(out_path), str(PUSHED_DIR / out_path.name))
    except Exception as e:
        print(f"[{portal_name}] COULD NOT write {kind} rows to Sheet ({type(e).__name__}: {e}). "
              f"Data is safe in {out_path} - run push_pending.py once fixed.")


def _dedupe(hits, key="ref"):
    seen, out = set(), []
    for r in hits:
        k = r.get(key) or r.get("title", "")
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def run_bonfire(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        bonfire.login(page)
        orgs = bonfire.discover_org_portals(page)
        print(f"[bonfire] {len(orgs)} connected org portals")
        for org_name, org_url in orgs.items():
            org_url = org_url.rstrip("/")
            for tab in ("open", "past"):
                rows = bonfire.fetch_all(page, org_url, tab=tab)
                if not rows:
                    continue
                seen_refs = set()
                for kw in KEYWORDS:
                    for r in bonfire.filter_keyword(rows, kw):
                        key = r["ref"] or r["title"]
                        if key in seen_refs:
                            continue
                        seen_refs.add(key)
                        r2 = dict(r)
                        r2["_org"] = org_name
                        r2["_tab"] = tab
                        all_hits.append(r2)

    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("Bonfire", r["title"], r["_org"], "", r["status"], r["close_date"], r["link"],
                   f"tab={r['_tab']} ref={r['ref']}", now, _match_facility(r["title"], r["_org"], facilities))
        for r in all_hits
    ]
    _classify_and_route("bonfire", rows)


def run_demandstar(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        demandstar.login(page)
        for kw in KEYWORDS:
            all_hits.extend(demandstar.search(page, kw))

    deduped = _dedupe(all_hits, key="link")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("DemandStar", r["title"], r["org"], "", r["status"], r["close_date"], r["link"],
                   "", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("demandstar", rows)


def run_wisconsin(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(wisconsin.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("Wisconsin Bidder Portal", r["title"], r["org"], "WI", r["status"],
                   r["close_date"], r["link"], f"ref={r['ref']} type={r['type']}", now,
                   _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("wisconsin", rows)


def run_njstart(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(njstart.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("NJSTART", r["title"], r["org"], "NJ", r["status"], r["close_date"], r["link"],
                   f"ref={r['ref']}", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("njstart", rows)


def run_passaicbids(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for status in passaicbids.STATUSES:
            for kw in KEYWORDS:
                all_hits.extend(passaicbids.search(page, kw, status=status))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("PassaicBids", r["title"], "Passaic County, NJ", "NJ", r["status"],
                   r["close_date"], r["link"], f"ref={r['ref']} posted={r['posted']}", now,
                   _match_facility(r["title"], "Passaic County", facilities))
        for r in deduped
    ]
    _classify_and_route("passaicbids", rows)


def run_indiana(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(indiana.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # Append an explicit "Indiana" state hint rather than relying on
        # r["org"] alone - many Indiana agency names are generic state
        # departments ("Natural Resources") with no place name in them at
        # all, which leaves match_facility's state-gating with nothing to
        # detect and silently disabled. Confirmed false positive in testing:
        # a Missouri facility ("Monroe Manor") matched an Indiana DNR post
        # via the shared word "Monroe", purely because no state was detected.
        fac = _match_facility(r["title"], f"{r['org']}, Indiana", facilities)
        rows.append(_build_row("Indiana IDOA", r["title"], r["org"], "IN", "Open",
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("indiana", rows)


def run_mississippi(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(mississippi.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # This portal's search-results list doesn't expose an agency name,
        # so passing "" as org_text would disable match_facility's state-
        # gating entirely. Every posting is MS state/county business, so
        # pass "Mississippi" as a state hint instead.
        fac = _match_facility(r["title"], "Mississippi", facilities)
        rows.append(_build_row("Mississippi DFA", r["title"], r["org"], "MS", r["status"],
                                "", r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("mississippi", rows)


def run_bidbuy(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(bidbuy.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # BidBuy's "Organization Name" column is the *state agency* (e.g.
        # "DHS - Human Services"), not a geographic jurisdiction, so it
        # carries no state signal for match_facility's gating. Pass
        # "Illinois" instead.
        fac = _match_facility(r["title"], "Illinois", facilities)
        rows.append(_build_row("BidBuy (IL)", r["title"], r["org"], "IL", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("bidbuy", rows)


def run_commbuys(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(commbuys.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # Same as BidBuy: the "org" column is the state agency, not a
        # geographic jurisdiction, so pass "Massachusetts" for state signal.
        fac = _match_facility(r["title"], "Massachusetts", facilities)
        rows.append(_build_row("COMMBUYS (MA)", r["title"], r["org"], "MA", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("commbuys", rows)


def run_oregonbuys(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(oregonbuys.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        fac = _match_facility(r["title"], "Oregon", facilities)
        rows.append(_build_row("OregonBuys (OR)", r["title"], r["org"], "OR", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("oregonbuys", rows)


def run_emma_md(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        emma_md.login(page)
        for kw in KEYWORDS:
            all_hits.extend(emma_md.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("eMMA (MD)", r["title"], r["org"], "MD", r["status"], r["close_date"],
                   r["link"], f"ref={r['ref']}", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("emma_md", rows)


def run_nyscr(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(nyscr.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("NYS Contract Reporter", r["title"], r["org"], "NY", r["status"],
                   r["close_date"], r["link"], f"ref={r['ref']}", now,
                   _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("nyscr", rows)


def run_edison_tn(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(edison_tn.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("Edison.TN", r["title"], r["org"], "TN", r["status"], r["close_date"],
                   r["link"], f"ref={r['ref']}", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("edison_tn", rows)


def run_cal_eprocure(facilities):
    # Needs stealth=True - plain headless Playwright gets a bare 403 here,
    # same fingerprint check as BidNet Direct (see adapters/bidnet.py).
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(cal_eprocure.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("Cal eProcure (CSCR)", r["title"], r["org"], "California", r["status"],
                   r["close_date"], r["link"], f"ref={r['ref']}", now,
                   _match_facility(r["title"], f"{r['org']}, California", facilities))
        for r in deduped
    ]
    _classify_and_route("cal_eprocure", rows)


def run_arbuy(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(arbuy.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("ARBuy (AR)", r["title"], r["org"], "AR", r["status"], r["close_date"],
                   r["link"], f"ref={r['ref']}", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("arbuy", rows)


def run_pa_emarketplace(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(pa_emarketplace.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # This site gives us a real "County" column, but a bare county name
        # like "Chester" doesn't register as a Pennsylvania signal to
        # detect_state() (it only recognizes full state names/abbreviations)
        # - confirmed false match in testing (an Indiana facility matched a
        # PA university posting via generic "University"+"Health" overlap).
        # Always append an explicit "Pennsylvania" state hint alongside the
        # county, don't just fall back to it when county is blank.
        county = r["county"]
        fac = _match_facility(r["title"], f"{county}, Pennsylvania" if county else "Pennsylvania",
                               facilities)
        rows.append(_build_row("PA eMarketplace", r["title"], r["org"], "PA", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']} county={county}",
                                now, fac))
    _classify_and_route("pa_emarketplace", rows)


def run_scbo(facilities):
    list_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            list_hits.extend(scbo.search(page, kw))

        # Dedupe by link BEFORE the expensive per-result detail-page fetch -
        # keywords overlap heavily, and fetching every hit from every
        # keyword independently is what made the first version of this
        # adapter time out. Still a detail-page-per-result adapter, so it's
        # only worth running against SC's small facility count (3).
        unique_hits = _dedupe(list_hits, key="link")
        all_hits = [scbo.fetch_detail(page, h) for h in unique_hits]

    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in all_hits:
        # Always append an explicit "South Carolina" state hint - a real org
        # string doesn't reliably signal SC to match_facility's state-gating
        # either (confirmed happening in testing).
        fac = _match_facility(r["title"], f"{r['org']}, South Carolina", facilities)
        rows.append(_build_row("SCBO (SC)", r["title"], r["org"], "SC", "", r["close_date"],
                                r["link"], f"type={r['type']}", now, fac))
    _classify_and_route("scbo", rows)


def run_passportnyc(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(passportnyc.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("PASSPort NYC", r["title"], r["org"], "NY", r["status"], r["close_date"],
                   r["link"], f"ref={r['ref']}", now, _match_facility(r["title"], r["org"], facilities))
        for r in deduped
    ]
    _classify_and_route("passportnyc", rows)


def run_utah(facilities):
    all_hits = []
    with new_page(headless=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(utah.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # This portal's "org" field is really a truncated agency+description
        # blob (e.g. "Department of Health and Human Services Procure the
        # services..."), with no explicit "Utah" state name in it - same
        # failure mode as several other portals this session (Mississippi,
        # BidBuy, PA eMarketplace all needed the same fix). Append an
        # explicit state hint rather than relying on it alone.
        fac = _match_facility(r["title"], f"{r['org']}, Utah", facilities)
        rows.append(_build_row("Utah SciQuest", r["title"], r["org"], "UT", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']} contact={r['contact']}",
                                now, fac))
    _classify_and_route("utah", rows)


def run_bidnet(facilities):
    # Needs core.browser.new_page(stealth=True) - plain headless Playwright
    # gets a bare 403 on every URL here (confirmed: a real browser and a
    # real user's Chrome session both loaded it fine), so this is a
    # headless-fingerprint check, not a network block. See
    # core/browser.py and adapters/bidnet.py for what the stealth patch does.
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(bidnet.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # Unlike every other portal this session, BidNet's "org" field IS
        # already a clean full US state name (e.g. "Hawaii", "Colorado") -
        # no state-hint workaround needed, this is the one case where
        # match_facility's state-gating gets a reliable signal for free.
        fac = _match_facility(r["title"], r["org"], facilities)
        # status="Open" is a stated fact, not a guess: search() only ever
        # hits /solicitations/open-bids, so every row it returns is open at
        # scrape time. Passing "" here previously starved the classifier of
        # any status signal, leaving lifecycle to ride on the deadline text
        # alone - which was landing on UNCLEAR (and getting the row dropped
        # from both tabs) even for postings with a plainly future deadline.
        rows.append(_build_row("BidNet Direct", r["title"], r["org"], r["org"], "Open",
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("bidnet", rows)


def run_magic_ms(facilities):
    # Distinct from mississippi.py (the public ms.gov DFA bid search) - this
    # is MAGIC, Mississippi's SAP SRM portal, login-required. No keyword
    # search found on this grid (only Agency/Commodity/RFx-Number filters),
    # so unlike every other adapter this just fetches whatever's on the
    # default "eRFxs - All" view once, rather than looping over KEYWORDS.
    # See adapters/magic_ms.py for the known ~10-row lazy-load limitation
    # (real total was 124) and why titles here are opaque reference codes,
    # not descriptive text - the LLM will have very little to classify on
    # for most rows; Agency (e.g. "MS VETERANS AFFAIRS") is the stronger
    # signal here than title.
    with new_page(headless=True) as page:
        magic_ms.login(page)
        all_hits = magic_ms.fetch_open_rfxs(page)

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # Every posting on this portal is Mississippi state business - same
        # state-hint pattern as mississippi.py, since org (agency name)
        # alone doesn't reliably signal MS to match_facility's gating.
        fac = _match_facility(r["title"], f"{r['org']}, Mississippi", facilities)
        rows.append(_build_row("MAGIC (MS SRM)", r["title"], r["org"], "MS", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("magic_ms", rows)


def run_bidnet_mitn(facilities):
    # Michigan's LOCAL-government layer: the MITN purchasing group on BidNet
    # Direct (~200 Michigan municipalities/counties, incl. the counties that
    # run our 29 MI medical care facilities). run_michigan below is the
    # STATE board (SIGMA VSS) and never sees county business, and the
    # national run_bidnet's 3-page cap crowds Michigan rows out (confirmed
    # 2026-09-18: 0 Michigan rows on page 1 of a national "nursing" search
    # vs 2 on the MITN-scoped one). Same stealth requirement as run_bidnet.
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(bidnet.search(page, kw, group="mitn"))

    deduped = _dedupe(all_hits, key="link")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # BidNet rows carry only the state ("Michigan") as org - no agency
        # name - so the facility matcher only gets a state gate here; fine,
        # a title naming the facility is what would match anyway.
        fac = _match_facility(r["title"], "Michigan", facilities)
        rows.append(_build_row("BidNet MITN (MI local)", r["title"], r["org"], "MI", "",
                                r["close_date"], r["link"], f"ref={r['ref']} group=mitn", now, fac))
    _classify_and_route("bidnet_mitn", rows)


def _run_bidnet_group(portal_label, group, state_abbrev, state_name, facilities):
    """Shared body for the per-state BidNet purchasing-group portals below -
    same local-government pattern as MITN (see run_bidnet_mitn), added for
    CA/OH/WA/VA after a Salesforce query showed these four states hold
    1,200+ of our Long-Term-Care facilities with zero scraper coverage
    (every other large-facility-count state already has either its own
    state-portal adapter or an existing BidNet group). Confirmed live and
    returning real results for each of the four groups before wiring in
    (2026-10-16): california=9, ohio=3, washington=0 ("nursing" specifically,
    but staffing/health/medical all returned hits - the group page itself is
    real, just thin on nursing-worded postings at check time), virginia=5."""
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(bidnet.search(page, kw, group=group))

    deduped = _dedupe(all_hits, key="link")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # Same as MITN: BidNet rows carry only the state name as org, no
        # agency - the facility matcher gets a state gate only.
        fac = _match_facility(r["title"], state_name, facilities)
        rows.append(_build_row(portal_label, r["title"], r["org"], state_abbrev, "",
                                r["close_date"], r["link"], f"ref={r['ref']} group={group}", now, fac))
    _classify_and_route(f"bidnet_{group}", rows)


def run_bidnet_california(facilities):
    _run_bidnet_group("BidNet California (local)", "california", "CA", "California", facilities)


def run_bidnet_ohio(facilities):
    _run_bidnet_group("BidNet Ohio (local)", "ohio", "OH", "Ohio", facilities)


def run_bidnet_washington(facilities):
    _run_bidnet_group("BidNet Washington (local)", "washington", "WA", "Washington", facilities)


def run_bidnet_virginia(facilities):
    _run_bidnet_group("BidNet Virginia (local)", "virginia", "VA", "Virginia", facilities)


# Added 2026-10-19: the next-biggest uncovered-by-anything states by LTC
# facility count (MN=109, IA=99, KS=78, NC=66, CT=58, FL=47, GA=45, MT=39 -
# none of these appear in Mike's credentialed-portal inventory at all, so
# unlike CA/OH/WA/VA there's no dedicated-adapter path available either;
# BidNet's local-gov purchasing groups are the only realistic coverage for
# now). Confirmed live before wiring in (staffing/nursing/health/medical
# combined, max_pages=1 each): georgia=42, minnesota=35, connecticut=23,
# iowa=18, montana=16, kansas=11, florida=3, north_carolina=0 (page itself
# confirmed real via page title - just thin on these keywords at check
# time). North Carolina's actual BidNet slug is "north-carolina", not the
# "northcarolina"/"nc" forms that seemed more obvious - confirmed by title
# check since those variants 404.
def run_bidnet_georgia(facilities):
    _run_bidnet_group("BidNet Georgia (local)", "georgia", "GA", "Georgia", facilities)


def run_bidnet_minnesota(facilities):
    _run_bidnet_group("BidNet Minnesota (local)", "minnesota", "MN", "Minnesota", facilities)


def run_bidnet_connecticut(facilities):
    _run_bidnet_group("BidNet Connecticut (local)", "connecticut", "CT", "Connecticut", facilities)


def run_bidnet_iowa(facilities):
    _run_bidnet_group("BidNet Iowa (local)", "iowa", "IA", "Iowa", facilities)


def run_bidnet_montana(facilities):
    _run_bidnet_group("BidNet Montana (local)", "montana", "MT", "Montana", facilities)


def run_bidnet_kansas(facilities):
    _run_bidnet_group("BidNet Kansas (local)", "kansas", "KS", "Kansas", facilities)


def run_bidnet_florida(facilities):
    _run_bidnet_group("BidNet Florida (local)", "florida", "FL", "Florida", facilities)


def run_bidnet_north_carolina(facilities):
    _run_bidnet_group("BidNet North Carolina (local)", "north-carolina", "NC", "North Carolina", facilities)


def run_movers_mo(facilities):
    # Missouri STATE board - MissouriBUYS powered by MOVERS (Oracle Fusion).
    # No browser: the public "Solicitation Abstracts" listing is backed by
    # an anonymous JSON REST endpoint (see adapters/movers_mo.py), so this
    # is plain HTTP - pulls every abstract (all statuses, so the LLM sees
    # closed/awarded history too) plus per-stem title searches that reach
    # past the listing's 500-row cap, then keyword-filters locally with the
    # same word-boundary rule as run_bonfire. Supersedes the old "Missouri
    # has no public bid content" note - that was the retired WebProcure
    # system. County-run nursing homes (all 17 MO facilities) don't post
    # here; see run_instantmarkets_mo for that layer.
    session = requests.Session()
    abstracts = movers_mo.fetch_everything_relevant(session)
    print(f"[movers_mo] {len(abstracts)} abstracts pulled from the MOVERS board")
    all_hits, seen = [], set()
    for kw in KEYWORDS + ["nurse", "nurses"]:
        for r in movers_mo.filter_keyword(abstracts, kw):
            key = r["ref"] or r["title"]
            if key in seen:
                continue
            seen.add(key)
            all_hits.append(r)

    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in all_hits:
        # org is the requesting department parsed from the synopsis when
        # present ("Issued on behalf of: Department of Mental Health"),
        # else the purchasing BU - neither carries a place name, so append
        # an explicit state hint for the matcher, same as every other
        # single-state portal here.
        fac = _match_facility(r["title"], f"{r['org']}, Missouri", facilities)
        note = f"ref={r['ref']} type={r['type']} bu={r['bu']} posted={r['posted']} " \
               f"attachments={r['attachments']}. {r['description']}"
        rows.append(_build_row("MissouriBUYS MOVERS", r["title"], r["org"], "MO", r["status"],
                                r["close_date"], r["link"], note.strip(), now, fac))
    _classify_and_route("movers_mo", rows)


def run_instantmarkets_mo(facilities):
    # Missouri's LOCAL-government layer (plus Missouri-sited federal/VA
    # postings): the state-sponsored InstantMarkets "Bid Locator" widget,
    # read by intercepting its search XHR - see adapters/instantmarkets_mo.py
    # for why it can't be a plain requests call like run_movers_mo. This is
    # the only source that can surface the 17 county-run MO nursing homes'
    # own solicitations. Active postings only for now (see the adapter's
    # status-filter note).
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(instantmarkets_mo.search(page, kw))

    deduped = _dedupe(all_hits, key="link")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # org is the posting entity ("Boone County", "City of Rolla",
        # "VETERANS AFFAIRS, DEPARTMENT OF") - a real jurisdiction name for
        # the matcher, plus an explicit state hint since the entity name
        # alone rarely says "Missouri".
        fac = _match_facility(r["title"], f"{r['org']}, Missouri", facilities)
        note = (f"ref={r['ref']} type={r['customer_type']} city={r['city']}. "
                f"{r['description']}").strip()
        rows.append(_build_row("MO Bid Locator (InstantMarkets)", r["title"], r["org"], "MO",
                                r["status"], r["close_date"], r["link"], note, now, fac))
    _classify_and_route("instantmarkets_mo", rows)


def run_michigan(facilities):
    # Needs core.browser.new_page(stealth=True) - plain headless Playwright
    # gets a WAF "Web Page Blocked!" page on every URL here, same
    # headless-fingerprint issue as BidNet Direct, not a real login wall
    # (this "View Published Solicitations" search is fully public). Caps
    # at ~20 results per keyword (the visible page size - didn't find a
    # working page-size-increase control, a "Show Me" dropdown was the only
    # <select> found and it's a different filter).
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(michigan.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        # org here is a state department name (e.g. "Transportation"), no
        # place name in it - same failure mode as several other portals.
        # Append an explicit "Michigan" state hint.
        fac = _match_facility(r["title"], f"{r['org']}, Michigan", facilities)
        rows.append(_build_row("Michigan SIGMA VSS", r["title"], r["org"], "MI", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("michigan", rows)


def run_kentucky_vss(facilities):
    # Same CGI Advantage VSS platform as Michigan (see adapters/advantage_vss.py)
    # - public, no login, same stealth requirement.
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(kentucky_vss.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        fac = _match_facility(r["title"], f"{r['org']}, Kentucky", facilities)
        rows.append(_build_row("Kentucky VSS", r["title"], r["org"], "KY", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("kentucky_vss", rows)


def run_colorado_vss(facilities):
    # Same CGI Advantage VSS platform as Michigan (see adapters/advantage_vss.py)
    # - public, no login, same stealth requirement.
    all_hits = []
    with new_page(headless=True, stealth=True) as page:
        for kw in KEYWORDS:
            all_hits.extend(colorado_vss.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in deduped:
        fac = _match_facility(r["title"], f"{r['org']}, Colorado", facilities)
        rows.append(_build_row("Colorado CGI Advantage VSS", r["title"], r["org"], "CO", r["status"],
                                r["close_date"], r["link"], f"ref={r['ref']}", now, fac))
    _classify_and_route("colorado_vss", rows)


def run_nh_dhhs(facilities):
    # Needs core.browser.new_page(headless=False) - this site sits behind an
    # Akamai WAF that blocks plain headless AND headless+stealth=True
    # consistently (confirmed across multiple attempts) - unlike BidNet/
    # Michigan's simpler headless-JS-property checks, Akamai typically also
    # fingerprints at the network/TLS layer, which JS patches can't touch.
    # Running an actual (non-headless) browser window cleared it immediately
    # with no stealth flags needed - this DOES mean a visible Chrome window
    # opens on-screen while this one runs.
    #
    # No working keyword search found on this page, so this pulls all ~49
    # listings once (see adapters/nh_dhhs.py) and relies entirely on the LLM
    # relevance call to filter - which is actually a good fit here, since
    # this is the one adapter that captures a REAL description, not just a
    # title, so the LLM has real content to judge instead of guessing from
    # a bare title like most other portals.
    with new_page(headless=False) as page:
        all_hits = nh_dhhs.fetch_all(page)

    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in all_hits:
        # No distinct agency field scraped (the specific Division is named
        # inside the description text, not a separate column) - use a
        # constant org and let the real description carry the detail.
        fac = _match_facility(r["title"], "New Hampshire Department of Health and Human Services", facilities)
        # The scraped "Date" is this RFP's POSTING date, not a closing
        # deadline - confirmed by several entries showing dates already
        # months in the past despite the page itself being titled "Open
        # Requests for Proposals". Passing it as close_date would make the
        # LLM's deadline-passed fact wrongly read as "deadline has passed"
        # on genuinely still-open postings, so it goes in Notes as context
        # instead, and Closing Date is left blank (-> "no deadline given").
        note = f"Posted: {r['close_date']}. {r['description']}"
        rows.append(_build_row("NH DHHS", r["title"], "NH DHHS", "NH", "Open",
                                "", r["link"], note, now, fac))
    _classify_and_route("nh_dhhs", rows)


def run_nhprocure(facilities):
    # Needs headless=False - same Akamai-style WAF as nh_dhhs.py, same fix.
    # Fully public despite Mike's docx listing an NHProcure credential for
    # it - confirmed 2026-10-19.
    all_hits = []
    with new_page(headless=False) as page:
        for kw in KEYWORDS:
            all_hits.extend(nhprocure.search(page, kw))

    deduped = _dedupe(all_hits)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("NHProcure", r["title"], r["org"], "NH", r["status"], r["close_date"],
                   r["link"], f"ref={r['ref']}", now,
                   _match_facility(r["title"], f"{r['org']}, New Hampshire", facilities))
        for r in deduped
    ]
    _classify_and_route("nhprocure", rows)


def run_arkansas_osp(facilities):
    all_hits = []
    with new_page(headless=False) as page:
        for kw in KEYWORDS:
            all_hits.extend(arkansas_osp.search(page, kw))

    deduped = _dedupe(all_hits, key="link")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    rows = [
        _build_row("Arkansas OSP", r["title"], r["org"], "AR", r["status"], r["close_date"],
                   r["link"], f"posted={r['pub_date']} {r['description'][:150]}", now,
                   _match_facility(r["title"], f"{r['org']}, Arkansas", facilities))
        for r in deduped
    ]
    _classify_and_route("arkansas_osp", rows)


# Ordered by real facility coverage (confirmed by cross-referencing each
# portal's actual state/county reach against data/facilities.csv - see the
# 2026-09 coverage audit). Default `python3 run_all.py` (no args) runs them
# in this order, highest-coverage first.
#   bidnet       - national (all 50 states) - was blocked (see below for the
#     old note), fixed with core.browser.new_page(stealth=True) once a real
#     browser/real user session confirmed the site itself wasn't network-
#     blocked, only headless Chromium's fingerprint was. Its "org" field is
#     a clean full state name for free, unlike most other portals here -
#     no state-hint workaround needed for match_facility's gating. Given the
#     national scope, put first despite not yet having a confirmed per-
#     facility count the way the single-state portals below do.
# Facility coverage now only matters
# for informational tagging and the tracker tab - the "Scraper Results" tab
# itself is no longer gated by it, so a portal's value isn't capped by this
# number anymore (a hospital or new facility it surfaces counts just as much).
#   bidnet_mitn  - Michigan LOCAL governments via BidNet's MITN purchasing group
#                  (the counties running our 29 MI facilities) - see run_bidnet_mitn.
#                  `michigan` below is the STATE board and never sees county bids.
#   bidnet_california, bidnet_ohio, bidnet_washington, bidnet_virginia - same
#                  BidNet purchasing-group pattern as MITN, added 2026-10-16
#                  after a Salesforce query showed these 4 states hold 1,200+
#                  of our LTC facilities with no scraper coverage at all -
#                  the single biggest coverage gap found so far. See
#                  _run_bidnet_group.
#   bidnet_georgia, bidnet_minnesota, bidnet_connecticut, bidnet_iowa,
#                  bidnet_montana, bidnet_kansas, bidnet_florida,
#                  bidnet_north_carolina - same pattern, added 2026-10-19 for
#                  the next-biggest uncovered-by-anything states (none of
#                  these 8 appear in Mike's credentialed inventory at all).
#   movers_mo    - Missouri STATE board (MissouriBUYS powered by MOVERS), API-
#                  backed, no browser. 17 MO facilities are county-run, so...
#   instantmarkets_mo - ...Missouri's state-sponsored Bid Locator (InstantMarkets)
#                  covers the county/city layer + MO-sited VA postings.
#   indiana      - up to 86 (all IN facilities; unconfirmed whether county-run
#                  SNF procurement even lands on this state-level board)
#   wisconsin    - 30 (confirmed statewide reach)
#   pa_emarketplace - 14 (PA, statewide) - the actual PA state procurement
#                  search (emarketplace.state.pa.us/Search.aspx), fully public,
#                  no login. Distinct from Mike's docx "PA Supplier Portal"/
#                  "JAGGAER" credentials, which are for an MSP relationship
#                  (22nd Century Technologies), not this RFP board.
#   mississippi  - 13 (confirmed statewide reach - public ms.gov bid search,
#                  no login required; see note in adapters/mississippi.py -
#                  MAGIC, the credentialed portal in Mike's docx, has a broken
#                  TLS cert and is a vendor-registration tool, not a search).
#   bidbuy       - 12 (IL, statewide) - same Ivalua platform as njstart, public
#                  advancedSearchBid.xhtml search works identically, no login
#   demandstar   - national network, unknown total reach; 1 confirmed (DuPage, IL -
#                  now redundant with bidbuy's IL statewide coverage, kept anyway
#                  since demandstar covers other states too)
#   bonfire      - 14 PA facilities reachable in principle (4 confirmed - the
#                  Kane sites in Allegheny County; the other 10 depend on
#                  whether their counties participate in PennBid, unconfirmed).
#                  Now overlaps with pa_emarketplace's statewide coverage above,
#                  but kept since Bonfire may surface county-run solicitations
#                  that never reach the state eMarketplace.
#   njstart      - 4 (NJ, statewide)
#   passaicbids  - 1 (Preakness Healthcare Center, a subset of njstart's NJ reach,
#                  but county-specific and more precise)
#   passportnyc  - 0 confirmed - all 14 NY facilities are upstate counties,
#                  none of the 5 NYC boroughs this portal covers. Kept for when
#                  that changes, but it's last for a reason.
#   utah         - 9 (statewide) - Jaggaer/SciQuest-based public event router
#                  (bids.sciquest.com), no login, no credential existed for
#                  this state in Mike's docx at all. Last in the order since
#                  it's the newest/least-tested adapter.
#
# Not yet built despite having credentials in Mike's docx:
#   - Tennessee Edison: login succeeds but lands the account on an EMPLOYEE
#     self-service page with "Authorization Error" - couldn't find the correct
#     SUPPLIER-role bid-search component path.
#   - Arkansas (AASIS/ARBuy): no working single portal found - real OSP
#     solicitations run through SAP Ariba Discovery, a SPA with individual
#     ANONYMOUS-access event-preview links, not a keyword-searchable list.
#   - Missouri: RESOLVED 2026-09-18 - the old note here ("no public bid content
#     at all, both systems require a login") was true of the retired
#     WebProcure system only. The current MissouriBUYS-powered-by-MOVERS
#     abstract board is public and API-backed -> run_movers_mo. Still a
#     STATE-only board; the 17 county nursing homes are covered via the
#     state-sponsored InstantMarkets Bid Locator (run_instantmarkets_mo).
#
# Built and tested working, but NOT wired into PORTALS below - 0 of our 314
# facilities are in these states, so they'd only ever produce "No Matched
# Facility" rows for the tracker (though they'd still work for the relevant
# pipeline now that it isn't facility-gated):
#   - adapters/commbuys.py (Massachusetts) - same Ivalua platform, public, no login
#   - adapters/oregonbuys.py (Oregon) - same Ivalua platform, public, no login
#   - Hawaii HANDS (hands.ehawaii.gov) - confirmed reachable but not yet built.
PORTALS = {
    "bidnet": run_bidnet,
    "indiana": run_indiana,
    "wisconsin": run_wisconsin,
    "michigan": run_michigan,
    "bidnet_mitn": run_bidnet_mitn,
    "bidnet_california": run_bidnet_california,
    "bidnet_ohio": run_bidnet_ohio,
    "bidnet_washington": run_bidnet_washington,
    "bidnet_virginia": run_bidnet_virginia,
    "bidnet_georgia": run_bidnet_georgia,
    "bidnet_minnesota": run_bidnet_minnesota,
    "bidnet_connecticut": run_bidnet_connecticut,
    "bidnet_iowa": run_bidnet_iowa,
    "bidnet_montana": run_bidnet_montana,
    "bidnet_kansas": run_bidnet_kansas,
    "bidnet_florida": run_bidnet_florida,
    "bidnet_north_carolina": run_bidnet_north_carolina,
    "cal_eprocure": run_cal_eprocure,
    "kentucky_vss": run_kentucky_vss,
    "colorado_vss": run_colorado_vss,
    "nhprocure": run_nhprocure,
    "arkansas_osp": run_arkansas_osp,
    "nh_dhhs": run_nh_dhhs,
    "pa_emarketplace": run_pa_emarketplace,
    "mississippi": run_mississippi,
    "movers_mo": run_movers_mo,
    "instantmarkets_mo": run_instantmarkets_mo,
    "magic_ms": run_magic_ms,
    "bidbuy": run_bidbuy,
    "demandstar": run_demandstar,
    "bonfire": run_bonfire,
    "njstart": run_njstart,
    "passaicbids": run_passaicbids,
    "scbo": run_scbo,
    "passportnyc": run_passportnyc,
    "utah": run_utah,
    "commbuys": run_commbuys,
    "oregonbuys": run_oregonbuys,
    "emma_md": run_emma_md,
    "nyscr": run_nyscr,
    "edison_tn": run_edison_tn,
    "arbuy": run_arbuy,
}


def main():
    global USE_LLM, USE_LLM_FACILITY_MATCH, USE_TEST_PROMPT
    args = sys.argv[1:]
    if "--no-llm" in args:
        USE_LLM = False
        args = [a for a in args if a != "--no-llm"]
    if "--llm-facility-match" in args:
        USE_LLM_FACILITY_MATCH = True
        args = [a for a in args if a != "--llm-facility-match"]
    if "--test-prompt" in args:
        USE_TEST_PROMPT = True
        args = [a for a in args if a != "--test-prompt"]

    requested = args or list(PORTALS.keys())
    unknown = [p for p in requested if p not in PORTALS]
    if unknown:
        print(f"Unknown portal(s): {unknown}. Choices: {list(PORTALS.keys())} "
              f"(plus --no-llm, --llm-facility-match, --test-prompt)")
        sys.exit(1)

    if (USE_LLM or USE_LLM_FACILITY_MATCH) and not os.environ.get("OPENAI_API_KEY"):
        print("No OPENAI_API_KEY found. Neither tab writes anything without it now - the LLM is "
              "the only way to determine relevance AND open-vs-closed status (no more hardcoded "
              "per-portal status parsing, no facility-match fallback). Set OPENAI_API_KEY to "
              "enable output, or pass --no-llm to silence this warning.")
        USE_LLM = False
        USE_LLM_FACILITY_MATCH = False

    facilities = load_facilities()
    for name in requested:
        print(f"\n=== {name} ===")
        try:
            PORTALS[name](facilities)
        except Exception as e:
            print(f"[{name}] FAILED: {type(e).__name__}: {e}")

    if USE_LLM_FACILITY_MATCH:
        s = _llm_facility_match_stats
        print(f"\n[llm-facility-match] {s['attempted']} attempted, {s['matched']} matched, "
              f"{s['errors']} errors (only ran on rows the deterministic matcher missed).")

    print("\nDone. Check 'Scraper Results' and 'Awarded-Historical RFPs' in the Google Sheet.")


if __name__ == "__main__":
    main()
