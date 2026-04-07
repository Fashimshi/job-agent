from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_settings, ConfigurationError
from src.pipeline import Pipeline

app = typer.Typer(
    name="job-agent",
    help="AI-powered hybrid job application agent",
    no_args_is_help=True,
)
console = Console()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@app.command()
def run(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Fill forms but don't submit"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (for CI/automation)"),
    query: str = typer.Option(None, "--query", "-q", help="Override search query (use instead of config queries)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """Full pipeline: discover -> score -> filter -> apply/notify."""
    setup_logging(verbose)

    try:
        settings = load_settings()
    except ConfigurationError as e:
        console.print(f"[red]Configuration Error:[/red]\n{e}")
        raise typer.Exit(1)

    # Override queries if --query provided
    if query:
        settings.discovery.queries = [query]

    console.print(Panel(
        f"[bold]Job Agent Pipeline[/bold]\n"
        f"Mode: {'DRY RUN' if dry_run else '[red]LIVE - WILL SUBMIT APPLICATIONS[/red]'}\n"
        f"Target: {', '.join(settings.discovery.queries[:3])}...\n"
        f"Location: {settings.discovery.location}",
        border_style="blue",
    ))

    if not dry_run and not yes:
        confirm = typer.confirm(
            "LIVE MODE: Applications will be submitted. Are you sure?",
            default=False,
        )
        if not confirm:
            raise typer.Abort()

    pipeline = Pipeline(settings)
    try:
        result = asyncio.run(pipeline.run(dry_run=dry_run))
        console.print(f"\n[bold green]Done![/bold green] "
                      f"New: {result['new_jobs']} | Scored: {result['scored']} | "
                      f"Evaluated: {result['evaluated']} | PDFs: {result['pdfs']} | "
                      f"Applied: {result['applied']} | Manual: {result['manual_needed']}")
    finally:
        pipeline.close()


@app.command()
def discover(
    query: str = typer.Option(None, "--query", "-q", help="Override search query"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run job discovery only (no scoring or applying)."""
    setup_logging(verbose)

    try:
        settings = load_settings()
    except ConfigurationError as e:
        console.print(f"[red]Configuration Error:[/red]\n{e}")
        raise typer.Exit(1)

    # Override queries if --query provided
    if query:
        settings.discovery.queries = [query]

    pipeline = Pipeline(settings)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Discovering jobs...", total=None)
            new_jobs = asyncio.run(pipeline.discover())
            progress.update(task, completed=100, total=100)

        console.print(f"\n[bold green]Discovered {len(new_jobs)} new jobs[/bold green]")
        for job in new_jobs[:10]:
            console.print(f"  [{job.ats_type.value}] {job.title} @ {job.company}")
        if len(new_jobs) > 10:
            console.print(f"  ... and {len(new_jobs) - 10} more")
    finally:
        pipeline.close()


@app.command()
def score(
    min_score: int = typer.Option(0, "--min-score", help="Only show jobs above this score"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Score all unscored jobs using LLM matching."""
    setup_logging(verbose)
    settings = load_settings()

    pipeline = Pipeline(settings)
    try:
        unscored = pipeline.db.get_unscored_jobs()
        if not unscored:
            console.print("[yellow]No unscored jobs found. Run 'discover' first.[/yellow]")
            return

        filtered = pipeline.filter.apply_all(unscored)
        console.print(f"Scoring {len(filtered)} jobs (filtered from {len(unscored)})...")

        scored = asyncio.run(pipeline.score(filtered))

        # Display results
        results_to_show = [(j, s) for j, s in scored if s.overall_score >= min_score]
        if results_to_show:
            pipeline.notifier.print_job_table(results_to_show)
        else:
            console.print(f"[yellow]No jobs scored above {min_score}[/yellow]")
    finally:
        pipeline.close()


@app.command(name="apply")
def apply_cmd(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_applications: int = typer.Option(10, "--max", help="Max applications this run"),
    min_score: int = typer.Option(70, "--min-score"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Auto-apply to eligible Greenhouse/Lever jobs."""
    setup_logging(verbose)
    settings = load_settings()

    if not dry_run:
        confirm = typer.confirm(
            "LIVE MODE: Applications will actually be submitted. Continue?",
            default=False,
        )
        if not confirm:
            raise typer.Abort()

    pipeline = Pipeline(settings)
    try:
        candidates = pipeline.db.get_auto_apply_candidates(min_score)
        if not candidates:
            console.print("[yellow]No auto-apply candidates found. Run 'discover' and 'score' first.[/yellow]")
            return

        console.print(f"Found {len(candidates)} auto-apply candidates (min score: {min_score})")
        applied = 0

        for job, match_score in candidates[:max_applications]:
            if pipeline.registry.is_excluded_from_apply(job.company):
                continue
            success = asyncio.run(pipeline.apply_to_job(job, match_score, dry_run))
            if success:
                applied += 1

        console.print(f"\n[bold green]Applied to {applied} jobs[/bold green] "
                      f"({'dry run' if dry_run else 'LIVE'})")
    finally:
        pipeline.close()


@app.command()
def status(
    min_score: int = typer.Option(0, "--min-score"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show current job tracking status and statistics."""
    setup_logging(verbose)
    settings = load_settings()

    pipeline = Pipeline(settings)
    try:
        stats = pipeline.db.get_stats()

        # Stats panel
        console.print(Panel(
            f"Total jobs discovered: [bold]{stats['total_jobs_discovered']}[/bold]\n"
            f"Jobs scored: [bold]{stats['jobs_scored']}[/bold]\n"
            f"Applications submitted: [bold green]{stats['applications_submitted']}[/bold green]\n"
            f"Applications pending: [bold yellow]{stats['applications_pending']}[/bold yellow]\n"
            f"Average match score: [bold]{stats['average_match_score']}[/bold]",
            title="[bold blue]Job Agent Status[/bold blue]",
            border_style="blue",
        ))

        # Top scored jobs
        scored = pipeline.db.get_jobs_by_score(min_score)
        if scored:
            pipeline.notifier.print_job_table(scored[:20])

        # Recent applications
        applications = pipeline.db.get_all_applications()
        if applications:
            app_table = Table(title="Recent Applications")
            app_table.add_column("Status", width=15)
            app_table.add_column("Company", width=20)
            app_table.add_column("Title", width=35)
            app_table.add_column("Score", width=6)
            app_table.add_column("Method", width=15)

            for app in applications[:10]:
                status_style = {
                    "applied": "green",
                    "ready_to_apply": "cyan",
                    "manual_needed": "yellow",
                    "failed": "red",
                    "pending": "dim",
                }.get(app["status"], "dim")

                app_table.add_row(
                    f"[{status_style}]{app['status']}[/{status_style}]",
                    app.get("company", "N/A"),
                    app.get("title", "N/A"),
                    str(app.get("overall_score", "N/A")),
                    app.get("method", "N/A"),
                )
            console.print(app_table)
    finally:
        pipeline.close()


@app.command()
def companies(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List all configured target companies."""
    setup_logging(verbose)
    settings = load_settings()

    from src.company.registry import CompanyRegistry
    registry = CompanyRegistry(settings.resolve_path("config/companies.yaml"))

    table = Table(title="Target Companies")
    table.add_column("Company", width=25)
    table.add_column("ATS", width=12)
    table.add_column("Auto-Apply", width=12)

    for name in registry.whitelisted_companies:
        entry = registry.get_company_entry(name)
        ats = entry.get("ats", "unknown") if entry else "unknown"
        excluded = entry.get("exclude_from_apply", False) if entry else False
        auto = "Yes" if ats in ("greenhouse", "lever") and not excluded else "No"
        auto_style = "green" if auto == "Yes" else "yellow" if not excluded else "red"

        table.add_row(
            name,
            ats,
            f"[{auto_style}]{auto}[/{auto_style}]",
        )

    console.print(table)


@app.command(name="export-dashboard")
def export_dashboard_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export pipeline data for GitHub Pages dashboard."""
    setup_logging(verbose)
    settings = load_settings()

    pipeline = Pipeline(settings)
    try:
        from src.export import export_dashboard, export_markdown
        data_path = export_dashboard(pipeline.db)
        export_markdown(pipeline.db)
        console.print(f"[bold green]Exported![/bold green] Dashboard: {data_path}")
    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)
    finally:
        pipeline.close()


@app.command()
def sync(
    min_score: int = typer.Option(70, "--min-score", help="Min score to sync to career-ops pipeline"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Sync Python agent results into career-ops format (applications.md + pipeline.md).

    Bridges the automated discovery system with career-ops interactive evaluation.
    High-scoring jobs get added to pipeline.md for deep A-F evaluation.
    All scored jobs get tracked in applications.md.
    """
    setup_logging(verbose)
    settings = load_settings()

    pipeline = Pipeline(settings)
    try:
        from src.sync_to_career_ops import sync_to_career_ops
        added_pipeline, added_tracker = sync_to_career_ops(pipeline.db, min_score)
        console.print(f"[bold green]Synced![/bold green] "
                      f"{added_pipeline} jobs added to pipeline.md, "
                      f"{added_tracker} entries added to applications.md")
    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)
    finally:
        pipeline.close()


@app.command()
def digest(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Send email digest of top matches (runs independently, never blocked by apply)."""
    setup_logging(verbose)
    settings = load_settings()

    pipeline = Pipeline(settings)
    try:
        all_qualified = pipeline.db.get_jobs_by_score(settings.matching.min_score_notify)
        stats = pipeline.db.get_stats()
        applied_jobs = []  # Digest shows qualified jobs, not applied ones

        pipeline.notifier.notify_digest(
            stats,
            new_jobs_count=0,
            applied_count=stats.get("applications_submitted", 0),
            manual_count=0,
            qualified_jobs=all_qualified,
            applied_jobs=applied_jobs,
        )
        console.print(f"[bold green]Digest sent![/bold green] "
                      f"({len(all_qualified)} qualified jobs included)")
    except Exception as e:
        console.print(f"[red]Failed to send digest: {e}[/red]")
        raise typer.Exit(1)
    finally:
        pipeline.close()


@app.command(name="refresh-resume")
def refresh_resume(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Extract text from resume PDF for LLM matching."""
    setup_logging(verbose)

    try:
        settings = load_settings()
    except ConfigurationError as e:
        # Allow running even without full config — we just need the paths
        console.print(f"[yellow]Warning: {e}[/yellow]")
        settings = None

    if settings:
        pdf_path = settings.resume_pdf_path
        text_path = settings.resume_text_path
    else:
        root = Path(__file__).resolve().parent.parent
        pdf_path = root / "config" / "resume.pdf"
        text_path = root / "config" / "resume_text.txt"

    if not pdf_path.exists():
        console.print(f"[red]Resume PDF not found: {pdf_path}[/red]")
        raise typer.Exit(1)

    from scripts.extract_resume_text import extract_resume_text
    text = extract_resume_text(pdf_path, text_path)
    console.print(f"[bold green]Done![/bold green] Extracted {len(text)} characters to {text_path}")


if __name__ == "__main__":
    app()
