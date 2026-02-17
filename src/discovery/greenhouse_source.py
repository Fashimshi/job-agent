from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.discovery.base import JobSource
from src.tracking.models import ATSType, RawJob

logger = logging.getLogger(__name__)

GREENHOUSE_BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseSource(JobSource):
    """Fetch jobs from Greenhouse Board API (free, no key required)."""

    source_name = "greenhouse"

    def __init__(self, company_tokens: dict[str, str], role_keywords: list[str] | None = None):
        """
        Args:
            company_tokens: Mapping of company name to Greenhouse board token.
                e.g. {"Meta": "meta", "Netflix": "netflix"}
            role_keywords: Keywords to match against job titles.
        """
        self.company_tokens = company_tokens
        self.role_keywords = [kw.lower() for kw in (role_keywords or [])]

    async def fetch_jobs(
        self,
        queries: list[str],
        location: str = "United States",
        posted_within_days: int = 7,
    ) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [
                self._fetch_company_jobs(client, company, token, queries)
                for company, token in self.company_tokens.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Greenhouse fetch error: {result}")
                continue
            all_jobs.extend(result)

        logger.info(f"Greenhouse: found {len(all_jobs)} matching jobs across {len(self.company_tokens)} companies")
        return all_jobs

    async def _fetch_company_jobs(
        self,
        client: httpx.AsyncClient,
        company: str,
        token: str,
        queries: list[str],
    ) -> list[RawJob]:
        url = f"{GREENHOUSE_BOARDS_API}/{token}/jobs"
        try:
            resp = await client.get(url, params={"content": "true"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch Greenhouse jobs for {company} ({token}): {e}")
            return []

        data = resp.json()
        jobs_data = data.get("jobs", [])
        query_lower = [q.lower() for q in queries]

        matched: list[RawJob] = []
        for job_data in jobs_data:
            title = job_data.get("title", "")
            title_lower = title.lower()

            # Check if job title matches any of our search queries
            if not self._matches_queries(title_lower):
                continue

            # Extract description text from HTML
            content = job_data.get("content", "")
            description = self._html_to_text(content) if content else ""

            # Extract location
            loc = self._extract_location(job_data)

            job_id = str(job_data.get("id", ""))
            posting_url = f"https://boards.greenhouse.io/{token}/jobs/{job_id}"
            apply_url = f"https://boards.greenhouse.io/embed/job_app?for={token}&token={job_id}"

            matched.append(
                RawJob(
                    source="greenhouse",
                    external_id=job_id,
                    title=title,
                    company=company,
                    location=loc,
                    posting_url=posting_url,
                    apply_url=apply_url,
                    description_raw=description,
                    ats_type=ATSType.GREENHOUSE,
                    posted_date=job_data.get("updated_at", ""),
                )
            )

        logger.info(f"  {company}: {len(matched)} matching jobs (of {len(jobs_data)} total)")
        return matched

    def _matches_queries(self, title_lower: str) -> bool:
        """Check if a job title matches any configured role keywords."""
        if not self.role_keywords:
            # Fallback to default keywords if none configured
            default_keywords = [
                "data scien", "machine learning", "ml ", "nlp",
                "ai engineer", "applied scientist", "research scientist",
                "llm", "deep learning",
            ]
            return any(kw in title_lower for kw in default_keywords)
        return any(kw in title_lower for kw in self.role_keywords)

    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    @staticmethod
    def _extract_location(job_data: dict[str, Any]) -> str:
        location = job_data.get("location", {})
        if isinstance(location, dict):
            return location.get("name", "")
        return str(location) if location else ""
