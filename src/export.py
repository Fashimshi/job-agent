"""Export pipeline data for GitHub Pages dashboard and markdown readability.

Generates:
- dashboard/data.json (for static site)
- data/applications.md (for git readability)
- interview-prep/story-bank.md (for git readability)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.tracking.database import Database

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def export_dashboard(db: Database) -> str:
    """Export all pipeline data to dashboard/data.json. Returns the path."""
    dashboard_dir = PROJECT_ROOT / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    data_path = dashboard_dir / "data.json"

    # Get all data
    all_jobs = db.get_all_jobs_with_scores()
    stats = db.get_stats()
    stories = db.get_all_stories()
    runs = db.get_pipeline_runs(limit=30)

    # Build job entries
    jobs_export = []
    for row in all_jobs:
        eval_data = {}
        if row.get("evaluation_json"):
            try:
                eval_data = json.loads(row["evaluation_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        block_a = eval_data.get("block_a", {})
        block_d = eval_data.get("block_d", {})

        # Determine status
        app_status = row.get("app_status") or "discovered"
        if eval_data:
            app_status = app_status if app_status != "discovered" else "evaluated"

        score_100 = row.get("overall_score") or 0
        score_5 = eval_data.get("score_5", round(score_100 / 20, 1) if score_100 else 0)

        # Get artifact paths for download links
        pdf_artifact = db.get_artifact(row["id"], "pdf")
        report_artifact = db.get_artifact(row["id"], "report_md")
        pdf_path = pdf_artifact["file_path"] if pdf_artifact else None
        report_path = report_artifact["file_path"] if report_artifact else None

        # Determine if auto-apply is possible
        ats = row.get("ats_type", "unknown")
        can_auto_apply = ats in ("greenhouse", "lever", "workday")

        jobs_export.append({
            "id": row["id"],
            "company": row["company"],
            "title": row["title"],
            "score_100": score_100,
            "score_5": score_5,
            "archetype": eval_data.get("archetype", ""),
            "status": app_status,
            "recommendation": eval_data.get("recommendation", ""),
            "tl_dr": block_a.get("tl_dr", ""),
            "seniority": block_a.get("seniority", ""),
            "remote": block_a.get("remote", row.get("remote_policy", "")),
            "comp_estimate": block_d.get("estimated_comp", ""),
            "match_pct": eval_data.get("block_b", {}).get("match_percentage", None),
            "has_evaluation": bool(eval_data),
            "has_pdf": pdf_path is not None,
            "pdf_path": pdf_path,
            "has_report": report_path is not None,
            "report_path": report_path,
            "can_auto_apply": can_auto_apply,
            "applied_at": row.get("app_applied_at", ""),
            "discovered_at": row.get("discovered_at", ""),
            "posting_url": row.get("posting_url", ""),
            "location": row.get("location", ""),
            "ats_type": ats,
            "reasoning": row.get("reasoning", ""),
        })

    # Build story bank
    stories_export = []
    for s in stories:
        stories_export.append({
            "title": s["story_title"],
            "situation": s.get("situation", ""),
            "task": s.get("task", ""),
            "action": s.get("action", ""),
            "result": s.get("result", ""),
            "reflection": s.get("reflection", ""),
            "tags": json.loads(s.get("tags", "[]")),
        })

    # Build run history
    runs_export = []
    for r in runs:
        summary = {}
        if r.get("summary_json"):
            try:
                summary = json.loads(r["summary_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        runs_export.append({
            "date": r.get("started_at", "")[:10],
            "discovered": summary.get("new_jobs", 0),
            "scored": summary.get("scored", 0),
            "evaluated": summary.get("evaluated", 0),
            "applied": summary.get("applied", 0),
        })

    # Status counts
    status_counts = {}
    for j in jobs_export:
        s = j["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total_discovered": stats.get("total_jobs_discovered", 0),
            "total_scored": stats.get("jobs_scored", 0),
            "total_applied": stats.get("applications_submitted", 0),
            "avg_score": stats.get("average_match_score", 0),
            "total_evaluated": sum(1 for j in jobs_export if j["has_evaluation"]),
            "status_counts": status_counts,
        },
        "jobs": jobs_export[:500],  # Limit for JSON size
        "story_bank": stories_export,
        "pipeline_history": runs_export,
    }

    data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Dashboard data exported: {data_path} ({len(jobs_export)} jobs)")
    return str(data_path)


def export_markdown(db: Database) -> None:
    """Export to markdown files for git readability."""
    _export_applications_md(db)
    _export_story_bank_md(db)


def _export_applications_md(db: Database) -> None:
    """Generate data/applications.md from database."""
    all_jobs = db.get_all_jobs_with_scores()
    path = PROJECT_ROOT / "data" / "applications.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Applications Tracker",
        "",
        "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |",
        "|---|------|---------|------|-------|--------|-----|--------|-------|",
    ]

    for i, row in enumerate(all_jobs, 1):
        score_100 = row.get("overall_score") or 0
        eval_data = {}
        if row.get("evaluation_json"):
            try:
                eval_data = json.loads(row["evaluation_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        score_5 = eval_data.get("score_5", round(score_100 / 20, 1) if score_100 else 0)
        status = row.get("app_status") or ("evaluated" if eval_data else "discovered")

        # Map to canonical display names
        status_display = {
            "applied": "Applied", "evaluated": "Evaluated", "pending": "Evaluated",
            "ready_to_apply": "Evaluated", "manual_needed": "Evaluated",
            "failed": "Evaluated", "responded": "Responded", "interview": "Interview",
            "offer": "Offer", "rejected": "Rejected", "discarded": "Discarded",
            "skipped": "SKIP", "discovered": "Discovered",
        }.get(status, status.title())

        has_pdf = "Y" if db.get_artifact(row["id"], "pdf") else "-"
        report = db.get_artifact(row["id"], "report_md")
        report_link = f"[{i:03d}]({report['file_path']})" if report else "-"

        date = (row.get("discovered_at") or "")[:10]
        reasoning = (row.get("reasoning") or "")[:60]

        lines.append(
            f"| {i} | {date} | {row['company']} | {row['title']} "
            f"| {score_5}/5 | {status_display} | {has_pdf} | {report_link} | {reasoning} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Exported {len(all_jobs)} entries to applications.md")


def _export_story_bank_md(db: Database) -> None:
    """Generate interview-prep/story-bank.md from database."""
    stories = db.get_all_stories()
    path = PROJECT_ROOT / "interview-prep" / "story-bank.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Interview Story Bank -- Fauzan Mohammed",
        "",
        "Stories accumulated from offer evaluations. STAR+R format.",
        "",
        "---",
        "",
    ]

    for s in stories:
        tags = json.loads(s.get("tags", "[]"))
        tags_str = ", ".join(tags) if tags else ""
        lines.extend([
            f"## {s['story_title']}",
            "",
            f"**Tags:** {tags_str}" if tags_str else "",
            "",
            f"**Situation:** {s.get('situation', '')}",
            "",
            f"**Task:** {s.get('task', '')}",
            "",
            f"**Action:** {s.get('action', '')}",
            "",
            f"**Result:** {s.get('result', '')}",
            "",
            f"**Reflection:** {s.get('reflection', '')}",
            "",
            "---",
            "",
        ])

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Exported {len(stories)} stories to story-bank.md")
