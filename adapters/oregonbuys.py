"""Adapter for OregonBuys (oregonbuys.gov) - Oregon's statewide procurement
system. Same underlying Ivalua platform as NJSTART - public, no login
required, despite Mike's docx listing a login credential. Thin wrapper
around the shared logic in adapters/ivalua.py."""
from adapters import ivalua

BASE_URL = "https://oregonbuys.gov"


def search(page, keyword):
    return ivalua.search(page, BASE_URL, keyword)
