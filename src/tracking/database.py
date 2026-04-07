from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tracking.models import (
    ApplicationRecord,
    ApplicationStatus,
    Artifact,
    Job,
    MatchScore,
    PipelineRun,
    StoryBankEntry,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    external_id     TEXT,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    remote_policy   TEXT,
    posting_url     TEXT NOT NULL,
    apply_url       TEXT,
    description_raw TEXT,
    ats_type        TEXT DEFAULT 'unknown',
    seniority       TEXT,
    salary_range    TEXT,
    posted_date     TEXT,
    discovered_at   TEXT NOT NULL,
    is_duplicate    INTEGER DEFAULT 0,
    duplicate_of    TEXT,
    UNIQUE(company, title, posting_url)
);

CREATE TABLE IF NOT EXISTS match_scores (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    overall_score   INTEGER NOT NULL,
    skill_score     INTEGER,
    experience_score INTEGER,
    seniority_score INTEGER,
    reasoning       TEXT,
    scored_at       TEXT NOT NULL,
    model_used      TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    status          TEXT NOT NULL DEFAULT 'pending',
    method          TEXT,
    cover_letter    TEXT,
    screenshot_path TEXT,
    applied_at      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    name            TEXT PRIMARY KEY,
    normalized_name TEXT,
    ats_type        TEXT,
    greenhouse_token TEXT,
    lever_slug      TEXT,
    career_url      TEXT,
    is_whitelisted  INTEGER DEFAULT 0,
    is_blacklisted  INTEGER DEFAULT 0,
    employee_count  INTEGER,
    classification  TEXT,
    classified_at   TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    type        TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    UNIQUE(job_id, type)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    type            TEXT NOT NULL,
    file_path       TEXT,
    content         TEXT,
    created_at      TEXT NOT NULL,
    metadata_json   TEXT
);

