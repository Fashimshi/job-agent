from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tracking.models import (
    ApplicationRecord,
    ApplicationStatus,
    Job,
    MatchScore,
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

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_match_scores_job ON match_scores(job_id);
CREATE INDEX IF NOT EXISTS idx_match_scores_overall ON match_scores(overall_score);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);
"""


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

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn  # type: ignore[return-value]

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
        """Get jobs that are scored high enough and have auto-apply ATS."""
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               LEFT JOIN applications a ON j.id = a.job_id
               WHERE ms.overall_score >= ?
                 AND j.ats_type IN ('greenhouse', 'lever', 'workday')
                 AND a.id IS NULL
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

    def get_jobs_needing_notification(self, min_score: int) -> list[tuple[Job, MatchScore]]:
        """Get scored jobs that aren't auto-apply and haven't been notified about."""
        rows = self.conn.execute(
            """SELECT j.*, ms.id as ms_id, ms.overall_score, ms.skill_score,
                      ms.experience_score, ms.seniority_score, ms.reasoning,
                      ms.scored_at, ms.model_used
               FROM jobs j
               JOIN match_scores ms ON j.id = ms.job_id
               LEFT JOIN applications a ON j.id = a.job_id
               WHERE ms.overall_score >= ?
                 AND j.ats_type NOT IN ('greenhouse', 'lever', 'workday')
                 AND (a.id IS NULL OR a.status = 'manual_needed')
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
