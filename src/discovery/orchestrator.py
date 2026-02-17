from __future__ import annotations

import asyncio
import logging

from src.discovery.base import JobSource
from src.tracking.database import Database
from src.tracking.models import Job, RawJob

logger = logging.getLogger(__name__)


class DiscoveryOrchestrator:
    """Run all job sources in parallel, deduplicate, and store results."""

    def __init__(self, sources: list[JobSource], db: Database):
        self.sources = sources
        self.db = db

    async def run_discovery(
        self,
        queries: list[str],
        location: str = "United States",
        posted_within_days: int = 7,
    ) -> list[Job]:
        """
        Fetch from all sources, deduplicate, store new jobs.
        Returns only newly discovered jobs.
        """
        # Run all sources in parallel
        tasks = [
            source.fetch_jobs(queries, location, posted_within_days)
            for source in self.sources
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_raw: list[RawJob] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Source {self.sources[i].source_name} failed: {result}")
                continue
            all_raw.extend(result)

        logger.info(f"Total raw jobs from all sources: {len(all_raw)}")

        # Deduplicate across sources
        unique_raw = self._deduplicate(all_raw)
        logger.info(f"After cross-source deduplication: {len(unique_raw)}")

        # Store new jobs
        new_jobs: list[Job] = []
        for raw in unique_raw:
            job = Job(**raw.model_dump())

            if self.db.job_exists(job.company, job.title, job.posting_url):
                continue

            inserted = self.db.insert_job(job)
            if inserted:
                new_jobs.append(job)

        logger.info(f"New jobs stored: {len(new_jobs)}")
        return new_jobs

    @staticmethod
    def _deduplicate(jobs: list[RawJob]) -> list[RawJob]:
        """Deduplicate by normalized (company, title, posting_url)."""
        seen: dict[tuple[str, str], RawJob] = {}

        for job in jobs:
            key = (
                job.company.lower().strip(),
                job.title.lower().strip(),
            )
            if key in seen:
                # Priority: greenhouse/lever (auto-apply) > linkedin (real URLs) > serpapi
                existing = seen[key]
                priority = {"greenhouse": 3, "lever": 3, "linkedin": 2, "serpapi": 1}
                if priority.get(job.source, 0) > priority.get(existing.source, 0):
                    seen[key] = job
            else:
                seen[key] = job

        return list(seen.values())
