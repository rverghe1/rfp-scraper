# Deploying to Railway

## What's set up

- `Dockerfile` - built on Playwright's official image (Chromium pre-installed,
  version-matched to `requirements.txt`), plus Xvfb for `adapters/nh_dhhs.py`'s
  `headless=False` requirement (that site's Akamai WAF blocks headless
  Chromium even with stealth patches - a real, visible browser window,
  even a virtual one, clears it fine).
- `entrypoint.sh` - starts the virtual display, writes `secrets.json` and the
  Google service account key from env vars (see below) to the paths the
  code already expects, then runs the container's command.
- `railway.json` - Dockerfile build, and a placeholder cron schedule
  (`0 13 * * 1-5` - weekdays 1pm UTC / 9am ET during standard time, 8am ET
  during daylight saving - **confirm/change this time before relying on
  it**, I picked a plausible weekday-morning default, not a stated
  preference).

## Required environment variables (set in Railway's dashboard, not committed)

| Variable | What it is |
|---|---|
| `OPENAI_API_KEY` | Same key you use locally - needed for the LLM classification, or nothing gets written to either sheet tab. |
| `SECRETS_JSON` | The **entire raw content** of your local `secrets.json` file, pasted as one value. Get it with: `cat secrets.json \| pbcopy` (Mac) and paste into Railway's variable editor. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The entire raw content of your Google service account key file (currently at `/Users/rohanverghese/Desktop/chow-scraper/service_account_key.json`). Same copy/paste approach: `cat /Users/rohanverghese/Desktop/chow-scraper/service_account_key.json \| pbcopy`. |

Both secret files are gitignored and were never meant to enter the image or
a git history - they're materialized at container *start*, from these env
vars, by `entrypoint.sh`.

## Deploying

1. `railway login`, then from this directory: `railway init` (or link to an
   existing Railway project if one's already been created for this).
2. Set the three env vars above in the Railway dashboard (Variables tab).
3. `railway up` to build and deploy from the Dockerfile.
4. Confirm the cron schedule in the Railway dashboard's Settings ->
   Cron Schedule (or edit `railway.json`'s `cronSchedule` and redeploy).

## The dashboard (`web/`) - already deployed

A separate, read-only FastAPI app that browses the same Google Sheet
(`web/app.py`, static HTML/JS in `web/static/`) - deployed as a **second
Railway service ("dashboard") in the same project**, since it needs no
cron and none of the Playwright/Chromium/Xvfb weight the scraper's image
carries.

Live at: https://dashboard-production-3ffe.up.railway.app

Unlike the scraper, `web/` is fully self-contained (it duplicates the
handful of Google Sheets constants it needs from `core/sheets.py` rather
than importing across the directory boundary - see the comment at the top
of `web/app.py`), so it deploys with `web/` as its own build root:

```bash
railway up web --path-as-root --service dashboard
```

That uploads `web/`'s contents as the archive root, so Railway's
Dockerfile auto-detection picks up `web/Dockerfile` (via `web/railway.json`)
without needing a repo-root config-path setting or a GitHub connection.

Setup notes for anyone recreating this from scratch:
- The service was created empty first: `railway add --service dashboard`.
- Only one env var is needed: `GOOGLE_SERVICE_ACCOUNT_JSON` (same value as
  the scraper service's). Nothing else - this app never writes to the
  sheet or logs into any portal.
- Public domain: `railway domain --service dashboard`.
- No auth is in front of this yet - anyone with the URL above can view it.
  Fine while it's just you; add at least a shared-password check before
  sharing the link with teammates.

## What I have NOT done yet, worth deciding before/after first deploy

- **Not tested end-to-end in a real container** - I don't have a Railway
  account or a way to spin up a container from this environment, so
  everything above is built and reasoned through carefully, but the first
  real deploy is the actual test. Watch the first run's logs closely,
  especially for the Xvfb/nh_dhhs path and the secrets-file materialization.
- **No alerting on failure** - if a run errors out or a portal's login
  starts failing (accounts occasionally need password rotations, sites
  occasionally change their DOM), nothing currently notifies you. Worth
  adding a Slack webhook or similar on non-zero exit / high error count
  before trusting this to run fully unattended for stretches of time.
- **Cron schedule is a guess** - confirm how often you actually want this
  to run (daily? a few times a week?) and at what time, then update
  `railway.json`.
- **Cost** - LLM classification runs on every scraped row across 16
  sources; a scheduled run means recurring OpenAI spend rather than the
  ad hoc spend from running manually. Worth watching the first few
  scheduled runs' actual token cost before locking in a daily cadence.
