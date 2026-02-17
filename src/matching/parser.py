from __future__ import annotations

import json
import logging
from typing import Any

from src.matching.llm_client import LLMClient
from src.tracking.models import ParsedJob

logger = logging.getLogger(__name__)

PARSE_PROMPT = """You are a job description parser. Extract structured information from the following job description.

Return a JSON object with exactly these fields:
{{
    "title": "exact job title",
    "company": "company name",
    "seniority": "one of: junior, mid, senior, staff, lead, principal, manager, director, unknown",
    "required_skills": ["list", "of", "required", "skills"],
    "preferred_skills": ["list", "of", "preferred/nice-to-have", "skills"],
    "years_experience_min": null or integer,
    "years_experience_max": null or integer,
    "education_requirement": "degree requirement or empty string",
    "location": "job location",
    "remote_policy": "one of: remote, hybrid, onsite, unknown",
    "salary_range": "salary range string or null",
    "responsibilities": ["key", "responsibilities"]
}}

Job Title: {title}
Company: {company}

Job Description:
{description}

Return ONLY the JSON object, no other text."""


class JobParser:
    """Parse job descriptions into structured fields using LLM."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def parse(self, title: str, company: str, description: str) -> ParsedJob:
        prompt = PARSE_PROMPT.format(
            title=title,
            company=company,
            description=description[:4000],  # Truncate very long descriptions
        )

        try:
            response = await self.llm.complete(prompt, max_tokens=1000)
            data = self._extract_json(response)
            return ParsedJob(**data)
        except Exception as e:
            logger.error(f"Failed to parse job {title} at {company}: {e}")
            return ParsedJob(title=title, company=company)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
            json_lines = []
            started = False
            for line in lines:
                if line.strip().startswith("```") and not started:
                    started = True
                    continue
                if line.strip() == "```":
                    break
                if started:
                    json_lines.append(line)
            text = "\n".join(json_lines)
        return json.loads(text)
