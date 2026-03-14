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
TIER_COLORS = {1: "#22c55e", 2: "#4ade80", 3: "#06b6d4", 4: "#3b82f6", 5: "#9ca3af"}

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
            tier = get_company_tier(job.company)
            html = self._render_applied_email(job, score, tier, screenshot_path)
            self._send_email(
                f"Applied: {job.title} at {job.company} (Score: {score.overall_score})",
                msg,
                html,
            )

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
            tier = get_company_tier(job.company)
            html = self._render_manual_email(job, score, tier, cover_letter)
            self._send_email(
                f"Apply Now: {job.title} at {job.company} (Score: {score.overall_score})",
                msg,
                html,
            )

    def notify_digest(
        self,
        stats: dict,
        new_jobs: int,
        applied: int,
        manual: int,
        qualified_jobs: list[tuple[Job, MatchScore]] | None = None,
        applied_jobs: list[tuple[Job, MatchScore]] | None = None,
    ) -> None:
        qualified_jobs = qualified_jobs or []
        applied_jobs = applied_jobs or []

        msg = (
            f"Job Agent Daily Digest\n"
            f"{'=' * 40}\n"
            f"New jobs discovered: {new_jobs}\n"
            f"Auto-applied: {applied}\n"
            f"Manual applications needed: {manual}\n"
            f"Qualified jobs found: {len(qualified_jobs)}\n"
            f"\nAll-time stats:\n"
            f"  Total jobs tracked: {stats.get('total_jobs_discovered', 0)}\n"
            f"  Jobs scored: {stats.get('jobs_scored', 0)}\n"
            f"  Applications submitted: {stats.get('applications_submitted', 0)}\n"
            f"  Average match score: {stats.get('average_match_score', 0)}\n"
        )

        if qualified_jobs:
            msg += "\n--- Top Qualified Jobs ---\n"
            for job, score in qualified_jobs[:15]:
                msg += f"  [{score.overall_score}] {job.title} at {job.company} — {job.posting_url}\n"

        if applied_jobs:
            msg += "\n--- Applied Today ---\n"
            for job, score in applied_jobs:
                msg += f"  [{score.overall_score}] {job.title} at {job.company}\n"

        if "console" in self.channels:
            console.print(Panel(msg, title="[bold blue]Daily Digest[/bold blue]", border_style="blue"))
        if "email" in self.channels:
            html = self._render_digest_email(
                stats, new_jobs, applied, manual, qualified_jobs, applied_jobs
            )
            self._send_email("Job Agent Daily Digest", msg, html)

    def print_job_table(self, jobs_with_scores: list[tuple[Job, MatchScore]]) -> None:
        """Print a rich table of scored jobs, sorted by tier then score."""
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

    # ── HTML Email Templates ──────────────────────────────────────────

    def _render_digest_email(
        self,
        stats: dict,
        new_jobs: int,
        applied: int,
        manual: int,
        qualified_jobs: list[tuple[Job, MatchScore]],
        applied_jobs: list[tuple[Job, MatchScore]],
    ) -> str:
        # Build qualified jobs rows
        qualified_rows = ""
        for job, score in qualified_jobs[:20]:
            tier = get_company_tier(job.company)
            tier_label = TIER_LABELS.get(tier, "Other")
            tier_color = TIER_COLORS.get(tier, "#9ca3af")
            score_color = "#22c55e" if score.overall_score >= 85 else "#eab308" if score.overall_score >= 70 else "#9ca3af"
            apply_url = job.posting_url or job.apply_url or "#"
            qualified_rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px 8px; font-size: 20px; font-weight: bold; color: {score_color};">{score.overall_score}</td>
                <td style="padding: 12px 8px;">
                    <div style="font-weight: 600; color: #111827;">{job.title}</div>
                    <div style="color: #6b7280; font-size: 13px;">{job.company} &middot; <span style="color: {tier_color};">{tier_label}</span></div>
                    <div style="color: #9ca3af; font-size: 12px;">{job.location or 'Remote/Unknown'}</div>
                </td>
                <td style="padding: 12px 8px; text-align: center;">
                    <a href="{apply_url}" style="display: inline-block; padding: 6px 16px; background: #2563eb; color: white; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 500;">Apply</a>
                </td>
            </tr>"""

        # Build applied jobs rows
        applied_rows = ""
        for job, score in applied_jobs:
            applied_rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 10px 8px; font-weight: bold; color: #22c55e;">{score.overall_score}</td>
                <td style="padding: 10px 8px;">
                    <div style="font-weight: 600;">{job.title}</div>
                    <div style="color: #6b7280; font-size: 13px;">{job.company}</div>
                </td>
                <td style="padding: 10px 8px; color: #22c55e; font-weight: 500;">Applied</td>
            </tr>"""

        avg_score = stats.get('average_match_score', 0)
        total_scored = stats.get('jobs_scored', 0)
        total_applied = stats.get('applications_submitted', 0)

        qualified_section = ""
        if qualified_rows:
            qualified_section = f"""
            <div style="margin-top: 24px;">
                <h2 style="color: #111827; font-size: 18px; margin-bottom: 12px;">Jobs You Qualify For</h2>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 2px solid #e5e7eb;">
                            <th style="padding: 8px; text-align: left; color: #6b7280; font-size: 12px; width: 50px;">SCORE</th>
                            <th style="padding: 8px; text-align: left; color: #6b7280; font-size: 12px;">JOB</th>
                            <th style="padding: 8px; text-align: center; color: #6b7280; font-size: 12px; width: 80px;">ACTION</th>
                        </tr>
                    </thead>
                    <tbody>{qualified_rows}</tbody>
                </table>
            </div>"""

        applied_section = ""
        if applied_rows:
            applied_section = f"""
            <div style="margin-top: 24px;">
                <h2 style="color: #111827; font-size: 18px; margin-bottom: 12px;">Applied Today</h2>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 2px solid #e5e7eb;">
                            <th style="padding: 8px; text-align: left; color: #6b7280; font-size: 12px; width: 50px;">SCORE</th>
                            <th style="padding: 8px; text-align: left; color: #6b7280; font-size: 12px;">JOB</th>
                            <th style="padding: 8px; text-align: left; color: #6b7280; font-size: 12px; width: 80px;">STATUS</th>
                        </tr>
                    </thead>
                    <tbody>{applied_rows}</tbody>
                </table>
            </div>"""

        no_jobs_msg = ""
        if not qualified_rows and not applied_rows:
            no_jobs_msg = """
            <div style="margin-top: 24px; padding: 20px; background: #fef3c7; border-radius: 8px; color: #92400e;">
                <strong>No qualifying jobs found this run.</strong>
                <p style="margin: 8px 0 0 0; font-size: 14px;">This could mean scoring failed (check API keys) or no new jobs matched your criteria.</p>
            </div>"""

        return f"""
        <div style="max-width: 640px; margin: 0 auto; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #374151;">
            <div style="background: linear-gradient(135deg, #1e3a5f, #2563eb); padding: 24px; border-radius: 12px 12px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 22px;">Job Agent Update</h1>
                <p style="color: #93c5fd; margin: 4px 0 0 0; font-size: 14px;">Your daily job search summary</p>
            </div>

            <div style="background: white; padding: 24px; border: 1px solid #e5e7eb; border-top: none;">
                <div style="display: flex; gap: 12px; margin-bottom: 8px;">
                    <div style="flex: 1; background: #f0fdf4; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #16a34a;">{new_jobs}</div>
                        <div style="font-size: 12px; color: #6b7280;">New Jobs</div>
                    </div>
                    <div style="flex: 1; background: #eff6ff; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #2563eb;">{len(qualified_jobs)}</div>
                        <div style="font-size: 12px; color: #6b7280;">Qualified</div>
                    </div>
                    <div style="flex: 1; background: #faf5ff; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 28px; font-weight: bold; color: #7c3aed;">{applied}</div>
                        <div style="font-size: 12px; color: #6b7280;">Applied</div>
                    </div>
                </div>

                <div style="margin-top: 8px; padding: 12px; background: #f9fafb; border-radius: 8px; font-size: 13px; color: #6b7280;">
                    All-time: {total_scored} scored &middot; {total_applied} applied &middot; Avg score: {avg_score}
                </div>

                {qualified_section}
                {applied_section}
                {no_jobs_msg}
            </div>

            <div style="background: #f9fafb; padding: 16px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; text-align: center; font-size: 12px; color: #9ca3af;">
                Job Agent &middot; Automated job search for {self.settings.applicant.first_name} {self.settings.applicant.last_name}
            </div>
        </div>"""

    def _render_applied_email(
        self, job: Job, score: MatchScore, tier: int, screenshot_path: str | None
    ) -> str:
        tier_label = TIER_LABELS.get(tier, "Other")
        return f"""
        <div style="max-width: 640px; margin: 0 auto; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="background: #16a34a; padding: 20px; border-radius: 12px 12px 0 0; text-align: center;">
                <div style="font-size: 32px;">&#10004;</div>
                <h1 style="color: white; margin: 8px 0 0 0; font-size: 20px;">Application Submitted</h1>
            </div>
            <div style="background: white; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
                <h2 style="margin: 0; color: #111827;">{job.title}</h2>
                <p style="color: #6b7280; margin: 4px 0 16px 0;">{job.company} &middot; {tier_label} &middot; {job.location or 'Remote'}</p>
                <div style="background: #f0fdf4; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                    <div style="font-size: 14px; color: #6b7280;">Match Score</div>
                    <div style="font-size: 36px; font-weight: bold; color: #16a34a;">{score.overall_score}/100</div>
                    <div style="font-size: 13px; color: #374151; margin-top: 8px;">{score.reasoning}</div>
                </div>
                <div style="font-size: 13px; color: #9ca3af;">
                    Skills: {score.skill_score} &middot; Experience: {score.experience_score} &middot; Seniority: {score.seniority_score}
                </div>
            </div>
        </div>"""

    def _render_manual_email(
        self, job: Job, score: MatchScore, tier: int, cover_letter: str
    ) -> str:
        tier_label = TIER_LABELS.get(tier, "Other")
        apply_url = job.posting_url or job.apply_url or "#"
        # Escape HTML in cover letter
        cover_letter_html = cover_letter.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        return f"""
        <div style="max-width: 640px; margin: 0 auto; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="background: #eab308; padding: 20px; border-radius: 12px 12px 0 0; text-align: center;">
                <div style="font-size: 32px;">&#9733;</div>
                <h1 style="color: white; margin: 8px 0 0 0; font-size: 20px;">High Match — Apply Now</h1>
            </div>
            <div style="background: white; padding: 24px; border: 1px solid #e5e7eb; border-top: none;">
                <h2 style="margin: 0; color: #111827;">{job.title}</h2>
                <p style="color: #6b7280; margin: 4px 0 16px 0;">{job.company} &middot; {tier_label} &middot; {job.location or 'Remote'}</p>
                <div style="background: #fefce8; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                    <div style="font-size: 14px; color: #6b7280;">Match Score</div>
                    <div style="font-size: 36px; font-weight: bold; color: #ca8a04;">{score.overall_score}/100</div>
                    <div style="font-size: 13px; color: #374151; margin-top: 8px;">{score.reasoning}</div>
                </div>
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{apply_url}" style="display: inline-block; padding: 12px 32px; background: #2563eb; color: white; border-radius: 8px; text-decoration: none; font-size: 16px; font-weight: 600;">Apply Now</a>
                </div>
                <details style="margin-top: 20px;">
                    <summary style="cursor: pointer; font-weight: 600; color: #374151;">Generated Cover Letter</summary>
                    <div style="margin-top: 12px; padding: 16px; background: #f9fafb; border-radius: 8px; font-size: 14px; line-height: 1.6; color: #374151; white-space: pre-wrap;">{cover_letter_html}</div>
                </details>
            </div>
            <div style="background: #f9fafb; padding: 12px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none; text-align: center; font-size: 12px; color: #9ca3af;">
                Skills: {score.skill_score} &middot; Experience: {score.experience_score} &middot; Seniority: {score.seniority_score}
            </div>
        </div>"""

    def _send_email(self, subject: str, body: str, html: str | None = None) -> None:
        if not self.settings.smtp_user or not self.settings.smtp_password:
            logger.warning("Email not configured (missing SMTP credentials), skipping email notification")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.settings.smtp_user
            msg["To"] = self.settings.notification_email
            msg["Subject"] = f"[Job Agent] {subject}"

            # Always attach plain text
            msg.attach(MIMEText(body, "plain"))

            # Attach HTML if provided
            if html:
                msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
                server.starttls()
                server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)

            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
