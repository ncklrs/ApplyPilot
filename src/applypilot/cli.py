"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    max_llm_calls: int = typer.Option(0, "--max-llm-calls", help="Cap total LLM calls for this run (0 = unlimited)."),
    agent: bool = typer.Option(False, "--agent", help="Use Claude Code agent for tailor/cover (higher quality, requires claude CLI)."),
    model: str = typer.Option("sonnet", "--model", "-m", help="Claude model for --agent mode (sonnet, haiku, opus)."),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _bootstrap()

    if max_llm_calls > 0:
        from applypilot.llm import set_budget
        set_budget(max_llm_calls)

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate --agent mode
    if agent:
        from applypilot.scoring.agent import is_agent_available
        if not is_agent_available():
            console.print(
                "[red]--agent requires Claude Code CLI.[/red]\n"
                "Install from [bold]https://claude.ai/code[/bold] or omit --agent to use LLM API."
            )
            raise typer.Exit(code=1)
        console.print("[cyan]Agent mode enabled — using Claude Code CLI for tailor/cover[/cyan]")

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        use_agent=agent,
        agent_model=model,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def apply(
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max applications to submit."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel browser workers."),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for job selection."),
    model: str = typer.Option("haiku", "--model", "-m", help="Claude model name."),
    continuous: bool = typer.Option(False, "--continuous", "-c", help="Run forever, polling for new jobs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without submitting."),
    headless: bool = typer.Option(False, "--headless", help="Run browsers in headless mode."),
    url: Optional[str] = typer.Option(None, "--url", help="Apply to a specific job URL."),
    gen: bool = typer.Option(False, "--gen", help="Generate prompt file for manual debugging instead of running."),
    mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
    mark_failed: Optional[str] = typer.Option(None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Launch auto-apply to submit job applications."""
    _bootstrap()

    from applypilot.config import check_tier, PROFILE_PATH as _profile_path
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/Claude needed) ---

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    # Check 1: Tier 3 required (Claude Code CLI + Chrome)
    check_tier(3, "auto-apply")

    # Check 2: Profile exists
    if not _profile_path.exists():
        console.print(
            "[red]Profile not found.[/red]\n"
            "Run [bold]applypilot init[/bold] to create your profile first."
        )
        raise typer.Exit(code=1)

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.apply.launcher import gen_prompt, BASE_CDP_PORT
        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
        mcp_path = _profile_path.parent / ".mcp-apply-0.json"
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print(f"\n[bold]Run manually:[/bold]")
        console.print(
            f"  claude --model {model} -p "
            f"--mcp-config {mcp_path} "
            f"--permission-mode bypassPermissions < {prompt_file}"
        )
        return

    from applypilot.apply.launcher import main as apply_main

    effective_limit = limit if limit is not None else (0 if continuous else 1)

    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Limit:    {'unlimited' if continuous else effective_limit}")
    console.print(f"  Workers:  {workers}")
    console.print(f"  Model:    {model}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=continuous,
        workers=workers,
    )


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def dedup(
    title_threshold: float = typer.Option(0.8, "--title-threshold", help="Min title similarity (0-1)."),
    company_threshold: float = typer.Option(0.7, "--company-threshold", help="Min company name similarity (0-1)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report duplicates without deleting."),
) -> None:
    """Find and remove fuzzy duplicate job postings."""
    _bootstrap()

    from applypilot.dedup import remove_duplicates
    from applypilot.database import get_connection

    conn = get_connection()
    result = remove_duplicates(
        conn,
        title_threshold=title_threshold,
        company_threshold=company_threshold,
        dry_run=dry_run,
    )

    console.print(f"\n[bold]Fuzzy Dedup Results[/bold]")
    console.print(f"  Duplicate pairs found: {result['found']}")
    console.print(f"  Removed: {result['removed']}")

    if result["duplicates"]:
        table = Table(title="Sample Duplicates", show_header=True)
        table.add_column("Keep URL")
        table.add_column("Remove URL")
        table.add_column("Similarity", justify="right")
        for keep, remove, sim in result["duplicates"]:
            table.add_row(keep[:60], remove[:60], sim)
        console.print(table)

    if dry_run and result["found"]:
        console.print("\n[dim]Run without --dry-run to delete duplicates.[/dim]")
    console.print()


@app.command(name="convert-resume")
def convert_resume_cmd(
    input_file: str = typer.Argument(..., help="Path to resume file (.pdf, .docx, .md, .txt)."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output .txt path. Default: ~/.applypilot/resume.txt"),
) -> None:
    """Convert a resume from PDF/DOCX/Markdown to plain text."""
    from pathlib import Path
    from applypilot.resume_parser import convert_resume
    from applypilot.config import RESUME_PATH

    src = Path(input_file).expanduser().resolve()
    if not src.exists():
        console.print(f"[red]File not found:[/red] {src}")
        raise typer.Exit(code=1)

    out_path = Path(output) if output else RESUME_PATH

    try:
        text = convert_resume(src, output_path=out_path)
        console.print(f"[green]Converted {src.name} -> {out_path}[/green] ({len(text)} chars)")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except ImportError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[dim]Install optional deps: pip install applypilot[resume][/dim]")
        raise typer.Exit(code=1)


@app.command(name="discover-ats")
def discover_ats_cmd(
    url: str = typer.Argument(..., help="Company career page URL to probe."),
) -> None:
    """Auto-detect which ATS a company uses and output a YAML snippet."""
    _bootstrap()

    from applypilot.discovery.ats_detect import detect_ats

    console.print(f"[cyan]Probing {url}...[/cyan]")

    result = detect_ats(url)
    if not result:
        console.print("[yellow]No ATS detected.[/yellow] The site may use a custom career page.")
        console.print("[dim]Tip: Try the direct career page URL (e.g. https://example.com/careers)[/dim]")
        raise typer.Exit(code=1)

    console.print(f"\n[green]Detected ATS:[/green] [bold]{result['ats'].upper()}[/bold]")
    console.print(f"  Token: {result['token']}")
    console.print(f"  Config: {result['config_file']}")
    console.print(f"\n[bold]Add to {result['config_file']}:[/bold]")
    console.print(f"\n{result['yaml_snippet']}")
    console.print()


@app.command()
def track(
    url: str = typer.Argument(..., help="Job URL to update."),
    stage: Optional[str] = typer.Option(None, "--stage", "-s", help="Set manual stage (interview, offer, rejected, withdrawn)."),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Add notes to this job."),
) -> None:
    """Manually track a job's stage or add notes."""
    _bootstrap()

    from applypilot.database import get_connection
    from datetime import datetime, timezone

    conn = get_connection()

    # Verify job exists
    row = conn.execute("SELECT url, title, site FROM jobs WHERE url = ?", (url,)).fetchone()
    if not row:
        console.print(f"[red]Job not found:[/red] {url}")
        raise typer.Exit(code=1)

    updates = []
    params = []

    if stage:
        valid_stages = ("interview", "offer", "rejected", "withdrawn", "ghosted", "negotiating")
        if stage not in valid_stages:
            console.print(f"[red]Invalid stage:[/red] '{stage}'. Valid: {', '.join(valid_stages)}")
            raise typer.Exit(code=1)
        updates.append("user_stage = ?")
        params.append(stage)

    if notes:
        updates.append("user_notes = ?")
        params.append(notes)

    if not updates:
        console.print("[yellow]Specify --stage or --notes to update.[/yellow]")
        raise typer.Exit(code=1)

    params.append(url)
    conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE url = ?", params)
    conn.commit()

    console.print(f"[green]Updated:[/green] {row['title']} @ {row['site']}")
    if stage:
        console.print(f"  Stage: {stage}")
    if notes:
        console.print(f"  Notes: {notes}")


@app.command()
def inbox(
    days: int = typer.Option(7, "--days", "-d", help="Look back this many days."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show matches without updating DB."),
) -> None:
    """Scan email inbox for application responses (confirmations, interviews, rejections)."""
    _bootstrap()

    from applypilot.inbox import scan_inbox, update_from_inbox

    try:
        matches = scan_inbox(since_days=days)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        console.print(
            "\n[dim]Add to ~/.applypilot/.env:[/dim]\n"
            "  IMAP_EMAIL=your@gmail.com\n"
            "  IMAP_PASSWORD=your-app-password\n"
            "\n[dim]Generate an App Password at: https://myaccount.google.com/apppasswords[/dim]"
        )
        raise typer.Exit(code=1)
    except ConnectionError as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        raise typer.Exit(code=1)

    if not matches:
        console.print("[dim]No application-related emails found.[/dim]")
        return

    # Display results
    table = Table(title=f"Inbox Scan ({len(matches)} matches)", show_header=True)
    table.add_column("Date", style="dim")
    table.add_column("Status")
    table.add_column("Subject")
    table.add_column("Matched Job")

    status_colors = {
        "confirmation": "green",
        "interview": "bold green",
        "rejection": "red",
        "follow_up": "yellow",
    }

    for m in matches:
        date_short = m["date"][:10] if m["date"] else "?"
        color = status_colors.get(m["classification"], "white")
        status_display = f"[{color}]{m['classification']}[/{color}]"
        job_match = m["matched_jobs"][0]["title"][:30] if m["matched_jobs"] else "[dim]unmatched[/dim]"
        table.add_row(date_short, status_display, m["subject"][:50], job_match)

    console.print(table)

    if dry_run:
        console.print("\n[dim]Run without --dry-run to update the database.[/dim]")
        return

    result = update_from_inbox(matches)
    console.print(f"\n[green]Updated {result['updated']} jobs[/green], {result['unmatched']} unmatched")


if __name__ == "__main__":
    app()
