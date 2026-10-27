import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_radar.models import Job
from job_radar.storage import Store
from job_radar.telegram import TelegramError, send_text_document
from job_radar.tsenta_telegram import document_for, run_once
from job_radar.tsenta_candidates import Candidate


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
DESCRIPTION = "Requires 3 years of Java and Kafka experience. Build backend services. " * 4


def sample_job(source_id="req-1"):
    return Job(
        company="Datadog", source_type="greenhouse", source_id=source_id,
        title="Software Engineer", location="New York, NY, United States",
        url=f"https://example.com/jobs/{source_id}", apply_url=f"https://example.com/jobs/{source_id}",
        description=DESCRIPTION, published_at=NOW - timedelta(hours=1),
        first_seen_at=NOW - timedelta(hours=1), last_seen_at=NOW,
    )


class TsentaTelegramTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temporary.name) / "jobs.sqlite3"))
        self.saved, _ = self.store.upsert(sample_job())

    def tearDown(self):
        self.store.connection.close()
        self.temporary.cleanup()

    def test_document_contains_full_description_and_link(self):
        candidate = Candidate(self.saved, "unverified", ("java", "kafka"), 2)
        filename, caption, content = document_for(candidate)
        self.assertEqual(filename, "Datadog-req-1.txt")
        self.assertIn(self.saved.url, caption)
        self.assertIn(DESCRIPTION.strip(), content)
        self.assertIn("Sponsorship: unverified", content)

    def test_send_once_and_do_not_repeat(self):
        with patch("job_radar.tsenta_telegram.resolve_chat_ids", return_value=["123"]), patch(
            "job_radar.tsenta_telegram.send_text_document"
        ) as send:
            self.assertEqual(run_once(self.store, "fake-token", "Java Kafka", NOW), 1)
            self.assertEqual(run_once(self.store, "fake-token", "Java Kafka", NOW), 0)
            send.assert_called_once()

    def test_failed_send_is_retried(self):
        with patch("job_radar.tsenta_telegram.resolve_chat_ids", return_value=["123"]), patch(
            "job_radar.tsenta_telegram.send_text_document", side_effect=TelegramError("offline")
        ):
            self.assertEqual(run_once(self.store, "fake-token", "Java Kafka", NOW), 0)
        self.assertFalse(self.store.tsenta_notified(self.saved))

    def test_source_id_deduplicates_changed_fingerprint(self):
        self.store.mark_tsenta_notified(self.saved, NOW)
        changed = sample_job()
        changed.fingerprint = "changed-description-fingerprint"
        self.assertTrue(self.store.tsenta_notified(changed))

    def test_multipart_document_request_contains_jd(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true}'

        with patch("job_radar.telegram.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            send_text_document("fake-token", "123", "job.txt", "Full Java JD", "Job link")
        request = urlopen.call_args.args[0]
        self.assertIn("/sendDocument", request.full_url)
        self.assertIn(b"Full Java JD", request.data)
        self.assertIn(b'filename="job.txt"', request.data)


if __name__ == "__main__":
    unittest.main()
