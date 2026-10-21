import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from job_radar import app
from job_radar.filtering import passes
from job_radar.models import Job
from job_radar.sources import (
    amd,
    ashby,
    goldman,
    jpmorgan,
    parse_date,
    search_page_count,
    workday_job_url,
)
from job_radar.storage import Store
from job_radar.telegram import TelegramError, format_job, resolve_chat_ids


def make_job(title="Software Engineer", description="Build backend services.", source_id="1"):
    return Job(
        company="Example",
        source_type="test",
        source_id=source_id,
        title=title,
        location="Seattle, WA, USA",
        url=f"https://example.com/jobs/{source_id}",
        apply_url=f"https://example.com/jobs/{source_id}",
        description=description,
    )


class FilteringTests(unittest.TestCase):
    def test_excludes_senior_and_manager_titles(self):
        self.assertFalse(passes(make_job(title="Senior Software Engineer")))
        self.assertFalse(passes(make_job(title="Software Engineering Manager")))

    def test_accepts_up_to_three_and_a_half_years(self):
        self.assertTrue(passes(make_job(description="Requires 3.5+ years of experience.")))
        self.assertTrue(passes(make_job(description="Requires 2-3 years of experience.")))

    def test_rejects_more_than_three_and_a_half_years(self):
        self.assertFalse(passes(make_job(description="Requires 4 years of experience.")))
        self.assertFalse(passes(make_job(description="Requires 3-5 years of experience.")))

    def test_allows_higher_preferred_experience(self):
        self.assertTrue(passes(make_job(description="5+ years preferred; 3 years required.")))

    def test_allows_higher_experience_in_a_preferred_section(self):
        self.assertTrue(
            passes(
                make_job(
                    description=(
                        "Required Qualifications: Bachelor's degree and 2+ years of experience. "
                        "Preferred Qualifications: Master's degree and 5+ years of experience."
                    )
                )
            )
        )


class SourceUrlTests(unittest.TestCase):
    def test_search_page_count_uses_api_total(self):
        payload = {"data": {"count": 474}}
        self.assertEqual(search_page_count(payload, configured_pages=3), 48)
        self.assertEqual(search_page_count(payload, configured_pages=3, maximum_pages=20), 20)
        self.assertEqual(search_page_count(payload, configured_pages=2, page_size=20), 24)

    def test_parse_date_handles_yesterday(self):
        self.assertIsNotNone(parse_date("Posted Yesterday"))

    def test_ashby_uses_stable_id_and_canonical_urls(self):
        payload = {
            "jobs": [
                {
                    "id": "job-123",
                    "title": "Software Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/example/job-123/application",
                    "applyUrl": "https://jobs.ashbyhq.com/old-board/removed-job/application",
                }
            ]
        }
        with patch("job_radar.sources.get_json", return_value=payload):
            job = ashby("Example", "example")[0]
        self.assertEqual(job.source_id, "job-123")
        self.assertEqual(job.url, "https://jobs.ashbyhq.com/example/job-123")
        self.assertEqual(job.apply_url, "https://jobs.ashbyhq.com/example/job-123/application")

    def test_amd_uses_public_job_page_for_telegram_link(self):
        payload = {
            "jobs": [
                {
                    "data": {
                        "req_id": "123",
                        "slug": "123",
                        "title": "Software Engineer",
                        "location_name": "Austin, TX, United States",
                        "apply_url": "https://careers-amd.icims.com/jobs/123/login",
                        "posted_date": "2026-10-19",
                    }
                }
            ]
        }
        with patch("job_radar.sources.get_page_json", return_value=payload):
            job = amd("AMD", ["software engineer"], pages=1)[0]

        self.assertEqual(job.url, "https://careers.amd.com/careers-home/jobs/123")
        self.assertEqual(job.apply_url, "https://careers-amd.icims.com/jobs/123/login")
        rendered = format_job(job)
        self.assertIn('href="https://careers.amd.com/careers-home/jobs/123"', rendered)
        self.assertIn(">View job</a>", rendered)
        self.assertNotIn("careers-amd.icims.com", rendered)

    def test_workday_url_includes_site_path(self):
        self.assertEqual(
            workday_job_url(
                "zoom.wd5.myworkdayjobs.com",
                "Zoom",
                "/job/San-Jose-CA/Software-Engineer_R19747-1",
            ),
            "https://zoom.wd5.myworkdayjobs.com/Zoom/job/San-Jose-CA/Software-Engineer_R19747-1",
        )

    def test_workday_url_does_not_duplicate_existing_site_path(self):
        self.assertEqual(
            workday_job_url(
                "salesforce.wd12.myworkdayjobs.com",
                "External_Career_Site",
                "/External_Career_Site/job/California---San-Francisco/Job_JR123",
            ),
            "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/California---San-Francisco/Job_JR123",
        )

    def test_goldman_uses_external_source_id_and_role_url(self):
        search = {
            "data": {
                "roleSearch": {
                    "totalCount": 1,
                    "items": [
                        {
                            "roleId": "role-1_GS_EARLY_CAREER",
                            "jobTitle": "Software Engineer - Analyst - New York",
                            "jobFunction": "Software Engineering",
                            "locations": [{"city": "New York", "state": "NY", "country": "United States"}],
                            "externalSource": {"sourceId": "role-1"},
                        }
                    ],
                }
            }
        }
        detail = {
            "data": {
                "role": {
                    "roleId": "role-1_GS_EARLY_CAREER",
                    "jobTitle": "Software Engineer - Analyst - New York",
                    "jobFunction": "Software Engineering",
                    "locations": [{"city": "New York", "state": "NY", "country": "United States"}],
                    "shortDescription": "Build software.",
                    "descriptionHtml": "<p>Requires 2+ years of experience.</p>",
                    "lastPostedDate": "2026-10-13T10:00:00Z",
                    "lastModifiedDate": "2026-10-13T10:00:00Z",
                }
            }
        }
        with patch("job_radar.sources.post_json", side_effect=[search, detail]):
            job = goldman("Goldman Sachs", ["software engineer"], pages=1)[0]
        self.assertEqual(job.source_id, "role-1")
        self.assertEqual(job.url, "https://higher.gs.com/roles/role-1")
        self.assertIn("2+ years", job.description)

    def test_jpmorgan_uses_stable_requisition_id_and_public_detail_url(self):
        recent_date = datetime.now(timezone.utc).date().isoformat()
        search = {
            "items": [
                {
                    "TotalJobsCount": 1,
                    "requisitionList": [
                        {
                            "Id": "210123456",
                            "Title": "Software Engineer II",
                            "PostedDate": recent_date,
                            "PrimaryLocation": "Jersey City, NJ, United States",
                            "secondaryLocations": [],
                            "JobFamily": "Software Engineering",
                            "JobFunction": "Technology",
                        }
                    ],
                }
            ]
        }
        detail = {
            "items": [
                {
                    "Id": "210123456",
                    "Title": "Software Engineer II",
                    "ExternalPostedStartDate": f"{recent_date}T10:00:00+00:00",
                    "ExternalDescriptionStr": "<p>Requires 2+ years of experience.</p>",
                }
            ]
        }
        with patch("job_radar.sources.get_json", side_effect=[search, detail]):
            job = jpmorgan(
                "JPMorgan Chase",
                "jpmc.fa.oraclecloud.com",
                "CX_1001",
                ["software engineer"],
                pages=1,
            )[0]
        self.assertEqual(job.source_id, "210123456")
        self.assertEqual(
            job.url,
            "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210123456",
        )
        self.assertIn("2+ years", job.description)


