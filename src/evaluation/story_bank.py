"""Extract STAR+R stories from A-F evaluations and accumulate in story bank."""
from __future__ import annotations

import json

from src.tracking.models import StoryBankEntry


def extract_stories(evaluation: dict, job_id: str) -> list[StoryBankEntry]:
    """Extract STAR+R stories from Block F of an evaluation."""
    block_f = evaluation.get("block_f", {})
    stories = block_f.get("star_stories", [])

    entries = []
    for story in stories:
        title = story.get("title", "")
        if not title:
            continue

        entries.append(StoryBankEntry(
            story_title=title,
            situation=story.get("situation", ""),
            task=story.get("task", ""),
            action=story.get("action", ""),
            result=story.get("result", ""),
            reflection=story.get("reflection", ""),
            source_job_ids=json.dumps([job_id]),
            tags=json.dumps([story.get("requirement", "")]),
        ))

    return entries
