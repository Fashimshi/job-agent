from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.tracking.models import ApplicationResult, ApplicantInfo, Job


class BaseApplicant(ABC):
    """Base class for ATS-specific auto-apply automation."""

    def __init__(self, applicant_info: ApplicantInfo):
        self.info = applicant_info

    @abstractmethod
    async def apply(
        self,
        job: Job,
        cover_letter: str,
        resume_path: Path,
        screenshot_dir: Path,
        dry_run: bool = True,
    ) -> ApplicationResult:
        """Fill and optionally submit the application form."""
        ...
