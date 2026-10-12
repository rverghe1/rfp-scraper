"""Adapter for BidBuy (bidbuy.illinois.gov) - Illinois's statewide
procurement system. Same underlying Ivalua platform as NJSTART - public,
no login required. Illinois has no dedicated portal credential in Mike's
docx; this public search is the real source for statewide IL bids. Thin
wrapper around the shared logic in adapters/ivalua.py."""
from adapters import ivalua

BASE_URL = "https://www.bidbuy.illinois.gov"


def search(page, keyword):
    return ivalua.search(page, BASE_URL, keyword)
