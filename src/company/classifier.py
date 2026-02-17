from __future__ import annotations

import logging

from src.company.registry import CompanyRegistry
from src.matching.llm_client import LLMClient

logger = logging.getLogger(__name__)

# Major established companies (not in whitelist but still valid targets)
KNOWN_ESTABLISHED = {
    "google", "apple", "microsoft", "amazon", "meta", "netflix", "nvidia",
    "adobe", "salesforce", "intel", "ibm", "oracle", "cisco", "qualcomm",
    "broadcom", "texas instruments", "applied materials", "amd",
    "jpmorgan", "goldman sachs", "morgan stanley", "bank of america",
    "wells fargo", "citigroup", "capital one", "american express",
    "visa", "mastercard", "paypal", "fidelity", "blackrock",
    "walmart", "target", "costco", "home depot",
    "disney", "comcast", "at&t", "verizon",
    "johnson & johnson", "pfizer", "unitedhealth",
    "procter & gamble", "coca-cola", "pepsico",
    "boeing", "lockheed martin", "raytheon",
    "tesla", "uber", "lyft", "airbnb", "doordash",
    "stripe", "databricks", "snowflake", "palantir",
    "twilio", "datadog", "cloudflare", "mongodb",
    "spotify", "pinterest", "snap", "reddit",
    "samsung", "sony", "lg",
}


class CompanyClassifier:
    """Classify unknown companies as established vs startup."""

    def __init__(self, registry: CompanyRegistry, llm: LLMClient | None = None):
        self.registry = registry
        self.llm = llm

    async def is_established(self, company_name: str) -> bool:
        """Returns True if the company is established (not a startup)."""
        # Check whitelist first
        if self.registry.is_whitelisted(company_name):
            return True

        # Check known established companies
        if company_name.lower().strip() in KNOWN_ESTABLISHED:
            return True

        # Check blacklist patterns
        if self.registry.is_likely_startup(company_name):
            return False

        # If LLM is available, ask it
        if self.llm:
            return await self._llm_classify(company_name)

        # Default: unknown companies are excluded (conservative)
        return False

    async def _llm_classify(self, company_name: str) -> bool:
        prompt = f"""Is "{company_name}" an established company (publicly traded, 1000+ employees, or well-known brand)?
Answer with ONLY "yes" or "no"."""
        try:
            response = await self.llm.complete(prompt, max_tokens=10)
            return response.strip().lower().startswith("yes")
        except Exception as e:
            logger.error(f"LLM classification failed for {company_name}: {e}")
            return False
