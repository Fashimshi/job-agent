from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from src.tracking.models import ATSType

logger = logging.getLogger(__name__)


class CompanyRegistry:
    """Load and query the company whitelist/blacklist from companies.yaml."""

    def __init__(self, companies_path: Path):
        with open(companies_path) as f:
            data = yaml.safe_load(f)

        self._whitelist: list[dict[str, Any]] = data.get("whitelist", [])
        self._blacklist_patterns: dict[str, Any] = data.get("blacklist_patterns", {})

        # Build lookup indices
        self._name_index: dict[str, dict[str, Any]] = {}
        self._greenhouse_tokens: dict[str, str] = {}  # company_name -> token
        self._lever_slugs: dict[str, str] = {}  # company_name -> slug

        for entry in self._whitelist:
            name_lower = entry["name"].lower()
            self._name_index[name_lower] = entry

            for alias in entry.get("aliases", []):
                self._name_index[alias.lower()] = entry

            if entry.get("greenhouse_token"):
                self._greenhouse_tokens[entry["name"]] = entry["greenhouse_token"]

            if entry.get("lever_slug"):
                self._lever_slugs[entry["name"]] = entry["lever_slug"]

    def is_whitelisted(self, company_name: str) -> bool:
        return company_name.lower().strip() in self._name_index

    def is_excluded_from_apply(self, company_name: str) -> bool:
        entry = self._name_index.get(company_name.lower().strip())
        if entry:
            return entry.get("exclude_from_apply", False)
        return False

    def get_ats_type(self, company_name: str) -> ATSType:
        entry = self._name_index.get(company_name.lower().strip())
        if entry:
            ats = entry.get("ats", "unknown")
            try:
                return ATSType(ats)
            except ValueError:
                return ATSType.UNKNOWN
        return ATSType.UNKNOWN

    def get_greenhouse_tokens(self) -> dict[str, str]:
        """Return {company_name: greenhouse_token} for all Greenhouse companies."""
        return dict(self._greenhouse_tokens)

    def get_lever_slugs(self) -> dict[str, str]:
        """Return {company_name: lever_slug} for all Lever companies."""
        return dict(self._lever_slugs)

    def get_company_entry(self, company_name: str) -> dict[str, Any] | None:
        return self._name_index.get(company_name.lower().strip())

    def is_likely_startup(self, company_name: str) -> bool:
        """Check if company matches blacklist patterns (startup indicators)."""
        name_lower = company_name.lower()
        for kw in self._blacklist_patterns.get("company_keywords", []):
            if kw.lower() in name_lower:
                return True
        return False

    @property
    def whitelisted_companies(self) -> list[str]:
        return [e["name"] for e in self._whitelist]
