"""Loads portal credentials from the local secrets.json (gitignored, chmod 600)."""
import json
from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent.parent / "secrets.json"


def load_all():
    with open(SECRETS_PATH) as f:
        return json.load(f)


def get_portal(name_substring):
    """Case-insensitive substring match on the 'Portal' field."""
    entries = load_all()
    matches = [e for e in entries if name_substring.lower() in e.get("Portal", "").lower()]
    if not matches:
        raise KeyError(f"No portal matching {name_substring!r} in secrets.json")
    return matches
