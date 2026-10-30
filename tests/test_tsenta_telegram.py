import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_radar.models import Job
from job_radar.storage import Store
from job_radar.telegram import TelegramError, send_plain_message
from job_radar.tsenta_telegram import message_text_for, messages_for, run_once
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

    def test_message_contains_full_description_and_link(self):
        candidate = Candidate(self.saved, "unverified", ("java", "kafka"), 2)
        (content,) = messages_for(candidate)
        self.assertIn(self.saved.url, content)
        self.assertIn(DESCRIPTION.strip(), content)
        self.assertIn("Sponsorship: unverified", content)

    def test_long_description_is_split_without_losing_text(self):
        job = sample_job()
        job.description = "Java Kafka distributed systems. " * 500
        candidate = Candidate(job, "unverified", ("java", "kafka"), 2)
        parts = messages_for(candidate)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4096 for part in parts))
        self.assertIn(job.url, parts[0])
        self.assertEqual(
            "".join(part.split("\n\n", 1)[1] for part in parts),
            message_text_for(candidate),
        )

    def test_send_once_and_do_not_repeat(self):
        with patch("job_radar.tsenta_telegram.resolve_chat_ids", return_value=["123"]), patch(
            "job_radar.tsenta_telegram.send_plain_message"
        ) as send:
            self.assertEqual(run_once(self.store, "fake-token", "Java Kafka", NOW), 1)
            self.assertEqual(run_once(self.store, "fake-token", "Java Kafka", NOW), 0)
            send.assert_called_once()
            self.assertIn(self.saved.url, send.call_args.args[2])

    def test_failed_send_is_retried(self):
        with patch("job_radar.tsenta_telegram.resolve_chat_ids", return_value=["123"]), patch(
            "job_radar.tsenta_telegram.send_plain_message", side_effect=TelegramError("offline")
        ), self.assertRaisesRegex(TelegramError, "No Tsenta jobs were delivered"):
            run_once(self.store, "fake-token", "Java Kafka", NOW)
        self.assertFalse(self.store.tsenta_notified(self.saved))

    def test_source_id_deduplicates_changed_fingerprint(self):
        self.store.mark_tsenta_notified(self.saved, NOW)
        changed = sample_job()
        changed.fingerprint = "changed-description-fingerprint"
        self.assertTrue(self.store.tsenta_notified(changed))

    def test_plain_message_request_contains_jd(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true}'

        with patch("job_radar.telegram.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            send_plain_message("fake-token", "123", "Full Java JD")
        request = urlopen.call_args.args[0]
        self.assertIn("/sendMessage", request.full_url)
        fields = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(fields["text"], ["Full Java JD"])
        self.assertEqual(fields["chat_id"], ["123"])
        self.assertNotIn("parse_mode", fields)


if __name__ == "__main__":
    unittest.main()
