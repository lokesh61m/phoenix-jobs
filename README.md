# Software Job Radar

A local monitor for fresh US software, ML, and AI jobs. It checks direct company career feeds and common ATS APIs, filters for US roles and postings asking for no more than 3.5 years of experience, deduplicates reposts, and sends grouped Telegram alerts.

## Current behavior

- The external cron-job.org scheduler refreshes every 5 minutes.
- Uses a 24-hour freshness window.
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

The first run sends only postings published in the last 24 hours. Later runs send only newly discovered postings. Add or edit sources in `config/companies.json`.

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

## Separate Tsenta shortlist bot

The second Telegram feed is for manually pasting roles into Tsenta; it does not
connect to Tsenta or start applications. It is separate from the existing alert
bot. It uses the 29 companies in `config/tsenta_companies.json`, a 24-hour
freshness window, US/US-remote roles, no senior/manager titles, and at most
**3 required years** of experience. Explicit no-sponsorship roles are excluded;
silent postings are labeled **unverified**. A role needs at least two concrete
skills matching a private profile-skills list before it is sent.

Each qualifying role is sent once through the second bot as plain Telegram
messages with the full job description, source link, sponsorship label, and
matched skills. Long descriptions are split into numbered messages.
The GitHub Actions workflow processes up to 10 new roles per five-minute run;
remaining roles are picked up on later runs. A separate SQLite notification
table deduplicates by source job ID and fingerprint. Failed sends are retried on
the next run. Telegram's existing broad alert feed is unchanged.

To enable the second feed:

1. Send `/start` to `@tsenta_phoenix_bot` from the Telegram account that should
   receive the shortlist.
2. In GitHub repository Settings → Secrets and variables → Actions, add
   `TSENTA_TELEGRAM_BOT_TOKEN` with the new BotFather token. Never paste it into
   chat or commit it to the repository.
3. Add `TSENTA_PROFILE_SKILLS` as another Actions secret. Use a short
   space-separated list of your technical skills, not your full resume. The
   runner reads this at scan time and never writes it to the public repository.

Both secrets are required; if either is absent, the second feed skips without
affecting the original bot. For a local dry-run, put the values in the ignored
`.env` file and run `python3 -m job_radar.tsenta_telegram --dry-run`. Do not put
real tokens directly in shell history. No Tsenta credits are used by this feed.
