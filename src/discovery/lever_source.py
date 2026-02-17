from __future__ import annotations

import asyncio
import logging

import httpx

from src.discovery.base import JobSource
from src.tracking.models import ATSType, RawJob

logger = logging.getLogger(__name__)

LEVER_API_BASE = "https://api.lever.co/v0/postings"


class LeverSource(JobSource):
    """Fetch jobs from Lever Postings API (free, no key required)."""

    source_name = "lever"

    def __init__(self, company_slugs: dict[str, str], role_keywords: list[str] | None = None):
        """
        Args:
            company_slugs: Mapping of company name to Lever slug.
                e.g. {"Spotify": "spotify", "Scale AI": "scaleai"}
            role_keywords: Keywords to match against job titles.
        """
        self.company_slugs = company_slugs
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
                self._fetch_company_jobs(client, company, slug, queries)
                for company, slug in self.company_slugs.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Lever fetch error: {result}")
                continue
            all_jobs.extend(result)

        logger.info(f"Lever: found {len(all_jobs)} matching jobs across {len(self.company_slugs)} companies")
        return all_jobs

    async def _fetch_company_jobs(
        self,
        client: httpx.AsyncClient,
        company: str,
        slug: str,
        queries: list[str],
    ) -> list[RawJob]:
        url = f"{LEVER_API_BASE}/{slug}"
        try:
            resp = await client.get(url, params={"mode": "json"})
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch Lever jobs for {company} ({slug}): {e}")
            return []

        postings = resp.json()
        if not isinstance(postings, list):
            logger.warning(f"Unexpected Lever response for {company}: {type(postings)}")
            return []

        matched: list[RawJob] = []
        for posting in postings:
            title = posting.get("text", "")
            title_lower = title.lower()

            if not self._matches_queries(title_lower):
                continue

            posting_id = posting.get("id", "")
            categories = posting.get("categories", {})
            location_str = categories.get("location", "") or ""
            team = categories.get("team", "") or ""

            # Build description from available fields
            description_parts = []
            if posting.get("descriptionPlain"):
                description_parts.append(posting["descriptionPlain"])
            for li in posting.get("lists", []):
                description_parts.append(f"\n{li.get('text', '')}:")
                description_parts.append(li.get("content", ""))

            description = "\n".join(description_parts)

            posting_url = posting.get("hostedUrl", f"https://jobs.lever.co/{slug}/{posting_id}")
            apply_url = posting.get("applyUrl", f"{posting_url}/apply")

            matched.append(
                RawJob(
                    source="lever",
                    external_id=str(posting_id),
                    title=title,
                    company=company,
                    location=location_str,
                    posting_url=posting_url,
                    apply_url=apply_url,
                    description_raw=description,
                    ats_type=ATSType.LEVER,
                    posted_date=str(posting.get("createdAt", "")),
                )
            )

        logger.info(f"  {company}: {len(matched)} matching jobs (of {len(postings)} total)")
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
