from __future__ import annotations

from abc import ABC, abstractmethod

from src.tracking.models import RawJob


class JobSource(ABC):
    source_name: str

    @abstractmethod
    async def fetch_jobs(
        self,
        queries: list[str],
        location: str = "United States",
        posted_within_days: int = 7,
    ) -> list[RawJob]:
        """Fetch jobs from this source matching the given queries."""
        ...
