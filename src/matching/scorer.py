from __future__ import annotations

import json
import logging
from pathlib import Path

from src.matching.llm_client import LLMClient
from src.tracking.models import Job, MatchScore, ParsedJob

logger = logging.getLogger(__name__)

SCORE_PROMPT = """You are a job match scorer. Given a candidate's resume and a parsed job description, evaluate how well the candidate matches the job.

Score each dimension from 0-100:
- skill_score: How many required and preferred skills does the candidate have?
- experience_score: Does the candidate have the right years and type of experience?
- seniority_score: Is the seniority level a good fit for the candidate's career stage?
- overall_score: Overall match quality (weighted average, with your judgment)

Also provide a 2-3 sentence reasoning explaining the score.

Return a JSON object:
{{
    "overall_score": <0-100>,
    "skill_score": <0-100>,
    "experience_score": <0-100>,
    "seniority_score": <0-100>,
    "reasoning": "<2-3 sentence explanation>"
}}

CANDIDATE RESUME:
{resume_text}

JOB DETAILS:
- Title: {title}
- Company: {company}
- Seniority: {seniority}
- Required Skills: {required_skills}
- Preferred Skills: {preferred_skills}
- Years Experience: {years_exp}
- Education: {education}
- Location: {location}
- Remote Policy: {remote_policy}
- Key Responsibilities: {responsibilities}

Return ONLY the JSON object, no other text."""


class JobScorer:
    """Score jobs against resume using LLM."""

    def __init__(self, llm: LLMClient, resume_text_path: Path):
        self.llm = llm
        self._resume_text = ""
        if resume_text_path.exists():
            self._resume_text = resume_text_path.read_text(encoding="utf-8")

    async def score(self, job: Job, parsed: ParsedJob) -> MatchScore:
        years_exp = ""
        if parsed.years_experience_min or parsed.years_experience_max:
            years_exp = f"{parsed.years_experience_min or '?'}-{parsed.years_experience_max or '?'} years"

        prompt = SCORE_PROMPT.format(
            resume_text=self._resume_text[:3000],
            title=parsed.title,
            company=parsed.company,
            seniority=parsed.seniority,
            required_skills=", ".join(parsed.required_skills),
            preferred_skills=", ".join(parsed.preferred_skills),
            years_exp=years_exp,
            education=parsed.education_requirement,
            location=parsed.location,
            remote_policy=parsed.remote_policy,
            responsibilities="; ".join(parsed.responsibilities[:5]),
        )

        try:
            response = await self.llm.complete(prompt, max_tokens=500)
            data = self._extract_json(response)
            return MatchScore(
                job_id=job.id,
                overall_score=max(0, min(100, data.get("overall_score", 0))),
                skill_score=max(0, min(100, data.get("skill_score", 0))),
                experience_score=max(0, min(100, data.get("experience_score", 0))),
                seniority_score=max(0, min(100, data.get("seniority_score", 0))),
                reasoning=data.get("reasoning", ""),
                model_used=self.llm.settings.matching.scoring_model,
            )
        except Exception as e:
            logger.error(f"Failed to score job {job.title} at {job.company}: {e}")
            return MatchScore(
                job_id=job.id,
                overall_score=0,
                skill_score=0,
                experience_score=0,
                seniority_score=0,
                reasoning=f"Scoring failed: {e}",
                model_used="error",
            )

    @staticmethod
    def _extract_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
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
