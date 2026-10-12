# Lightweight, self-contained image for the dashboard - no Playwright/
# Chromium/Xvfb needed here, unlike the scraper's own Dockerfile, since this
# service only reads from the Google Sheet. Deploys with this directory
# (web/) as its own build root, independent of the rest of the repo.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
