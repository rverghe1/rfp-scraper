"""Matches scraped RFP postings against the 314-facility county-owned SNF list.

v2: token-overlap matching alone produced false positives (e.g. a posting
about "Wildlife Management" matched to "WASHINGTON CO EXTENDED CARE FACILITY"
purely because both texts happened to share 2 common short words). Fixed by:
  1. A much broader stopword list (drops generic institutional/procurement words).
  2. IDF filtering: a token only counts as a match signal if it's rare across
     the facility list (appears in <=2 facility names) - i.e. it has to be
     distinctive, not just non-stopword.
  3. A state gate: if the posting text names a US state, it must match the
     facility's state, or the match is rejected outright.
"""
import csv
import re
from collections import Counter
from pathlib import Path

FACILITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "facilities.csv"

_STOPWORDS = {
    "the", "of", "and", "for", "to", "a", "an", "in", "on", "at", "by", "or",
    "center", "centre", "centers", "centres", "home", "homes", "county",
    "care", "nursing", "health", "healthcare", "rehabilitation", "rehab",
    "facility", "facilities", "medical", "manor", "campus", "campuses",
    "living", "village", "community", "communities", "co", "district",
    "department", "services", "service", "program", "programs", "special",
    "support", "staffing", "staff", "temporary", "long", "term", "skilled",
    "extended", "regional", "public", "state", "county's", "office", "human",
    "social", "management", "administration", "committee", "board", "unit",
    "units", "systems", "system", "agency", "agencies", "authority",
    "corporation", "county,", "request", "proposals", "proposal", "rfp",
    "rfq", "rfi", "bid", "quote", "engineering", "construction",
    # Common generic descriptor/place words that happen to be rare in this
    # small 314-facility corpus (df==1 or 2) purely by chance, not because
    # they actually identify a specific facility.
    "white", "green", "gold", "golden", "park", "pine", "lake", "hill",
    "hills", "view", "valley", "spring", "springs", "north", "south",
    "east", "west", "new", "grand", "grove", "ridge", "creek", "river",
    "meadow", "meadows", "brook", "wood", "woods", "field", "fields",
    "garden", "gardens", "haven", "point", "place", "way", "trail",
    "san", "santa", "saint", "fort", "mount", "mt", "falls", "manor's",
    "lake", "lakes", "university", "universities",
    # Generic clinical/service descriptors - real words that describe a
    # *type* of care, not a specific facility's identity, but can be rare
    # enough in this small corpus to otherwise look "distinctive."
    "geriatric", "geriatrics", "psychiatric", "pediatric", "behavioral",
    "therapeutic", "adolescent", "veterans", "veteran",
}

_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _normalize(name):
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    tokens = [t for t in name.split() if t and t not in _STOPWORDS and len(t) >= 3]
    return tokens


def load_facilities():
    with open(FACILITIES_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for r in rows:
        r["_tokens"] = set(_normalize(r["NAME"]))

    # Document frequency per token across all facility names, for IDF filtering.
    df = Counter()
    for r in rows:
        for t in r["_tokens"]:
            df[t] += 1
    for r in rows:
        r["_tokens"] = r["_tokens"]  # unchanged; df is used at match time
    return rows, df


def detect_state(text, org_text=""):
    """org_text (usually just a jurisdiction name, e.g. 'Montgomery County PA')
    gets a looser check: a standalone unambiguous 2-letter abbrev anywhere in
    it counts, not just comma-prefixed - short place-name strings rarely
    contain a stray capital 2-letter word that isn't the actual state."""
    text_l = text.lower()
    m = re.search(r",\s*([A-Z]{2})\b", text)
    if m and m.group(1) in _STATE_NAMES.values():
        return m.group(1)
    for name, abbrev in _STATE_NAMES.items():
        if name in text_l:
            return abbrev
    if org_text:
        # org_text is a short jurisdiction string (e.g. "Montgomery County PA",
        # "City of X, TX"), not a full sentence, so collision risk with common
        # words is low - trust any standalone real state abbreviation here.
        for word in re.findall(r"\b[A-Z]{2}\b", org_text):
            if word in _STATE_NAMES.values():
                return word
    return None


def match_facility(posting_title, posting_org, facilities_and_df):
    """facilities_and_df: the (facilities, df) tuple from load_facilities().
    Returns the best-matching facility dict, or None.
    """
    facilities, df = facilities_and_df
    full_text = f"{posting_title} {posting_org}"
    text_tokens = set(_normalize(full_text))
    if not text_tokens:
        return None

    posting_state = detect_state(full_text, org_text=posting_org)

    best, best_score, best_overlap = None, 0, 0
    for fac in facilities:
        overlap = text_tokens & fac["_tokens"]
        if not overlap:
            continue
        # Only distinctive tokens (rare across the facility list) count toward score.
        distinctive = [t for t in overlap if df[t] <= 2]
        if not distinctive:
            continue
        if posting_state and fac["STATE"] != posting_state:
            continue
        score = 0
        for t in distinctive:
            if df[t] == 1:
                score += 3       # unique to exactly one facility name - very strong
            elif len(t) >= 6:
                score += 2       # rare (df<=2) and a real word, not a short fragment
            else:
                score += 1
        # Tiebreak on total raw overlap size (including non-distinctive tokens
        # like "hospital"/"manor") so e.g. "Monroe Community Hospital" beats
        # "Monroe Manor" for a posting that also says "...Hospital".
        if score > best_score or (score == best_score and len(overlap) > best_overlap):
            best_score, best, best_overlap = score, fac, len(overlap)

    if best_score >= 2:
        return best
    return None
