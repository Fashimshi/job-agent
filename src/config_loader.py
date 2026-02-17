from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class ConfigurationError(Exception):
    """Raised when configuration is invalid."""
    pass


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


class ApplicantConfig(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    linkedin_url: str
    github_url: str | None = None
    portfolio_url: str | None = None
    location: str
    current_company: str = ""
    work_authorized: bool = True
    sponsorship_needed: bool = False
    sponsorship_details: str = ""

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, v):
            raise ValueError(f"Invalid email format: {v}")
        return v

    @field_validator("linkedin_url")
    @classmethod
    def validate_linkedin_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"LinkedIn URL must start with http:// or https://: {v}")
        if v and "linkedin.com" not in v.lower():
            raise ValueError(f"Invalid LinkedIn URL: {v}")
        return v

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"GitHub URL must start with http:// or https://: {v}")
        return v

    @field_validator("portfolio_url")
    @classmethod
    def validate_portfolio_url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"Portfolio URL must start with http:// or https://: {v}")
        return v


class DiscoveryConfig(BaseModel):
    queries: list[str]
    location: str = "United States"
    posted_within_days: int = 7
    remote_ok: bool = True


class MatchingConfig(BaseModel):
    primary_provider: str = "openai"
    fallback_provider: str = "anthropic"
    scoring_model: str = "gpt-4o-mini"
    scoring_model_fallback: str = "claude-sonnet-4-5-20250929"
    cover_letter_model: str = "gpt-4o"
    cover_letter_model_fallback: str = "claude-sonnet-4-5-20250929"
    min_score_auto_apply: int = 85
    min_score_notify: int = 70
    min_score_log: int = 50


class ApplicationConfig(BaseModel):
    dry_run: bool = True
    max_per_day: int = 10
    screenshot_dir: str = "./data/screenshots"


class SeniorityConfig(BaseModel):
    include: list[str] = Field(default_factory=lambda: ["senior", "staff", "lead"])
    exclude: list[str] = Field(
        default_factory=lambda: [
            "intern", "junior", "associate", "director",
            "vp", "vice president", "principal", "manager", "head of",
        ]
    )


class NotificationsConfig(BaseModel):
    channels: list[str] = Field(default_factory=lambda: ["console"])
    digest_enabled: bool = True


class PathsConfig(BaseModel):
    resume_pdf: str = "./config/resume.pdf"
    resume_text: str = "./config/resume_text.txt"
    database: str = "./data/jobs.db"


class Settings(BaseModel):
    applicant: ApplicantConfig
    discovery: DiscoveryConfig
    matching: MatchingConfig
    application: ApplicationConfig = ApplicationConfig()
    seniority: SeniorityConfig = SeniorityConfig()
    role_keywords: list[str] = Field(default_factory=list)
    notifications: NotificationsConfig = NotificationsConfig()
    paths: PathsConfig = PathsConfig()

    # Environment secrets (loaded from .env)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    serpapi_key: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notification_email: str = ""

    def resolve_path(self, relative: str) -> Path:
        return (_project_root() / relative).resolve()

    @property
    def resume_pdf_path(self) -> Path:
        return self.resolve_path(self.paths.resume_pdf)

    @property
    def resume_text_path(self) -> Path:
        return self.resolve_path(self.paths.resume_text)

    @property
    def database_path(self) -> Path:
        return self.resolve_path(self.paths.database)

    @property
    def screenshot_dir_path(self) -> Path:
        return self.resolve_path(self.application.screenshot_dir)

    def validate(self) -> list[str]:
        """
        Validate configuration and return list of errors.
        Returns empty list if configuration is valid.
        """
        errors: list[str] = []

        # Check that at least one LLM API key is configured
        if not self.openai_api_key and not self.anthropic_api_key:
            errors.append(
                "No LLM API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
            )

        # Validate primary provider has matching API key
        if self.matching.primary_provider == "openai" and not self.openai_api_key:
            errors.append(
                "Primary provider is 'openai' but OPENAI_API_KEY is not set"
            )
        if self.matching.primary_provider == "anthropic" and not self.anthropic_api_key:
            errors.append(
                "Primary provider is 'anthropic' but ANTHROPIC_API_KEY is not set"
            )

        # Check resume files exist
        if not self.resume_pdf_path.exists():
            errors.append(f"Resume PDF not found: {self.resume_pdf_path}")

        if not self.resume_text_path.exists():
            errors.append(
                f"Resume text not found: {self.resume_text_path}. "
                "Run 'job-agent refresh-resume' to extract text from PDF."
            )

        # Check role_keywords is not empty
        if not self.role_keywords:
            errors.append(
                "No role_keywords configured in settings.yaml. "
                "Add keywords like 'data scientist', 'machine learning' to filter jobs."
            )

        # Check discovery queries
        if not self.discovery.queries:
            errors.append("No discovery queries configured in settings.yaml")

        return errors

    def validate_or_raise(self) -> None:
        """Validate configuration and raise ConfigurationError if invalid."""
        errors = self.validate()
        if errors:
            error_msg = "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            raise ConfigurationError(error_msg)


def load_settings(
    config_dir: Path | None = None,
    env_file: Path | None = None,
) -> Settings:
    root = _project_root()
    config_dir = config_dir or root / "config"
    env_file = env_file or root / ".env"

    if env_file.exists():
        load_dotenv(env_file)

    yaml_data = load_yaml(config_dir / "settings.yaml")

    yaml_data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
    yaml_data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    yaml_data["serpapi_key"] = os.getenv("SERPAPI_KEY", "")
    yaml_data["smtp_host"] = os.getenv("SMTP_HOST", "smtp.gmail.com")
    yaml_data["smtp_port"] = int(os.getenv("SMTP_PORT", "587"))
    yaml_data["smtp_user"] = os.getenv("SMTP_USER", "")
    yaml_data["smtp_password"] = os.getenv("SMTP_PASSWORD", "")
    yaml_data["notification_email"] = os.getenv(
        "NOTIFICATION_EMAIL", yaml_data.get("applicant", {}).get("email", "")
    )

    settings = Settings(**yaml_data)

    # Validate configuration
    settings.validate_or_raise()

    return settings
