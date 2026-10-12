import os
import tempfile
import unittest
from unittest.mock import patch

from job_radar import app
from job_radar.filtering import passes
from job_radar.models import Job
from job_radar.storage import Store


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


if __name__ == "__main__":
    unittest.main()
