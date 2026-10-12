"""Adapter for NJSTART (njstart.gov) - New Jersey's statewide procurement
system. Bid search is fully public, no login required. Thin wrapper around
the shared Ivalua-platform logic in adapters/ivalua.py - see that file for
the column-mapping/status-semantics notes shared across all four states on
this platform (NJ, IL/BidBuy, MA/COMMBUYS, OR/OregonBuys)."""
from adapters import ivalua

BASE_URL = "https://www.njstart.gov"


def search(page, keyword):
    return ivalua.search(page, BASE_URL, keyword)
