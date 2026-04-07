"""Tests for pipeline digest email reliability.

Validates that the digest email is always sent, even when:
- Discovery step crashes
- Scoring step crashes
- Auto-apply step crashes
- Everything succeeds normally
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.tracking.models import Job, MatchScore


@pytest.fixture
def mock_settings():
    """Create a mock Settings object with all required attributes."""
    settings = MagicMock()
    settings.database_path = ":memory:"
    settings.matching.min_score_auto_apply = 78
    settings.matching.min_score_notify = 70
    settings.matching.min_score_log = 50
    settings.application.dry_run = False
    settings.application.max_per_day = 10
    settings.resume_pdf_path = "/tmp/resume.pdf"
    settings.resume_text_path = MagicMock()
    settings.resume_text_path.exists.return_value = False
    settings.screenshot_dir_path = "/tmp/screenshots"
    settings.notifications.channels = ["email"]
    settings.smtp_user = "test@example.com"
    settings.smtp_password = "password"
    settings.smtp_host = "smtp.gmail.com"
    settings.smtp_port = 587
    settings.notification_email = "test@example.com"
    settings.resolve_path = lambda x: f"/tmp/{x}"
    settings.applicant.first_name = "Test"
    settings.applicant.last_name = "User"
    settings.applicant.email = "test@example.com"
    settings.applicant.phone = "555-1234"
    settings.applicant.linkedin_url = "https://linkedin.com/in/test"
    settings.applicant.github_url = None
    settings.applicant.portfolio_url = None
    settings.applicant.location = "San Francisco, CA"
    settings.applicant.current_company = "Acme"
    settings.applicant.work_authorized = True
    settings.applicant.sponsorship_needed = False
    settings.applicant.sponsorship_details = ""
    return settings


def _make_patches():
    """Create all necessary patches for the Pipeline class."""
    return [
        patch("src.pipeline.Database"),
        patch("src.pipeline.Notifier"),
        patch("src.pipeline.CompanyRegistry"),
        patch("src.pipeline.LLMClient"),
        patch("src.pipeline.JobParser"),
        patch("src.pipeline.JobScorer"),
        patch("src.pipeline.CoverLetterGenerator"),
        patch("src.pipeline.JobFilter"),
        patch("src.pipeline.DiscoveryOrchestrator"),
        patch("src.pipeline.CompanyClassifier"),
        patch("src.pipeline.JobEvaluator"),
        patch("src.pipeline.PdfBuilder"),
    ]


def _setup_db(mock_db):
    db = mock_db.return_value
    db.connect = MagicMock()
    db.get_jobs_by_score = MagicMock(return_value=[])
    db.get_unscored_jobs = MagicMock(return_value=[])
    db.get_unevaluated_jobs = MagicMock(return_value=[])
    db.get_auto_apply_candidates = MagicMock(return_value=[])
    db.get_jobs_needing_notification = MagicMock(return_value=[])
    db.get_artifact = MagicMock(return_value=None)
    db.insert_pipeline_run = MagicMock()
    db.update_pipeline_run = MagicMock()
    db.conn = MagicMock()
    db.get_stats = MagicMock(return_value={
        "total_jobs_discovered": 0,
        "jobs_scored": 0,
        "applications_submitted": 0,
        "average_match_score": 0,
    })
    return db


class TestDigestAlwaysSends:
    """Test that digest email is sent regardless of pipeline failures."""

    @pytest.mark.asyncio
    async def test_digest_sends_when_discovery_crashes(self, mock_settings):
        patches = _make_patches()
        mocks = [p.start() for p in patches]
        try:
            from src.pipeline import Pipeline
            MockDB, MockNotifier = mocks[0], mocks[1]
            _setup_db(MockDB)
            pipeline = Pipeline(mock_settings)
            pipeline.discover = AsyncMock(side_effect=RuntimeError("Discovery exploded"))
            await pipeline.run(dry_run=False)
            MockNotifier.return_value.notify_digest.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_digest_sends_when_scoring_crashes(self, mock_settings):
        patches = _make_patches()
        mocks = [p.start() for p in patches]
        try:
            from src.pipeline import Pipeline
            MockDB, MockNotifier = mocks[0], mocks[1]
            _setup_db(MockDB)
            pipeline = Pipeline(mock_settings)
            pipeline.discover = AsyncMock(return_value=[])
            pipeline.score = AsyncMock(side_effect=RuntimeError("Scoring crashed"))
            await pipeline.run(dry_run=False)
            MockNotifier.return_value.notify_digest.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_digest_sends_on_successful_run(self, mock_settings):
        patches = _make_patches()
        mocks = [p.start() for p in patches]
        try:
            from src.pipeline import Pipeline
            MockDB, MockNotifier = mocks[0], mocks[1]
            db = _setup_db(MockDB)
            db.get_stats.return_value = {
                "total_jobs_discovered": 5, "jobs_scored": 3,
                "applications_submitted": 1, "average_match_score": 75,
            }
            pipeline = Pipeline(mock_settings)
            pipeline.discover = AsyncMock(return_value=[])
            pipeline.score = AsyncMock(return_value=[])
            await pipeline.run(dry_run=False)
            MockNotifier.return_value.notify_digest.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_digest_sends_when_triage_crashes(self, mock_settings):
        patches = _make_patches()
        mocks = [p.start() for p in patches]
        try:
            from src.pipeline import Pipeline
            MockDB, MockNotifier = mocks[0], mocks[1]
            db = _setup_db(MockDB)
            db.get_auto_apply_candidates = MagicMock(side_effect=RuntimeError("DB lost"))
            pipeline = Pipeline(mock_settings)
            pipeline.discover = AsyncMock(return_value=[])
            pipeline.score = AsyncMock(return_value=[])
            await pipeline.run(dry_run=False)
            MockNotifier.return_value.notify_digest.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_digest_failure_doesnt_crash_pipeline(self, mock_settings):
        patches = _make_patches()
        mocks = [p.start() for p in patches]
        try:
            from src.pipeline import Pipeline
            MockDB, MockNotifier = mocks[0], mocks[1]
            db = _setup_db(MockDB)
            db.get_jobs_by_score = MagicMock(side_effect=RuntimeError("DB corrupted"))
            db.get_stats = MagicMock(return_value={})
            pipeline = Pipeline(mock_settings)
            pipeline.discover = AsyncMock(return_value=[])
            pipeline.score = AsyncMock(return_value=[])
            result = await pipeline.run(dry_run=False)
            assert isinstance(result, dict)
        finally:
            for p in patches:
                p.stop()
