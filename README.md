# Software Job Radar

A local monitor for fresh US software, ML, and AI jobs. It checks direct company career feeds and common ATS APIs, filters for US roles and postings asking for no more than four years of experience, deduplicates reposts, and sends grouped Telegram alerts.

## Current behavior

- Refreshes every 20 minutes by default.
- Uses a 72-hour freshness window.
- Prefers direct company career feeds, including Greenhouse, Lever, Ashby,
  SmartRecruiters, Amazon Jobs, Apple Jobs, Google Careers, Microsoft Careers,
  Workday, Eightfold, and AMD career sites.
- Accepts US locations and explicit US-remote roles; rejects ambiguous worldwide/remote listings.
- Accepts roles whose minimum stated experience requirement is four years or less.
- Stores first-seen state in SQLite so a role is not alerted twice, even when a URL changes.
- Sends one compact Telegram batch per scan.

## Setup

1. Create the local environment file:

   ```sh
   cp .env.example .env
   ```

2. Put the Telegram bot token from BotFather in `.env`. Do not commit `.env` or share the token.

3. Open the bot in Telegram and send `/start`. The monitor can discover the numeric chat ID automatically from the bot's updates. You may also set `TELEGRAM_CHAT_ID` explicitly.

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

Each entry has a company name, source type, and public board identifier. Supported source values are `greenhouse`, `lever`, `ashby`, `smartrecruiters`, `amazon`, `apple`, `google`, `microsoft`, `eightfold`, `amd`, and `workday`.

The source roster is intentionally editable. Public career systems change their board identifiers occasionally, so an unavailable source is logged and skipped without stopping the rest of the scan.

The current roster has 59 active sources. Meta, Uber, ServiceNow, Walmart, and Dell are recorded but disabled until their official portals can be queried without a session-dependent or anti-bot workaround; they are not silently replaced with stale aggregator listings.

## GitHub Actions deployment

`.github/workflows/job-radar.yml` runs one scan every 15 minutes. It stores the SQLite state as a short-lived GitHub Actions artifact so deduplication survives between runners without committing the database to the repository.

Add these repository Actions secrets before enabling the workflow:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