class NotificationDeduplicationTests(unittest.TestCase):
    def test_same_posting_from_two_requisition_ids_is_sent_once(self):
        first = make_job(source_id="req-1")
        second = make_job(source_id="req-2")

        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "jobs.sqlite3")
            with patch.dict(os.environ, {"DB_PATH": database}), patch.object(
                app, "load_companies", return_value=[{"company": "Example", "type": "test"}]
            ), patch.object(app, "fetch_source", return_value=[first, second]):
                self.assertEqual(app.scan(dry_run=True), 1)

    def test_changed_description_with_same_source_id_is_not_new(self):
        first = make_job(source_id="stable-id", description="Build backend services.")
        second = make_job(source_id="stable-id", description="Build backend services and APIs.")

        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "jobs.sqlite3"))
            saved, is_new = store.upsert(first)
            self.assertTrue(is_new)
            store.mark_sent([saved], saved.first_seen_at)

            updated, is_new = store.upsert(second)
            self.assertFalse(is_new)
            self.assertIsNotNone(updated.last_sent_at)

    def test_same_posting_day_and_location_is_not_new(self):
        posted = datetime(2026, 10, 10, tzinfo=timezone.utc)
        first = make_job(source_id="req-1", description="Build backend services.")
        second = make_job(source_id="req-2", description="Build backend services for a different team.")
        first.published_at = posted
        second.published_at = posted

        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "jobs.sqlite3"))
            saved, is_new = store.upsert(first)
            self.assertTrue(is_new)
            store.mark_sent([saved], saved.first_seen_at)

            updated, is_new = store.upsert(second)
            self.assertFalse(is_new)
            self.assertIsNotNone(updated.last_sent_at)


class TelegramSubscriptionTests(unittest.TestCase):
    def test_resolves_configured_and_private_start_chats(self):
        updates = {
            "result": [
                {"message": {"chat": {"id": 222, "type": "private"}, "text": "/start"}},
                {"message": {"chat": {"id": 333, "type": "private"}, "text": "hello"}},
                {"message": {"chat": {"id": -444, "type": "group"}, "text": "/start"}},
            ]
        }
        with patch.dict(
            os.environ,
            {"TELEGRAM_CHAT_ID": "111", "TELEGRAM_CHAT_IDS": "555, 666"},
            clear=False,
        ), patch("job_radar.telegram.call", return_value=updates):
            self.assertEqual(resolve_chat_ids("token"), ["111", "222", "555", "666"])

    def test_requires_a_configured_or_started_private_chat(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_CHAT_ID": "", "TELEGRAM_CHAT_IDS": ""},
            clear=False,
        ), patch("job_radar.telegram.call", return_value={"result": []}), self.assertRaisesRegex(
            TelegramError, "No Telegram subscribers found"
        ):
            resolve_chat_ids("token")


if __name__ == "__main__":
    unittest.main()
