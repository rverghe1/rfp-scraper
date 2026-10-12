"""Shared Playwright browser context helper."""
from contextlib import contextmanager
from playwright.sync_api import sync_playwright

# Opt-in per-adapter (stealth=True), not a global default - only needed for
# sites that block plain headless Chromium specifically (confirmed on
# BidNet Direct: bare headless got a 403 on every URL, real Chrome and a
# real user's browser worked fine, so it's a headless-fingerprint check,
# not a network/IP block). Patches the handful of properties headless
# Chromium exposes differently from a real browser (navigator.webdriver,
# missing plugins/languages, no window.chrome) - not a full anti-detection
# suite, just enough to clear that specific check. Don't reach for this by
# default: most sites don't need it, and there's no reason to look more
# "automated-but-hiding-it" than plain headless already does.
_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""
_STEALTH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


@contextmanager
def new_page(headless=True, slow_mo=0, stealth=False):
    with sync_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled"] if stealth else []
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo, args=launch_args)
        if stealth:
            context = browser.new_context(
                user_agent=_STEALTH_UA, viewport={"width": 1456, "height": 900}, locale="en-US",
            )
        else:
            context = browser.new_context()
        page = context.new_page()
        if stealth:
            page.add_init_script(_STEALTH_JS)
        try:
            yield page
        finally:
            browser.close()
