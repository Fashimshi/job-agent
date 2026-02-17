from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.matching.filters import get_company_tier
from src.tracking.models import Job, MatchScore

TIER_LABELS = {1: "FAANG", 2: "Big Tech", 3: "Mid Tech", 4: "Finance", 5: "Other"}

if TYPE_CHECKING:
    from src.config_loader import Settings

logger = logging.getLogger(__name__)
console = Console()


class Notifier:
    """Send notifications via console and/or email."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.channels = settings.notifications.channels

    def notify_auto_applied(self, job: Job, score: MatchScore, screenshot_path: str | None) -> None:
        msg = (
            f"AUTO-APPLIED: {job.title} at {job.company}\n"
            f"Score: {score.overall_score}/100 | {score.reasoning}\n"
            f"Screenshot: {screenshot_path or 'N/A'}"
        )
        if "console" in self.channels:
            console.print(Panel(msg, title="[bold green]Application Submitted[/bold green]", border_style="green"))
        if "email" in self.channels:
            self._send_email(f"Applied: {job.title} at {job.company}", msg)

    def notify_manual_needed(self, job: Job, score: MatchScore, cover_letter: str) -> None:
        msg = (
            f"HIGH MATCH: {job.title} at {job.company}\n"
            f"Score: {score.overall_score}/100 | {score.reasoning}\n"
            f"Apply URL: {job.posting_url}\n"
            f"\n--- Generated Cover Letter ---\n{cover_letter}\n"
        )
        if "console" in self.channels:
            console.print(Panel(msg, title="[bold yellow]Manual Application Needed[/bold yellow]", border_style="yellow"))
        if "email" in self.channels:
            self._send_email(f"Apply Now: {job.title} at {job.company}", msg)

    def notify_digest(self, stats: dict, new_jobs: int, applied: int, manual: int) -> None:
        msg = (
            f"Job Agent Daily Digest\n"
            f"{'=' * 40}\n"
            f"New jobs discovered: {new_jobs}\n"
            f"Auto-applied: {applied}\n"
            f"Manual applications needed: {manual}\n"
            f"\nAll-time stats:\n"
            f"  Total jobs tracked: {stats.get('total_jobs_discovered', 0)}\n"
            f"  Jobs scored: {stats.get('jobs_scored', 0)}\n"
            f"  Applications submitted: {stats.get('applications_submitted', 0)}\n"
            f"  Average match score: {stats.get('average_match_score', 0)}\n"
        )
        if "console" in self.channels:
            console.print(Panel(msg, title="[bold blue]Daily Digest[/bold blue]", border_style="blue"))
        if "email" in self.channels:
            self._send_email("Job Agent Daily Digest", msg)

    def print_job_table(self, jobs_with_scores: list[tuple[Job, MatchScore]]) -> None:
        """Print a rich table of scored jobs, sorted by tier then score."""
        # Sort: tier first (FAANG=1 first), then score descending
        sorted_jobs = sorted(
            jobs_with_scores,
            key=lambda x: (get_company_tier(x[0].company), -x[1].overall_score),
        )

        table = Table(title="Job Matches")
        table.add_column("Score", style="bold", width=6)
        table.add_column("Tier", width=10)
        table.add_column("Company", width=20)
        table.add_column("Title", width=35)
        table.add_column("ATS", width=12)
        table.add_column("Location", width=20)

        for job, score in sorted_jobs:
            tier = get_company_tier(job.company)
            tier_label = TIER_LABELS.get(tier, "Other")
            tier_style = {1: "bold green", 2: "green", 3: "cyan", 4: "blue", 5: "dim"}.get(tier, "dim")
            score_style = "green" if score.overall_score >= 85 else "yellow" if score.overall_score >= 70 else "dim"
            table.add_row(
                f"[{score_style}]{score.overall_score}[/{score_style}]",
                f"[{tier_style}]{tier_label}[/{tier_style}]",
                job.company,
                job.title,
                job.ats_type.value,
                job.location or "N/A",
            )

        console.print(table)

    def _send_email(self, subject: str, body: str) -> None:
        if not self.settings.smtp_user or not self.settings.smtp_password:
            logger.warning("Email not configured (missing SMTP credentials), skipping email notification")
            return

        try:
            msg = MIMEMultipart()
            msg["From"] = self.settings.smtp_user
            msg["To"] = self.settings.notification_email
            msg["Subject"] = f"[Job Agent] {subject}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
                server.starttls()
                server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)

            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
