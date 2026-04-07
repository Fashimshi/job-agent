from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from src.evaluation.evaluator import JobEvaluator
from src.evaluation.pdf_builder import PdfBuilder
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
    PipelineRun,
)

if TYPE_CHECKING:
    from src.config_loader import Settings

logger = logging.getLogger(__name__)

# Step timeouts (seconds)
DISCOVER_TIMEOUT = 15 * 60   # 15 min
SCORE_TIMEOUT = 20 * 60      # 20 min
EVALUATE_TIMEOUT = 15 * 60   # 15 min
GENERATE_TIMEOUT = 10 * 60   # 10 min
DIGEST_TIMEOUT = 3 * 60      # 3 min
APPLY_TIMEOUT = 15 * 60      # 15 min
MANUAL_TIMEOUT = 5 * 60      # 5 min


class Pipeline:
    """End-to-end job discovery, matching, and application pipeline.

    Each step has its own timeout so no single step can starve the others.
    Order: discover -> filter -> score -> DIGEST -> apply -> manual notify
    Digest runs before apply so it always sends, even if apply is slow.
    """

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

        # Evaluation engine (career-ops A-F blocks via Claude API)
        resume_text = ""
        cv_path = Path(settings.resolve_path("cv.md"))
        if cv_path.exists():
            resume_text = cv_path.read_text(encoding="utf-8")[:4000]
        elif settings.resume_text_path.exists():
            resume_text = settings.resume_text_path.read_text(encoding="utf-8")[:4000]
        self.evaluator = JobEvaluator(self.llm, self.db, resume_text)
        self.pdf_builder = PdfBuilder(self.db)

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
        """Run the full pipeline with per-step timeouts.

        Order: discover -> filter -> score -> DIGEST -> apply -> manual notify

        Digest sends BEFORE apply so you always get the email even if apply
        takes too long or crashes.
        """
        dry_run = dry_run if dry_run is not None else self.settings.application.dry_run

        logger.info("=" * 60)
        logger.info("STARTING JOB AGENT PIPELINE")
        logger.info("=" * 60)

        new_jobs: list = []
        filtered: list = []
        scored: list = []
        evaluated_count = 0
        pdf_count = 0
        applied_count = 0
        manual_count = 0
        applied_jobs: list[tuple[Job, MatchScore]] = []

        # Record pipeline run
        pipeline_run = PipelineRun(trigger="automated")
        self.db.insert_pipeline_run(pipeline_run)

        # ── Step 1: DISCOVER (15 min timeout) ────────────────────────
        try:
            new_jobs = await asyncio.wait_for(
                self.discover(), timeout=DISCOVER_TIMEOUT
            )
            logger.info(f"DISCOVER: {len(new_jobs)} new jobs found")
        except asyncio.TimeoutError:
            logger.error(f"DISCOVER timed out after {DISCOVER_TIMEOUT // 60} min")
        except Exception as e:
            logger.error(f"DISCOVER failed: {e}")

        # ── Step 2: FILTER ───────────────────────────────────────────
        try:
            unscored = self.db.get_unscored_jobs()
            filtered = self.filter.apply_all(unscored)
            logger.info(f"FILTER: {len(filtered)}/{len(unscored)} passed")
        except Exception as e:
            logger.error(f"FILTER failed: {e}")

        # ── Step 3: SCORE (20 min timeout) ───────────────────────────
        try:
            scored = await asyncio.wait_for(
                self.score(filtered), timeout=SCORE_TIMEOUT
            )
            logger.info(f"SCORE: {len(scored)} jobs scored")
        except asyncio.TimeoutError:
            logger.error(f"SCORE timed out after {SCORE_TIMEOUT // 60} min")
        except Exception as e:
            logger.error(f"SCORE failed: {e}")

        # ── Step 4: EVALUATE (15 min timeout, deep A-F via Claude API) ─
        try:
            min_eval = getattr(self.settings, '_min_score_evaluate', 75)
            eval_candidates = self.db.get_unevaluated_jobs(min_eval)
            max_evals = 15
            eval_start = time.monotonic()

            for job, score in eval_candidates[:max_evals]:
                if time.monotonic() - eval_start > EVALUATE_TIMEOUT:
                    logger.warning(f"EVALUATE timed out after {EVALUATE_TIMEOUT // 60} min")
                    break
                try:
                    result = await self.evaluator.evaluate(job, score)
                    if result:
                        evaluated_count += 1
                except Exception as e:
                    logger.error(f"Evaluate error for {job.title} at {job.company}: {e}")

            logger.info(f"EVALUATE: {evaluated_count} deep evaluations completed")
        except Exception as e:
            logger.error(f"EVALUATE step failed: {e}")

        # ── Step 5: GENERATE PDF (10 min timeout) ───────────────────
        try:
            gen_start = time.monotonic()
            # Get evaluated jobs with score >= 4.0/5 for PDF generation
            all_evaluated = self.db.get_jobs_by_score(0)
            for job, score in all_evaluated:
                if time.monotonic() - gen_start > GENERATE_TIMEOUT:
                    logger.warning(f"GENERATE timed out after {GENERATE_TIMEOUT // 60} min")
                    break
                # Check if already has PDF
                if self.db.get_artifact(job.id, "pdf"):
                    continue
                # Check if has evaluation with score >= 4.0
                row = self.db.conn.execute(
                    "SELECT evaluation_json FROM match_scores WHERE job_id=?",
                    (job.id,),
                ).fetchone()
                if not row or not row["evaluation_json"]:
                    continue
                try:
                    import json as _json
                    eval_data = _json.loads(row["evaluation_json"])
                    if eval_data.get("score_5", 0) >= 4.0:
                        pdf_path = self.pdf_builder.build(job, eval_data)
                        if pdf_path:
                            pdf_count += 1
                except Exception as e:
                    logger.error(f"PDF generation error for {job.title}: {e}")

            logger.info(f"GENERATE: {pdf_count} tailored PDFs created")
        except Exception as e:
            logger.error(f"GENERATE step failed: {e}")

        # ── Step 6: DIGEST (3 min timeout, runs BEFORE apply) ───────
        try:
            all_qualified = self.db.get_jobs_by_score(self.settings.matching.min_score_notify)
            stats = self.db.get_stats()
            await asyncio.wait_for(
                asyncio.to_thread(
                    self.notifier.notify_digest,
                    stats, len(new_jobs), applied_count, manual_count,
                    qualified_jobs=all_qualified,
                    applied_jobs=applied_jobs,
                ),
                timeout=DIGEST_TIMEOUT,
            )
            logger.info("DIGEST: sent successfully")
        except asyncio.TimeoutError:
            logger.error(f"DIGEST timed out after {DIGEST_TIMEOUT // 60} min")
        except Exception as e:
            logger.error(f"DIGEST failed: {e}")

        # ── Step 5: AUTO-APPLY (15 min timeout) ─────────────────────
        failed_auto = []
        try:
            auto_candidates = self.db.get_auto_apply_candidates(
                self.settings.matching.min_score_auto_apply
            )
            apply_start = time.monotonic()
            max_attempts = self.settings.application.max_per_day * 3
            attempt_count = 0

            for job, score in auto_candidates:
                if self.registry.is_excluded_from_apply(job.company):
                    continue
                if self.db.get_today_application_count() >= self.settings.application.max_per_day:
                    logger.warning("Daily application limit reached")
                    break
                if attempt_count >= max_attempts:
                    logger.warning(f"Max apply attempts reached ({max_attempts})")
                    break
                if time.monotonic() - apply_start > APPLY_TIMEOUT:
                    logger.warning(f"APPLY timed out after {APPLY_TIMEOUT // 60} min")
                    break

                try:
                    attempt_count += 1
                    result = await self.apply_to_job(job, score, dry_run)
                    if result:
                        applied_count += 1
                        applied_jobs.append((job, score))
                    else:
                        failed_auto.append((job, score))
                except Exception as e:
                    logger.error(f"Apply error for {job.title} at {job.company}: {e}")
                    failed_auto.append((job, score))

            logger.info(f"APPLY: {applied_count} applications submitted, {len(failed_auto)} failed")
        except Exception as e:
            logger.error(f"APPLY step failed: {e}")

        # ── Step 6: MANUAL NOTIFICATIONS (5 min timeout) ────────────
        try:
            manual_start = time.monotonic()
            manual_candidates = self.db.get_jobs_needing_notification(
                self.settings.matching.min_score_notify
            )
            # Add failed auto-apply jobs to manual queue
            manual_job_ids = {j.id for j, _ in manual_candidates}
            for job, score in failed_auto:
                if job.id not in manual_job_ids:
                    manual_candidates.append((job, score))

            max_manual = 15
            for job, score in manual_candidates:
                if manual_count >= max_manual:
                    logger.info(f"Manual cap reached ({max_manual})")
                    break
                if time.monotonic() - manual_start > MANUAL_TIMEOUT:
                    logger.warning(f"MANUAL timed out after {MANUAL_TIMEOUT // 60} min")
                    break
                if self.registry.is_excluded_from_apply(job.company):
                    continue
                if self.db.is_notified(job.id, "manual_needed"):
                    continue
                try:
                    await self.prepare_manual_application(job, score)
                    self.db.mark_notified(job.id, "manual_needed")
                    manual_count += 1
                except Exception as e:
                    logger.error(f"Manual notify error for {job.title}: {e}")

            logger.info(f"MANUAL: {manual_count} notifications sent")
        except Exception as e:
            logger.error(f"MANUAL step failed: {e}")

        # ── Summary ─────────────────────────────────────────────────
        try:
            all_scored = self.db.get_jobs_by_score(self.settings.matching.min_score_log)
            if all_scored:
                self.notifier.print_job_table(all_scored[:20])
        except Exception:
            pass

        # ── Step 9: EXPORT (dashboard + markdown) ───────────────────
        try:
            from src.export import export_dashboard, export_markdown
            export_dashboard(self.db)
            export_markdown(self.db)
            logger.info("EXPORT: dashboard data + markdown exported")
        except Exception as e:
            logger.error(f"EXPORT failed: {e}")

        # ── Record pipeline run completion ──────────────────────────
        import json as _json
        summary = {
            "new_jobs": len(new_jobs), "filtered": len(filtered),
            "scored": len(scored), "evaluated": evaluated_count,
            "pdfs": pdf_count, "applied": applied_count,
            "manual": manual_count,
        }
        try:
            self.db.update_pipeline_run(
                pipeline_run.id,
                completed_at=datetime.now(timezone.utc),
                steps_json="{}",
                summary_json=_json.dumps(summary),
            )
        except Exception:
            pass

        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE")
        logger.info(f"  Discovered: {len(new_jobs)}")
        logger.info(f"  Filtered: {len(filtered)}")
        logger.info(f"  Scored: {len(scored)}")
        logger.info(f"  Evaluated: {evaluated_count}")
        logger.info(f"  PDFs: {pdf_count}")
        logger.info(f"  Applied: {applied_count}")
        logger.info(f"  Manual: {manual_count}")
        logger.info("=" * 60)

        return {
            "new_jobs": len(new_jobs),
            "filtered": len(filtered),
            "scored": len(scored),
            "evaluated": evaluated_count,
            "pdfs": pdf_count,
            "applied": applied_count,
            "manual_needed": manual_count,
        }

    async def discover(self) -> list[Job]:
        """Run job discovery. LinkedIn is the primary source; others are fallbacks."""
        # Primary source: LinkedIn (broadest coverage, real job URLs)
        primary = [LinkedInSource(max_results_per_query=100)]

        # Fallback sources: ATS APIs and SerpAPI
        fallbacks = []
        gh_tokens = self.registry.get_greenhouse_tokens()
        if gh_tokens:
            fallbacks.append(GreenhouseSource(gh_tokens, self.settings.role_keywords))
        lever_slugs = self.registry.get_lever_slugs()
        if lever_slugs:
            fallbacks.append(LeverSource(lever_slugs, self.settings.role_keywords))
        if self.settings.serpapi_key:
            fallbacks.append(SerpAPISource(self.settings.serpapi_key))

        # Run LinkedIn first as primary
        orchestrator = DiscoveryOrchestrator(primary, self.db)
        primary_jobs = await orchestrator.run_discovery(
            queries=self.settings.discovery.queries,
            location=self.settings.discovery.location,
            posted_within_days=self.settings.discovery.posted_within_days,
        )
        logger.info(f"  LinkedIn (primary): {len(primary_jobs)} new jobs")

        # Run fallback sources for additional coverage
        fallback_jobs: list[Job] = []
        if fallbacks:
            fallback_orchestrator = DiscoveryOrchestrator(fallbacks, self.db)
            fallback_jobs = await fallback_orchestrator.run_discovery(
                queries=self.settings.discovery.queries,
                location=self.settings.discovery.location,
                posted_within_days=self.settings.discovery.posted_within_days,
            )
            logger.info(f"  Fallbacks (Greenhouse/Lever/SerpAPI): {len(fallback_jobs)} new jobs")

        return primary_jobs + fallback_jobs

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
        """Auto-apply to a single job via Playwright (Greenhouse/Lever/Workday)."""
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
