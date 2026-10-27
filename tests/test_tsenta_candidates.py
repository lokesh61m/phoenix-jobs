import json
import unittest
from datetime import datetime, timedelta, timezone

from job_radar.config import ROOT
from job_radar.models import Job
from job_radar.tsenta_candidates import (
    load_selected_companies,
    select_candidates,
    sponsorship_status,
)


def job(company="Datadog", title="Software Engineer", description="Build Java and Kafka services."):
    return Job(
        company=company,
        source_type="greenhouse",
        source_id="req-1",
        title=title,
        location="New York, NY, United States",
        url="https://example.com/job/req-1",
        apply_url="https://example.com/job/req-1",
        description=description,
        published_at=datetime.now(timezone.utc),
    )


class TsentaCandidateTests(unittest.TestCase):
    def test_selected_roster_has_29_active_sources(self):
        selected = load_selected_companies()
        active = {
            entry["company"].lower()
            for entry in json.loads((ROOT / "config" / "companies.json").read_text())
            if entry.get("enabled", True)
        }
        self.assertEqual(len(selected), 29)
        self.assertTrue(selected.issubset(active))

    def test_experience_cap_is_three_not_telegram_three_and_a_half(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        jobs = [
            job(description="Requires 3+ years of Java experience."),
            job(description="Requires 3.5+ years of Java experience."),
        ]
        jobs[1].source_id = "req-2"
        jobs[1].url = "https://example.com/job/req-2"
        result = select_candidates(jobs, "Java, Kafka", {"datadog"}, cutoff)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job.source_id, "req-1")

    def test_sponsorship_silent_is_unverified_not_rejected(self):
        self.assertEqual(sponsorship_status("Build Java services."), "unverified")
        self.assertEqual(sponsorship_status("Visa sponsorship is available."), "confirmed")
        self.assertEqual(sponsorship_status("We cannot sponsor candidates."), "not_available")
        self.assertEqual(sponsorship_status("Must work without sponsorship."), "not_available")
        self.assertEqual(sponsorship_status("Visa sponsorship is not available."), "not_available")
        self.assertEqual(sponsorship_status("US citizenship is required."), "not_available")

    def test_excludes_non_sponsors_and_other_companies(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        jobs = [
            job(description="Must be authorized to work without the need for sponsorship."),
            job(company="OpenAI", description="Build Java services."),
        ]
        self.assertEqual(select_candidates(jobs, "Java", {"datadog"}, cutoff), [])

    def test_uses_profile_summary_only_in_memory_to_rank(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        strong = job(description="Build Java, Kafka, and Kubernetes services.")
        weak = job(company="Twilio", description="Build web applications.")
        result = select_candidates([weak, strong], "Java Kafka Kubernetes", {"datadog", "twilio"}, cutoff)
        self.assertEqual([candidate.job.company for candidate in result], ["Datadog", "Twilio"])
        self.assertEqual(result[0].matched_skills, ("java", "kubernetes", "kafka"))
        self.assertEqual(result[1].score, 0)

    def test_excludes_stale_jobs_and_senior_titles(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        old = job()
        old.published_at = cutoff - timedelta(seconds=1)
        senior = job(title="Senior Software Engineer")
        self.assertEqual(select_candidates([old, senior], "Java", {"datadog"}, cutoff), [])


if __name__ == "__main__":
    unittest.main()
