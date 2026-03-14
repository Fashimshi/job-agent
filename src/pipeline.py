from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from src.application.cover_letter import CoverLetterGenerator
from src.application.greenhouse_apply import GreenhouseApplicant
from src.application.lever_apply import LeverApplicant
from src.application.workday_apply import WorkdayApplicant
from src.company.classifier import CompanyClassifier
from src.company.registry import CompanyRegistry
from src.discovery.greenhouse_source import GreenhouseSource
from src.discovery.lever_source import LeverSource
from src.discovery.linkedin_source import LinkedInSource
from src.discovery.orchestrator import DiscoveryOrchestrator
from src.discovery.serpapi_source import SerpAPISource
from src.matching.filters import JobFilter
from src.matching.llm_client import LLMClient
from src.matching.parser import JobParser
from src.matching.scorer import JobScorer
from src.notifications.notifier import Notifier
from src.tracking.database import Database
from src.tracking.models import (
    ATSType,
    ApplicationRecord,
    ApplicationStatus,
    ApplicantInfo,
    Job,
    MatchScore,
)

if TYPE_CHECKING:
    from src.config_loader import Settings

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end job discovery, matching, and application pipeline."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.connect()

        self.registry = CompanyRegistry(
            Path(settings.resolve_path("config/companies.yaml"))
        )
        self.llm = LLMClient(settings)
        self.classifier = CompanyClassifier(self.registry, self.llm)
        self.filter = JobFilter(settings)
        self.parser = JobParser(self.llm)
        self.scorer = JobScorer(self.llm, settings.resume_text_path)
        self.cover_letter_gen = CoverLetterGenerator(self.llm, settings.resume_text_path)
        self.notifier = Notifier(settings)

        self.applicant_info = ApplicantInfo(
            first_name=settings.applicant.first_name,
            last_name=settings.applicant.last_name,
            email=settings.applicant.email,
            phone=settings.applicant.phone,
            linkedin_url=settings.applicant.linkedin_url,
            github_url=settings.applicant.github_url,
            portfolio_url=settings.applicant.portfolio_url,
            location=settings.applicant.location,
            current_company=settings.applicant.current_company,
            work_authorized=settings.applicant.work_authorized,
            sponsorship_needed=settings.applicant.sponsorship_needed,
            sponsorship_details=settings.applicant.sponsorship_details,
        )

    async def run(self, dry_run: bool | None = None) -> dict:
        """Run the full pipeline: discover -> filter -> score -> apply/notify."""
        dry_run = dry_run if dry_run is not None else self.settings.application.dry_run

        logger.info("=" * 60)
        logger.info("STARTING JOB AGENT PIPELINE")
        logger.info("=" * 60)

        # Step 1: Discover
        new_jobs = await self.discover()
        logger.info(f"Step 1 DISCOVER: {len(new_jobs)} new jobs found")

        # Step 2: Filter
        unscored = self.db.get_unscored_jobs()
        filtered = self.filter.apply_all(unscored)
        logger.info(f"Step 2 FILTER: {len(filtered)}/{len(unscored)} passed filters")

        # Step 3: Score
        scored = await self.score(filtered)
        logger.info(f"Step 3 SCORE: {len(scored)} jobs scored")

        # Step 4: Triage and act
        applied_count = 0
        manual_count = 0
        applied_jobs: list[tuple[Job, MatchScore]] = []

        # Auto-apply candidates (score >= threshold, any ATS — we resolve at runtime)
        auto_candidates = self.db.get_auto_apply_candidates(
            self.settings.matching.min_score_auto_apply
        )
        failed_auto = []  # Jobs that couldn't auto-apply → fall through to manual
        for job, score in auto_candidates:
            if self.registry.is_excluded_from_apply(job.company):
                continue
            if self.db.get_today_application_count() >= self.settings.application.max_per_day:
                logger.warning("Daily application limit reached")
                break

            result = await self.apply_to_job(job, score, dry_run)
            if result:
                applied_count += 1
                applied_jobs.append((job, score))
            else:
                # Couldn't auto-apply (unsupported ATS, untrusted URL, etc.)
                failed_auto.append((job, score))

        # Manual notification: scored >= notify threshold + failed auto-apply jobs
        manual_candidates = self.db.get_jobs_needing_notification(
            self.settings.matching.min_score_notify
        )
        # Merge failed auto-apply into manual list (avoid duplicates)
        manual_job_ids = {j.id for j, _ in manual_candidates}
        for job, score in failed_auto:
            if job.id not in manual_job_ids:
                manual_candidates.append((job, score))

        for job, score in manual_candidates:
            if self.registry.is_excluded_from_apply(job.company):
                continue
            if self.db.is_notified(job.id, "manual_needed"):
                continue
            await self.prepare_manual_application(job, score)
            self.db.mark_notified(job.id, "manual_needed")
            manual_count += 1

        # Step 5: Notify digest
        # Collect all qualifying jobs (scored >= notify threshold) for the digest
        all_qualified = self.db.get_jobs_by_score(self.settings.matching.min_score_notify)
        stats = self.db.get_stats()
        self.notifier.notify_digest(
            stats, len(new_jobs), applied_count, manual_count,
            qualified_jobs=all_qualified,
            applied_jobs=applied_jobs,
        )

        # Show summary table
        all_scored = self.db.get_jobs_by_score(self.settings.matching.min_score_log)
        if all_scored:
            self.notifier.print_job_table(all_scored[:20])

        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE")
        logger.info("=" * 60)

        return {
            "new_jobs": len(new_jobs),
            "filtered": len(filtered),
            "scored": len(scored),
            "applied": applied_count,
            "manual_needed": manual_count,
        }

    async def discover(self) -> list[Job]:
        """Run job discovery from all configured sources."""
        sources = []

        # LinkedIn — primary source (no API key needed, real job URLs)
        sources.append(LinkedInSource(max_results_per_query=50))

        # Greenhouse — direct ATS API (auto-apply capable)
        gh_tokens = self.registry.get_greenhouse_tokens()
        if gh_tokens:
            sources.append(GreenhouseSource(gh_tokens, self.settings.role_keywords))

        # Lever — direct ATS API (auto-apply capable)
        lever_slugs = self.registry.get_lever_slugs()
        if lever_slugs:
            sources.append(LeverSource(lever_slugs, self.settings.role_keywords))

        # SerpAPI — backup for broader coverage
        if self.settings.serpapi_key:
            sources.append(SerpAPISource(self.settings.serpapi_key))

        if not sources:
            logger.warning("No discovery sources configured!")
            return []

        orchestrator = DiscoveryOrchestrator(sources, self.db)
        return await orchestrator.run_discovery(
            queries=self.settings.discovery.queries,
            location=self.settings.discovery.location,
            posted_within_days=self.settings.discovery.posted_within_days,
        )

    async def score(self, jobs: list[Job], max_workers: int = 10) -> list[tuple[Job, MatchScore]]:
        """Score a list of jobs using LLM with parallel workers."""
        semaphore = asyncio.Semaphore(max_workers)
        results: list[tuple[Job, MatchScore]] = []
        lock = asyncio.Lock()

        async def _score_one(job: Job) -> None:
            async with semaphore:
                parsed = await self.parser.parse(
                    job.title, job.company, job.description_raw or ""
                )
                match_score = await self.scorer.score(job, parsed)
                self.db.insert_score(match_score)
                async with lock:
                    results.append((job, match_score))
                logger.info(
                    f"  Scored: {match_score.overall_score}/100 - "
                    f"{job.title} at {job.company}"
                )

        await asyncio.gather(*[_score_one(job) for job in jobs], return_exceptions=True)
        return results

    # Trusted ATS domains for auto-apply
    TRUSTED_ATS_DOMAINS = {
        "greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com",
    }

    def _is_trusted_apply_url(self, url: str) -> bool:
        """Only allow auto-apply on official ATS platforms."""
        if not url:
            return False
        hostname = urlparse(url).hostname or ""
        return any(hostname.endswith(d) for d in self.TRUSTED_ATS_DOMAINS)

    async def _resolve_ats(self, job: Job) -> None:
        """Follow the apply URL to detect the real ATS platform."""
        if job.ats_type not in (ATSType.UNKNOWN, ATSType.CUSTOM):
            return  # Already known
        if not job.apply_url:
            return

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.head(job.apply_url)
                final_url = str(resp.url).lower()

                if "greenhouse.io" in final_url or "boards.greenhouse" in final_url:
                    job.ats_type = ATSType.GREENHOUSE
                    job.apply_url = str(resp.url)
                elif "lever.co" in final_url or "jobs.lever" in final_url:
                    job.ats_type = ATSType.LEVER
                    job.apply_url = str(resp.url)
                elif "myworkdayjobs" in final_url or "wd1." in final_url or "wd5." in final_url:
                    job.ats_type = ATSType.WORKDAY
                    job.apply_url = str(resp.url)

                if job.ats_type != ATSType.UNKNOWN:
                    logger.info(
                        f"  Resolved ATS for {job.company}: {job.ats_type.value} "
                        f"({str(resp.url)[:80]})"
                    )
                    # Update in database
                    self.db.conn.execute(
                        "UPDATE jobs SET ats_type=?, apply_url=? WHERE id=?",
                        (job.ats_type.value, job.apply_url, job.id),
                    )
                    self.db.conn.commit()
        except Exception as e:
            logger.debug(f"Failed to resolve ATS for {job.apply_url}: {e}")

    async def apply_to_job(self, job: Job, score: MatchScore, dry_run: bool) -> bool:
        """Auto-apply to a single job."""
        logger.info(f"Applying to: {job.title} at {job.company} (score: {score.overall_score})")

        # Resolve ATS type if unknown (follow redirects to detect platform)
        await self._resolve_ats(job)

        # Safety check: if ATS is known (greenhouse/lever/workday), trust it.
        # For unknown ATS, check if the URL at least points to a trusted domain.
        if job.ats_type in (ATSType.UNKNOWN, ATSType.CUSTOM):
            if not self._is_trusted_apply_url(job.apply_url):
                return False  # Silently skip — not an ATS we can auto-apply to

        # Parse for cover letter
        parsed = await self.parser.parse(job.title, job.company, job.description_raw or "")
        cover_letter = await self.cover_letter_gen.generate(parsed, score)

        # Choose applicant based on ATS
        if job.ats_type == ATSType.GREENHOUSE:
            applicant = GreenhouseApplicant(self.applicant_info)
        elif job.ats_type == ATSType.LEVER:
            applicant = LeverApplicant(self.applicant_info)
        elif job.ats_type == ATSType.WORKDAY:
            applicant = WorkdayApplicant(self.applicant_info)
        else:
            logger.info(f"No auto-apply for {job.title} at {job.company} — ATS: {job.ats_type.value}")
            return False

        result = await applicant.apply(
            job=job,
            cover_letter=cover_letter,
            resume_path=self.settings.resume_pdf_path,
            screenshot_dir=self.settings.screenshot_dir_path,
            dry_run=dry_run,
        )

        # Record the application
        status = (
            ApplicationStatus.APPLIED if result.success and not dry_run
            else ApplicationStatus.READY_TO_APPLY if result.success
            else ApplicationStatus.FAILED
        )
        app_record = ApplicationRecord(
            job_id=job.id,
            status=status,
            method=f"auto_{job.ats_type.value}",
            cover_letter=cover_letter,
            screenshot_path=result.screenshot_path,
            applied_at=result.submitted_at,
            error_message=result.error_message,
        )
        self.db.insert_application(app_record)

        if result.success:
            self.notifier.notify_auto_applied(job, score, result.screenshot_path)
            self.db.mark_notified(job.id, "auto_applied")

        return result.success

    async def prepare_manual_application(self, job: Job, score: MatchScore) -> None:
        """Generate materials for manual application and notify."""
        parsed = await self.parser.parse(job.title, job.company, job.description_raw or "")
        cover_letter = await self.cover_letter_gen.generate(parsed, score)

        app_record = ApplicationRecord(
            job_id=job.id,
            status=ApplicationStatus.MANUAL_NEEDED,
            method="manual",
            cover_letter=cover_letter,
        )
        self.db.insert_application(app_record)

        self.notifier.notify_manual_needed(job, score, cover_letter)

    def close(self) -> None:
        self.db.close()
