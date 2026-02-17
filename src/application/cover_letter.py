from __future__ import annotations

import logging
from pathlib import Path

from src.matching.llm_client import LLMClient
from src.tracking.models import MatchScore, ParsedJob

logger = logging.getLogger(__name__)

COVER_LETTER_PROMPT = """Write a professional cover letter for the following job application.

IMPORTANT GUIDELINES:
- Length: 250-350 words
- Tone: Professional but genuine, not generic
- Specifically reference the company name and role
- Highlight the 2-3 most relevant experiences from the resume that match this role
- Show understanding of the company's work and how the candidate can contribute
- Do NOT start with "Dear Hiring Manager" cliches — use a more modern, direct opening
- Do NOT include the candidate's address or date — this will be used in online applications
- End with a confident but not arrogant closing

CANDIDATE RESUME:
{resume_text}

JOB DETAILS:
- Title: {title}
- Company: {company}
- Required Skills: {required_skills}
- Key Responsibilities: {responsibilities}

MATCH ANALYSIS:
{reasoning}

Write the cover letter now:"""


class CoverLetterGenerator:
    """Generate tailored cover letters using LLM."""

    def __init__(self, llm: LLMClient, resume_text_path: Path):
        self.llm = llm
        self._resume_text = ""
        if resume_text_path.exists():
            self._resume_text = resume_text_path.read_text(encoding="utf-8")

    async def generate(self, parsed: ParsedJob, match_score: MatchScore) -> str:
        prompt = COVER_LETTER_PROMPT.format(
            resume_text=self._resume_text[:3000],
            title=parsed.title,
            company=parsed.company,
            required_skills=", ".join(parsed.required_skills),
            responsibilities="; ".join(parsed.responsibilities[:5]),
            reasoning=match_score.reasoning,
        )

        try:
            cover_letter = await self.llm.complete_cover_letter(prompt, max_tokens=1500)
            return cover_letter.strip()
        except Exception as e:
            logger.error(f"Failed to generate cover letter for {parsed.title} at {parsed.company}: {e}")
            return ""
