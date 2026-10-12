# Playwright's official image ships Chromium + all its system deps
# pre-installed and version-matched to the playwright pip package below -
# far less fragile than installing Playwright's browser deps by hand on a
# generic base image.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

# xvfb: a virtual display. adapters/nh_dhhs.py needs a REAL (non-headless)
# browser window - that site's Akamai WAF blocks headless Chromium even
# with stealth patches (confirmed: TLS/network-level fingerprinting, not
# just JS-visible properties, so no JS patch can fix it), but a genuine
# browser window - even a virtual, headless-server one via Xvfb - clears it
# fine. Every other adapter runs plain headless and doesn't need the
# display, but they don't mind one being present either.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers are already in the base image, but this is a no-op safety net in
# case the pinned playwright version in requirements.txt ever drifts from
# the base image's tag above.
RUN playwright install chromium

COPY . .

# See entrypoint.sh: starts Xvfb, waits for it, then runs whatever command
# Railway's cron/service config passes in (normally `python3 run_all.py`).
ENTRYPOINT ["./entrypoint.sh"]
CMD ["python3", "run_all.py"]
