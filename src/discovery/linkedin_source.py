from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from src.discovery.base import JobSource
from src.tracking.models import ATSType, RawJob

logger = logging.getLogger(__name__)

# LinkedIn's public guest jobs API — no authentication required
LINKEDIN_JOBS_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Time filter mapping (seconds)
TIME_FILTERS = {
    1: "r86400",      # past 24 hours
    3: "r259200",     # past 3 days
    7: "r604800",     # past week
    30: "r2592000",   # past month
}


class LinkedInSource(JobSource):
    """Fetch jobs from LinkedIn's public jobs search (no auth required)."""

    source_name = "linkedin"

    def __init__(self, max_results_per_query: int = 25):
        self.max_results_per_query = max_results_per_query

    async def fetch_jobs(
        self,
        queries: list[str],
        location: str = "United States",
        posted_within_days: int = 7,
    ) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        time_filter = self._get_time_filter(posted_within_days)

        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            for query in queries:
                try:
                    jobs = await self._search_query(
                        client, query, location, time_filter
                    )
                    all_jobs.extend(jobs)
                    logger.info(f"  LinkedIn '{query}': {len(jobs)} jobs found")
                except Exception as e:
                    logger.error(f"LinkedIn error for '{query}': {e}")
                # Be polite — small delay between queries
                await asyncio.sleep(1.5)

        # Deduplicate by (company, title)
        seen = set()
        unique_jobs = []
        for job in all_jobs:
            key = (job.company.lower(), job.title.lower())
            if key not in seen:
                seen.add(key)
                unique_jobs.append(job)

        # Fetch full descriptions for each job
        logger.info(f"LinkedIn: fetching descriptions for {len(unique_jobs)} jobs...")
        async with httpx.AsyncClient(
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            sem = asyncio.Semaphore(5)
            async def _fetch_desc(job: RawJob) -> None:
                async with sem:
                    try:
                        desc = await self._fetch_job_description(client, job.posting_url)
                        if desc:
                            job.description_raw = desc
                    except Exception as e:
                        logger.debug(f"Failed to fetch description for {job.title}: {e}")
                    await asyncio.sleep(0.5)

            await asyncio.gather(*[_fetch_desc(j) for j in unique_jobs])

        logger.info(
            f"LinkedIn: found {len(unique_jobs)} unique jobs across {len(queries)} queries"
        )
        return unique_jobs

    async def _search_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        location: str,
        time_filter: str,
    ) -> list[RawJob]:
        raw_jobs: list[RawJob] = []
        start = 0
        page_size = 25

        while start < self.max_results_per_query:
            params: dict[str, Any] = {
                "keywords": query,
                "location": location,
                "start": start,
                "f_TPR": time_filter,
            }

            resp = await client.get(LINKEDIN_JOBS_API, params=params)
            if resp.status_code != 200:
                logger.warning(
                    f"LinkedIn returned {resp.status_code} for '{query}' at offset {start}"
                )
                break

            jobs = self._parse_job_cards(resp.text)
            if not jobs:
                break

            raw_jobs.extend(jobs)
            start += page_size

            if len(jobs) < page_size:
                break

            await asyncio.sleep(1.0)

        return raw_jobs

    def _parse_job_cards(self, html: str) -> list[RawJob]:
        """Parse LinkedIn job cards from the HTML response."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="base-card")

        jobs: list[RawJob] = []
        for card in cards:
            try:
                job = self._parse_single_card(card)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.debug(f"Failed to parse LinkedIn card: {e}")
                continue

        return jobs

    def _parse_single_card(self, card: Any) -> RawJob | None:
        """Parse a single LinkedIn job card."""
        # Title
        title_el = card.find("h3", class_="base-search-card__title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        # Company
        company_el = card.find("h4", class_="base-search-card__subtitle")
        company = company_el.get_text(strip=True) if company_el else ""

        # Location
        location_el = card.find("span", class_="job-search-card__location")
        location = location_el.get_text(strip=True) if location_el else ""

        # Job URL
        link_el = card.find("a", class_="base-card__full-link")
        posting_url = ""
        if link_el and link_el.get("href"):
            posting_url = link_el["href"].split("?")[0]  # Strip tracking params

        # Posted date
        time_el = card.find("time")
        posted_date = ""
        if time_el:
            posted_date = time_el.get("datetime", "") or time_el.get_text(strip=True)

        # External ID from URL
        external_id = ""
        if posting_url and "view/" in posting_url:
            external_id = posting_url.split("view/")[-1].rstrip("/")
        elif posting_url:
            parts = posting_url.rstrip("/").split("-")
            if parts:
                external_id = parts[-1]

        # Detect ATS from apply URL (we'll get this from the job page later)
        ats_type = ATSType.UNKNOWN

        if not title or not posting_url:
            return None

        return RawJob(
            source="linkedin",
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            posting_url=posting_url,
            apply_url=posting_url,
            description_raw="",  # Would need a second request per job to get full description
            ats_type=ats_type,
            posted_date=posted_date,
        )

    async def _fetch_job_description(self, client: httpx.AsyncClient, url: str) -> str:
        """Fetch full job description from LinkedIn job detail page."""
        if not url:
            return ""
        resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        # LinkedIn wraps description in a specific div
        desc_div = soup.find("div", class_="show-more-less-html__markup")
        if desc_div:
            return desc_div.get_text(separator="\n", strip=True)
        # Fallback: try the description meta tag
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            return meta["content"]
        return ""

    @staticmethod
    def _get_time_filter(days: int) -> str:
        if days <= 1:
            return TIME_FILTERS[1]
        if days <= 3:
            return TIME_FILTERS[3]
        if days <= 7:
            return TIME_FILTERS[7]
        if days <= 30:
            return TIME_FILTERS[30]
        return ""
