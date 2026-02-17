from __future__ import annotations

import logging
from typing import Any

import httpx

from src.discovery.base import JobSource
from src.tracking.models import ATSType, RawJob

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"


class SerpAPISource(JobSource):
    """Fetch jobs via SerpAPI Google Jobs engine (100 free searches/month)."""

    source_name = "serpapi"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def fetch_jobs(
        self,
        queries: list[str],
        location: str = "United States",
        posted_within_days: int = 7,
    ) -> list[RawJob]:
        if not self.api_key:
            logger.warning("SerpAPI key not configured, skipping")
            return []

        all_jobs: list[RawJob] = []
        chips = self._days_to_chips(posted_within_days)

        async with httpx.AsyncClient(timeout=30) as client:
            for query in queries:
                try:
                    jobs = await self._search_query(client, query, location, chips)
                    all_jobs.extend(jobs)
                    logger.info(f"  SerpAPI '{query}': {len(jobs)} jobs found")
                except Exception as e:
                    logger.error(f"SerpAPI error for '{query}': {e}")

        # Deduplicate by (company, title)
        seen = set()
        unique_jobs = []
        for job in all_jobs:
            key = (job.company.lower(), job.title.lower())
            if key not in seen:
                seen.add(key)
                unique_jobs.append(job)

        logger.info(f"SerpAPI: found {len(unique_jobs)} unique jobs across {len(queries)} queries")
        return unique_jobs

    async def _search_query(
        self,
        client: httpx.AsyncClient,
        query: str,
        location: str,
        chips: str,
    ) -> list[RawJob]:
        params: dict[str, Any] = {
            "engine": "google_jobs",
            "q": query,
            "location": location,
            "api_key": self.api_key,
        }
        if chips:
            params["chips"] = chips

        resp = await client.get(SERPAPI_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        jobs_results = data.get("jobs_results", [])
        raw_jobs: list[RawJob] = []

        for result in jobs_results:
            title = result.get("title", "")
            company = result.get("company_name", "")
            loc = result.get("location", "")
            description = result.get("description", "")

            # Extract real apply URLs from apply_options (not the Google share link)
            apply_options = result.get("apply_options", [])

            # Find the best apply URL — prefer direct company/ATS links
            apply_url = ""
            posting_url_candidate = ""
            for opt in apply_options:
                link = opt.get("link", "")
                if not link:
                    continue
                # Only accept links from legitimate sources
                link_lower = link.lower()
                if self._is_junk_site(link_lower):
                    continue
                # Prefer direct ATS links (auto-apply capable)
                if any(ats in link_lower for ats in [
                    "greenhouse.io", "lever.co", "myworkdayjobs",
                ]):
                    apply_url = link
                    break
                if not posting_url_candidate:
                    posting_url_candidate = link

            if not apply_url:
                apply_url = posting_url_candidate
            if not apply_url and apply_options:
                apply_url = apply_options[0].get("link", "")

            # Use LinkedIn job URL as posting_url if available
            posting_url = ""
            for opt in apply_options:
                link = opt.get("link", "")
                if "linkedin.com" in link.lower():
                    posting_url = link
                    break
            if not posting_url:
                posting_url = apply_url

            # Detect ATS type from apply URL
            ats_type = self._detect_ats(apply_url)

            # Get posting date info
            detected_extensions = result.get("detected_extensions", {})
            posted_at = detected_extensions.get("posted_at", "")

            # Skip jobs with no usable URL
            if not apply_url and not posting_url:
                continue

            raw_jobs.append(
                RawJob(
                    source="serpapi",
                    external_id=result.get("job_id", ""),
                    title=title,
                    company=company,
                    location=loc,
                    posting_url=posting_url or apply_url,
                    apply_url=apply_url,
                    description_raw=description,
                    ats_type=ats_type,
                    posted_date=posted_at,
                )
            )

        return raw_jobs

    @staticmethod
    def _detect_ats(url: str) -> ATSType:
        url_lower = url.lower()
        if "greenhouse.io" in url_lower:
            return ATSType.GREENHOUSE
        if "lever.co" in url_lower:
            return ATSType.LEVER
        if "myworkdayjobs" in url_lower or "workday" in url_lower:
            return ATSType.WORKDAY
        return ATSType.UNKNOWN

    # Job aggregator / spam sites — block the domain, not path
    _JUNK_DOMAINS = {
        "whatjobs.com", "bebee.com", "besbee.com", "jooble.org",
        "jobrapido.com", "talent.com", "salary.com", "neuvoo.com",
        "adzuna.com", "getwork.com", "lensa.com", "recruit.net",
        "jobcase.com", "learn4good.com", "simplyhired.com",
        "careerbuilder.com", "monster.com", "snagajob.com",
        "ziprecruiter.com", "dice.com", "hired.com",
        "wellfound.com", "builtin.com", "triplebyte.com",
        "ladders.com", "flexjobs.com", "remotejobsanywhere.com",
        "remotive.com", "weworkremotely.com", "remote.co",
        "jobspresso.co", "workingnomads.com", "nodesk.co",
        "jobgether.com", "otta.com", "cord.co", "huntr.co",
        "clickajobs.com", "jobisjob.com", "neuvoo.ca",
        "efinancialcareers.com", "ihire.com", "postjobfree.com",
        "zippia.com", "comparably.com", "payscale.com",
        "themuse.com", "idealist.org", "mediabistro.com",
        "startwire.com", "jobvite.com", "applytojob.com",
        "tarta.ai", "ai-jobs.net", "datajobs.com",
        "google.com",  # Google search redirects, not actual jobs
    }

    @classmethod
    def _is_junk_site(cls, url: str) -> bool:
        """Check if a URL belongs to a junk job aggregator."""
        from urllib.parse import urlparse
        try:
            hostname = urlparse(url).hostname or ""
        except Exception:
            return True
        # Strip "www." prefix
        if hostname.startswith("www."):
            hostname = hostname[4:]
        # Check against known junk domains
        if hostname in cls._JUNK_DOMAINS:
            return True
        # Also block if the root domain matches (e.g., us.jobrapido.com)
        parts = hostname.split(".")
        if len(parts) >= 2:
            root = ".".join(parts[-2:])
            if root in cls._JUNK_DOMAINS:
                return True
        return False

    @staticmethod
    def _days_to_chips(days: int) -> str:
        if days <= 1:
            return "date_posted:today"
        if days <= 3:
            return "date_posted:3days"
        if days <= 7:
            return "date_posted:week"
        if days <= 30:
            return "date_posted:month"
        return ""
