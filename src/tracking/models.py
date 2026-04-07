from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class ATSType(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class ApplicationStatus(str, Enum):
    PENDING = "pending"
    READY_TO_APPLY = "ready_to_apply"
    EVALUATED = "evaluated"
    APPLIED = "applied"
    FAILED = "failed"
    MANUAL_NEEDED = "manual_needed"
    SKIPPED = "skipped"
    RESPONDED = "responded"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    DISCARDED = "discarded"


class RawJob(BaseModel):
    source: str
    external_id: str | None = None
    title: str
    company: str
    location: str | None = None
    remote_policy: str | None = None
    posting_url: str
    apply_url: str | None = None
    description_raw: str | None = None
    ats_type: ATSType = ATSType.UNKNOWN
    posted_date: str | None = None


class Job(RawJob):
    id: str = Field(default_factory=_new_id)
    discovered_at: datetime = Field(default_factory=_utcnow)
    is_duplicate: bool = False
    duplicate_of: str | None = None


class ParsedJob(BaseModel):
    title: str
    company: str
    seniority: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    years_experience_min: int | None = None
    years_experience_max: int | None = None
    education_requirement: str = ""
    location: str = ""
    remote_policy: str = ""
    salary_range: str | None = None
    responsibilities: list[str] = Field(default_factory=list)


class MatchScore(BaseModel):
    id: str = Field(default_factory=_new_id)
    job_id: str
    overall_score: int = Field(ge=0, le=100)
    skill_score: int = Field(ge=0, le=100)
    experience_score: int = Field(ge=0, le=100)
    seniority_score: int = Field(ge=0, le=100)
    reasoning: str = ""
    scored_at: datetime = Field(default_factory=_utcnow)
    model_used: str = ""


class ApplicationRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    job_id: str
    status: ApplicationStatus = ApplicationStatus.PENDING
    method: str | None = None
    cover_letter: str | None = None
    screenshot_path: str | None = None
    applied_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ApplicantInfo(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    linkedin_url: str
    github_url: str | None = None
    portfolio_url: str | None = None
    location: str = ""
    current_company: str = ""
    work_authorized: bool = True
    sponsorship_needed: bool = False
    sponsorship_details: str = ""


class ApplicationResult(BaseModel):
    success: bool
    job_id: str
    screenshot_path: str | None = None
    error_message: str | None = None
    submitted_at: datetime | None = None


class Artifact(BaseModel):
    id: str = Field(default_factory=_new_id)
    job_id: str
    type: str  # 'pdf', 'report_md', 'cover_letter', 'html_cv'
    file_path: str | None = None
    content: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    metadata_json: str | None = None


class StoryBankEntry(BaseModel):
    id: str = Field(default_factory=_new_id)
    story_title: str
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    reflection: str = ""
    source_job_ids: str = "[]"  # JSON array
    tags: str = "[]"  # JSON array
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PipelineRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    trigger: str = "manual"  # 'scheduled', 'manual', 'workflow_dispatch'
    steps_json: str = "{}"
    summary_json: str = "{}"
