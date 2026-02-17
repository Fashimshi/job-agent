from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.tracking.models import Job

if TYPE_CHECKING:
    from src.config_loader import Settings

logger = logging.getLogger(__name__)

# Company tiers — higher tier = higher priority
# Tier 1: FAANG
TIER_1_FAANG = {
    "google", "alphabet", "google deepmind", "apple", "amazon", "aws",
    "meta", "facebook", "netflix",
}

# Tier 2: Big Tech
TIER_2_BIG_TECH = {
    "microsoft", "nvidia", "adobe", "salesforce", "oracle", "ibm",
    "intel", "cisco", "qualcomm", "broadcom", "amd",
    "uber", "lyft", "airbnb", "doordash", "stripe", "databricks",
    "snowflake", "palantir", "openai", "anthropic", "tesla",
    "spotify", "pinterest", "snap", "reddit", "linkedin",
    "cloudflare", "datadog", "twilio", "mongodb", "square", "block",
    "coinbase", "robinhood", "figma", "notion", "ramp", "scale ai",
}

# Tier 3: Mid-tier Tech
TIER_3_MID_TECH = {
    "samsung", "sony", "dell", "hp", "vmware", "servicenow",
    "splunk", "elastic", "confluent", "hashicorp", "gitlab",
    "atlassian", "hubspot", "zoom", "okta", "crowdstrike",
    "palo alto networks", "fortinet", "zscaler", "docusign",
    "dropbox", "box", "asana", "monday.com", "canva",
    "grammarly", "instacart", "wayfair", "chewy", "etsy",
    "roblox", "ea", "electronic arts", "activision", "riot games",
    "grafana labs", "vercel", "supabase", "retool", "plaid",
    "fetch", "toast", "gusto", "rippling",
}

# Tier 4: Banks & Investment Firms
TIER_4_FINANCE = {
    "jpmorgan", "jpmorgan chase", "jp morgan", "goldman sachs",
    "morgan stanley", "bank of america", "wells fargo", "citigroup",
    "citi", "capital one", "american express", "visa", "mastercard",
    "paypal", "fidelity", "blackrock", "vanguard", "citadel",
    "two sigma", "de shaw", "jane street", "bridgewater",
    "point72", "aqr", "renaissance technologies",
    "charles schwab", "td ameritrade", "sofi", "robinhood",
    "barclays", "hsbc", "deutsche bank", "ubs", "credit suisse",
}

# Startup indicators — always reject
STARTUP_INDICATORS = {
    "stealth", "pre-revenue", "early-stage", "seed-stage",
}

STARTUP_TITLE_INDICATORS = {
    "founding", "co-founder", "cofounder",
}


def get_company_tier(company_name: str) -> int:
    """Return company tier (1=FAANG, 2=Big Tech, 3=Mid Tech, 4=Finance, 5=Other established).
    Returns 0 for startups/unknown."""
    name = company_name.lower().strip()

    if name in TIER_1_FAANG:
        return 1
    if name in TIER_2_BIG_TECH:
        return 2
    if name in TIER_3_MID_TECH:
        return 3
    if name in TIER_4_FINANCE:
        return 4

    # Check for startup indicators
    for indicator in STARTUP_INDICATORS:
        if indicator in name:
            return 0

    return 5  # Other established / unknown


class JobFilter:
    """Fast pre-LLM filters to reject obviously non-matching jobs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.role_keywords = [kw.lower() for kw in settings.role_keywords]
        self.seniority_include = [s.lower() for s in settings.seniority.include]
        self.seniority_exclude = [s.lower() for s in settings.seniority.exclude]

    def apply_all(self, jobs: list[Job]) -> list[Job]:
        """Apply all filters, sort by company tier, return passing jobs."""
        initial = len(jobs)
        passed = [j for j in jobs if self.passes(j)]

        # Sort by company tier (FAANG first, then Big Tech, etc.)
        passed.sort(key=lambda j: get_company_tier(j.company))

        logger.info(f"Filters: {len(passed)}/{initial} jobs passed")
        return passed

    def passes(self, job: Job) -> bool:
        title_lower = job.title.lower()
        company_lower = job.company.lower().strip()
        return (
            self._keyword_filter(title_lower)
            and self._seniority_filter(title_lower)
            and self._not_startup(company_lower, title_lower)
        )

    def _keyword_filter(self, title_lower: str) -> bool:
        """Job title must contain at least one relevant role keyword."""
        return any(kw in title_lower for kw in self.role_keywords)

    def _seniority_filter(self, title_lower: str) -> bool:
        """Reject excluded seniority levels. If include list is set, require match."""
        for excluded in self.seniority_exclude:
            if excluded in title_lower:
                return False

        if self.seniority_include:
            return any(s in title_lower for s in self.seniority_include)

        return True

    def _not_startup(self, company_lower: str, title_lower: str) -> bool:
        """Reject startups and suspicious small companies."""
        # Check company name for startup indicators
        for indicator in STARTUP_INDICATORS:
            if indicator in company_lower:
                logger.debug(f"Filtered out startup: {company_lower}")
                return False

        # Check title for founding-role indicators
        for indicator in STARTUP_TITLE_INDICATORS:
            if indicator in title_lower:
                logger.debug(f"Filtered out founding role: {title_lower}")
                return False

        return True