CREATE TABLE IF NOT EXISTS story_bank (
    id              TEXT PRIMARY KEY,
    story_title     TEXT NOT NULL,
    situation       TEXT,
    task            TEXT,
    action          TEXT,
    result          TEXT,
    reflection      TEXT,
    source_job_ids  TEXT,
    tags            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    trigger         TEXT NOT NULL,
    steps_json      TEXT,
    summary_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_match_scores_job ON match_scores(job_id);
CREATE INDEX IF NOT EXISTS idx_match_scores_overall ON match_scores(overall_score);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_notifications_job ON notifications(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type);
"""

MIGRATIONS = [
    "ALTER TABLE match_scores ADD COLUMN evaluation_json TEXT",
]


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._run_migrations()

    def close(self) -> None:
        if self._conn:
            # Checkpoint WAL so the DB is a single file for artifact upload
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn  # type: ignore[return-value]

    def _run_migrations(self) -> None:
        """Run schema migrations safely (skip if already applied)."""
        for sql in MIGRATIONS:
            try:
                self.conn.execute(sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # Column/table already exists

    # ── Jobs ──────────────────────────────────────────────────────────

    def insert_job(self, job: Job) -> bool:
        """Insert a job. Returns True if inserted, False if duplicate."""
        try:
            self.conn.execute(
                """INSERT INTO jobs
                   (id, source, external_id, title, company, location, remote_policy,
                    posting_url, apply_url, description_raw, ats_type, posted_date,
                    discovered_at, is_duplicate, duplicate_of)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id, job.source, job.external_id, job.title, job.company,
                    job.location, job.remote_policy, job.posting_url, job.apply_url,
                    job.description_raw, job.ats_type.value, job.posted_date,
                    job.discovered_at.isoformat(), int(job.is_duplicate), job.duplicate_of,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_unscored_jobs(self) -> list[Job]:
        rows = self.conn.execute(
            """SELECT j.* FROM jobs j
               LEFT JOIN match_scores ms ON j.id = ms.job_id
               WHERE ms.id IS NULL AND j.is_duplicate = 0
               ORDER BY j.discovered_at DESC"""
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_jobs_by_score(self, min_score: int = 0) -> list[tuple[Job, MatchScore]]:
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               WHERE ms.overall_score >= ?
               ORDER BY ms.overall_score DESC""",
            (min_score,),
        ).fetchall()
        results = []
        for r in rows:
            job = self._row_to_job(r)
            score = MatchScore(
                id=r["ms_id"], job_id=job.id, overall_score=r["overall_score"],
                skill_score=r["skill_score"] or 0,
                experience_score=r["experience_score"] or 0,
                seniority_score=r["seniority_score"] or 0,
                reasoning=r["reasoning"] or "",
                scored_at=datetime.fromisoformat(r["scored_at"]),
                model_used=r["model_used"] or "",
            )
            results.append((job, score))
        return results

    def get_auto_apply_candidates(self, min_score: int) -> list[tuple[Job, MatchScore]]:
        """Get jobs scored high enough, prioritizing LinkedIn + top companies."""
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               LEFT JOIN applications a ON j.id = a.job_id
               WHERE ms.overall_score >= ?
                 AND a.id IS NULL
               ORDER BY
                 CASE WHEN j.source = 'linkedin' THEN 0 ELSE 1 END,
                 ms.overall_score DESC""",
            (min_score,),
        ).fetchall()
        results = []
        for r in rows:
            job = self._row_to_job(r)
            score = MatchScore(
                id=r["ms_id"], job_id=job.id, overall_score=r["overall_score"],
                skill_score=r["skill_score"] or 0,
                experience_score=r["experience_score"] or 0,
                seniority_score=r["seniority_score"] or 0,
                reasoning=r["reasoning"] or "",
                scored_at=datetime.fromisoformat(r["scored_at"]),
                model_used=r["model_used"] or "",
            )
            results.append((job, score))
        return results

    def get_jobs_needing_notification(self, min_score: int) -> list[tuple[Job, MatchScore]]:
        """Get scored jobs that haven't been notified about yet, LinkedIn first."""
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               LEFT JOIN applications a ON j.id = a.job_id
               WHERE ms.overall_score >= ?
                 AND a.id IS NULL
               ORDER BY
                 CASE WHEN j.source = 'linkedin' THEN 0 ELSE 1 END,
                 ms.overall_score DESC""",
            (min_score,),
        ).fetchall()
        results = []
        for r in rows:
            job = self._row_to_job(r)
            score = MatchScore(
                id=r["ms_id"], job_id=job.id, overall_score=r["overall_score"],
                skill_score=r["skill_score"] or 0,
                experience_score=r["experience_score"] or 0,
                seniority_score=r["seniority_score"] or 0,
                reasoning=r["reasoning"] or "",
                scored_at=datetime.fromisoformat(r["scored_at"]),
                model_used=r["model_used"] or "",
            )
            results.append((job, score))
        return results

    def job_exists(self, company: str, title: str, posting_url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE company=? AND title=? AND posting_url=?",
            (company, title, posting_url),
        ).fetchone()
        return row is not None

    # ── Match Scores ─────────────────────────────────────────────────

    def insert_score(self, score: MatchScore) -> None:
        self.conn.execute(
            """INSERT INTO match_scores
               (id, job_id, overall_score, skill_score, experience_score,
                seniority_score, reasoning, scored_at, model_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score.id, score.job_id, score.overall_score, score.skill_score,
                score.experience_score, score.seniority_score, score.reasoning,
                score.scored_at.isoformat(), score.model_used,
            ),
        )
        self.conn.commit()

    # ── Applications ─────────────────────────────────────────────────

    def insert_application(self, app: ApplicationRecord) -> None:
        self.conn.execute(
            """INSERT INTO applications
               (id, job_id, status, method, cover_letter, screenshot_path,
                applied_at, error_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                app.id, app.job_id, app.status.value, app.method,
                app.cover_letter, app.screenshot_path,
                app.applied_at.isoformat() if app.applied_at else None,
                app.error_message, app.created_at.isoformat(),
                app.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def update_application_status(
        self, app_id: str, status: ApplicationStatus, error_message: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE applications SET status=?, error_message=?, updated_at=?
               WHERE id=?""",
            (status.value, error_message, now, app_id),
        )
        self.conn.commit()

    def get_today_application_count(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM applications WHERE applied_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_all_applications(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT a.*, j.title, j.company, j.posting_url,
                      ms.overall_score
               FROM applications a
               JOIN jobs j ON a.job_id = j.id
               LEFT JOIN match_scores ms ON j.id = ms.job_id
               ORDER BY a.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Notifications ─────────────────────────────────────────────────

    def mark_notified(self, job_id: str, notification_type: str) -> None:
        """Mark a job as notified to prevent duplicate emails."""
        try:
            self.conn.execute(
                """INSERT INTO notifications (id, job_id, type, sent_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    job_id,
                    notification_type,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already notified

    def is_notified(self, job_id: str, notification_type: str) -> bool:
        """Check if a job has already been notified about."""
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE job_id=? AND type=?",
            (job_id, notification_type),
        ).fetchone()
        return row is not None

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        total_jobs = self.conn.execute("SELECT COUNT(*) as c FROM jobs").fetchone()["c"]
        scored_jobs = self.conn.execute("SELECT COUNT(*) as c FROM match_scores").fetchone()["c"]
        applied = self.conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE status='applied'"
        ).fetchone()["c"]
        pending = self.conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE status='pending'"
        ).fetchone()["c"]
        avg_score = self.conn.execute(
            "SELECT AVG(overall_score) as avg FROM match_scores"
        ).fetchone()["avg"]

        return {
            "total_jobs_discovered": total_jobs,
            "jobs_scored": scored_jobs,
            "applications_submitted": applied,
            "applications_pending": pending,
            "average_match_score": round(avg_score, 1) if avg_score else 0,
        }

    # ── Evaluation ───────────────────────────────────────────────────

    def update_evaluation(self, job_id: str, evaluation_json: str) -> None:
        """Store A-F evaluation JSON for a scored job."""
        self.conn.execute(
            "UPDATE match_scores SET evaluation_json=? WHERE job_id=?",
            (evaluation_json, job_id),
        )
        self.conn.commit()

    def get_unevaluated_jobs(self, min_score: int) -> list[tuple[Job, MatchScore]]:
        """Get scored jobs above threshold that haven't been A-F evaluated yet."""
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used, ms.evaluation_json
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               WHERE ms.overall_score >= ?
                 AND (ms.evaluation_json IS NULL OR ms.evaluation_json = '')
               ORDER BY ms.overall_score DESC""",
            (min_score,),
        ).fetchall()
        results = []
        for r in rows:
            job = self._row_to_job(r)
            score = MatchScore(
                id=r["ms_id"], job_id=job.id, overall_score=r["overall_score"],
                skill_score=r["skill_score"] or 0,
                experience_score=r["experience_score"] or 0,
                seniority_score=r["seniority_score"] or 0,
                reasoning=r["reasoning"] or "",
                scored_at=datetime.fromisoformat(r["scored_at"]),
                model_used=r["model_used"] or "",
            )
            results.append((job, score))
        return results

    # ── Artifacts ────────────────────────────────────────────────────

    def insert_artifact(self, artifact: Artifact) -> None:
        self.conn.execute(
            """INSERT INTO artifacts (id, job_id, type, file_path, content, created_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artifact.id, artifact.job_id, artifact.type, artifact.file_path,
             artifact.content, artifact.created_at.isoformat(), artifact.metadata_json),
        )
        self.conn.commit()

    def get_artifact(self, job_id: str, artifact_type: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE job_id=? AND type=?",
            (job_id, artifact_type),
        ).fetchone()
        return dict(row) if row else None

    # ── Story Bank ──────────────────────────────────────────────────

    def insert_story(self, story: StoryBankEntry) -> None:
        self.conn.execute(
            """INSERT INTO story_bank
               (id, story_title, situation, task, action, result, reflection,
                source_job_ids, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (story.id, story.story_title, story.situation, story.task,
             story.action, story.result, story.reflection,
             story.source_job_ids, story.tags,
             story.created_at.isoformat(), story.updated_at.isoformat()),
        )
        self.conn.commit()

    def get_all_stories(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM story_bank ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Pipeline Runs ───────────────────────────────────────────────

    def insert_pipeline_run(self, run: PipelineRun) -> None:
        self.conn.execute(
            """INSERT INTO pipeline_runs (id, started_at, completed_at, trigger, steps_json, summary_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run.id, run.started_at.isoformat(),
             run.completed_at.isoformat() if run.completed_at else None,
             run.trigger, run.steps_json, run.summary_json),
        )
        self.conn.commit()

    def update_pipeline_run(self, run_id: str, completed_at: datetime,
                            steps_json: str, summary_json: str) -> None:
        self.conn.execute(
            """UPDATE pipeline_runs SET completed_at=?, steps_json=?, summary_json=?
               WHERE id=?""",
            (completed_at.isoformat(), steps_json, summary_json, run_id),
        )
        self.conn.commit()

    def get_pipeline_runs(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Export (for dashboard) ──────────────────────────────────────

    def get_all_jobs_with_scores(self) -> list[dict]:
        """Get all jobs with their scores and evaluation data for export."""
        rows = self.conn.execute(
            """SELECT j.*, ms.overall_score, ms.skill_score, ms.experience_score,
                      ms.seniority_score, ms.reasoning, ms.evaluation_json,
                      a.status as app_status, a.method as app_method,
                      a.applied_at as app_applied_at
               FROM jobs j
               LEFT JOIN match_scores ms ON j.id = ms.job_id
               LEFT JOIN applications a ON j.id = a.job_id
               ORDER BY COALESCE(ms.overall_score, 0) DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            remote_policy=row["remote_policy"],
            posting_url=row["posting_url"],
            apply_url=row["apply_url"],
            description_raw=row["description_raw"],
            ats_type=row["ats_type"] or "unknown",
            posted_date=row["posted_date"],
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
            is_duplicate=bool(row["is_duplicate"]),
            duplicate_of=row["duplicate_of"],
        )
