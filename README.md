# Software Job Radar

A local monitor for fresh US software, ML, and AI jobs. It checks direct company career feeds and common ATS APIs, filters for US roles and postings asking for no more than 3.5 years of experience, deduplicates reposts, and sends grouped Telegram alerts.

## Current behavior

- The external cron-job.org scheduler refreshes every 5 minutes.
- Uses a 72-hour freshness window.
- Prefers direct company career feeds, including Greenhouse, Lever, Ashby,
  SmartRecruiters, Amazon Jobs, Apple Jobs, Google Careers, Microsoft Careers,
  Workday, Eightfold, AMD, Goldman Sachs, JPMorgan Chase, Mastercard, and
  Oracle career sites.
- Accepts US locations and explicit US-remote roles; rejects ambiguous worldwide/remote listings.
- Accepts roles whose maximum stated required experience is 3.5 years or less.
- Excludes titles containing `senior` or `manager`, along with staff, principal, director, and architect titles.
- Stores first-seen state in SQLite so a role is not alerted twice, even when a URL changes.
- Sends one compact Telegram batch per scan.

## Setup

1. Create the local environment file:

   ```sh
   cp .env.example .env
   ```

2. Put the Telegram bot token from BotFather in `.env`. Do not commit `.env` or share the token.

3. Open the bot in Telegram and send `/start`. The monitor discovers every private chat that sends `/start`, so multiple subscribers can receive the same alerts. You may also set `TELEGRAM_CHAT_ID` or comma-separated `TELEGRAM_CHAT_IDS` explicitly.

4. Run a test scan:

   ```sh
   python3 -m job_radar --once --dry-run
   ```

5. Run the monitor continuously:

   ```sh
   python3 -m job_radar
   ```

The first run sends only postings published in the last 72 hours. Later runs send only newly discovered postings. Add or edit sources in `config/companies.json`.

## Source format

Each entry has a company name, source type, and public board identifier. Supported source values are `greenhouse`, `lever`, `ashby`, `smartrecruiters`, `amazon`, `apple`, `google`, `microsoft`, `eightfold`, `amd`, `workday`, `goldman`, `jpmorgan`, and `oracle`.

The source roster is intentionally editable. Public career systems change their board identifiers occasionally, so an unavailable source is logged and skipped without stopping the rest of the scan.

The current roster has 63 active sources. Meta, Uber, ServiceNow, Walmart, and Dell are recorded but disabled until their official portals can be queried without a session-dependent or anti-bot workaround; they are not silently replaced with stale aggregator listings.

## GitHub Actions deployment

`.github/workflows/job-radar.yml` is triggered by cron-job.org every 5 minutes. It stores the SQLite state as a short-lived GitHub Actions artifact so deduplication survives between runners without committing the database to the repository.

Add these repository Actions secrets before enabling the workflow:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

`TELEGRAM_CHAT_ID` is optional for discovery, but keeping your existing value is fine. Friends only need to open the bot and send `/start`; they will receive future alerts on the next successful scan.
