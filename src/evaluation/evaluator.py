"""A-F Job Evaluation Engine.

Calls Claude API to produce deep structured evaluation for top-scoring jobs.
Replaces the interactive Claude Code evaluation with an automated API-driven approach.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.evaluation.prompts import build_evaluation_prompt, build_report_markdown
from src.evaluation.story_bank import extract_stories
from src.tracking.models import Artifact, Job, MatchScore

if TYPE_CHECKING:
    from src.matching.llm_client import LLMClient
    from src.tracking.database import Database

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class JobEvaluator:
    """Evaluates jobs using Claude API with career-ops A-F block framework."""

    def __init__(self, llm: LLMClient, db: Database, resume_text: str):
        self.llm = llm
        self.db = db
        self.resume_text = resume_text

    async def evaluate(self, job: Job, score: MatchScore) -> dict | None:
        """Run full A-F evaluation for a single job. Returns evaluation dict or None on failure."""
        logger.info(f"  Evaluating: {job.title} at {job.company} (score: {score.overall_score})")

        prompt = build_evaluation_prompt(
            job_title=job.title,
            company=job.company,
            job_description=job.description_raw or "",
            resume_text=self.resume_text,
        )

        try:
            response = await self.llm.complete(
                prompt=prompt,
                model="gpt-4o",
                max_tokens=4000,
            )

            evaluation = self._parse_json(response)
            if not evaluation:
                logger.error(f"Failed to parse evaluation JSON for {job.title} at {job.company}")
                return None

            # Store evaluation in database
            eval_json = json.dumps(evaluation, ensure_ascii=False)
            self.db.update_evaluation(job.id, eval_json)

            # Generate and save report
            report_num = self._get_next_report_num()
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            company_slug = re.sub(r"[^a-z0-9]+", "-", job.company.lower()).strip("-")

            report_md = build_report_markdown(
                job_title=job.title,
                company=job.company,
                evaluation=evaluation,
                report_num=report_num,
                date=date,
                job_url=job.posting_url or "",
            )

            report_path = PROJECT_ROOT / "reports" / f"{report_num}-{company_slug}-{date}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_md, encoding="utf-8")

            # Store artifact
            artifact = Artifact(
                job_id=job.id,
                type="report_md",
                file_path=str(report_path.relative_to(PROJECT_ROOT)),
                created_at=datetime.now(timezone.utc),
            )
            self.db.insert_artifact(artifact)

            # Extract and store STAR stories
            stories = extract_stories(evaluation, job.id)
            for story in stories:
                self.db.insert_story(story)

            score_5 = evaluation.get("score_5", 0)
            recommendation = evaluation.get("recommendation", "?")
            logger.info(
                f"  Evaluated: {score_5}/5 ({recommendation}) - "
                f"{job.title} at {job.company} -> {report_path.name}"
            )

            return evaluation

        except Exception as e:
            logger.error(f"Evaluation failed for {job.title} at {job.company}: {e}")
            return None

    def _parse_json(self, response: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown code blocks."""
        response = response.strip()

        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in response
        brace_start = response.find("{")
        brace_end = response.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(response[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _get_next_report_num(self) -> str:
        """Get next sequential report number (3 digits, zero-padded)."""
        reports_dir = PROJECT_ROOT / "reports"
        if not reports_dir.exists():
            return "001"

        max_num = 0
        for f in reports_dir.iterdir():
            if f.suffix == ".md":
                match = re.match(r"(\d+)", f.name)
                if match:
                    max_num = max(max_num, int(match.group(1)))

        return str(max_num + 1).zfill(3)
