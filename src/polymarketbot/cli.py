"""Typer CLI for polymarketbot."""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console
from rich.table import Table

from polymarketbot import __version__
from polymarketbot.clients.clob import ClobService
from polymarketbot.clients.gamma import GammaClient
from polymarketbot.config import load_config
from polymarketbot.persistence.store import Store
from polymarketbot.utils.logging import setup_logging


def _configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


_configure_stdio()

app = typer.Typer(
    name="polymarketbot",
    help="Open-source Polymarket CLOB V2 auto-trading bot",
    add_completion=False,
    no_args_is_help=True,
)
console = Console(legacy_windows=False)


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    ctx.ensure_object(dict)
    import logging

    level = logging.DEBUG if verbose else logging.INFO
    cfg = load_config(config_path=config)
    setup_logging(cfg.settings.log_dir, level=level)
    ctx.obj["config"] = cfg


@app.command("version")
def version_cmd() -> None:
    """Show package version."""
    console.print(f"polymarketbot {__version__}")


@app.command("setup")
def setup_cmd(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Non-interactive: paper mode defaults, skip wallet prompts",
    ),
) -> None:
    """Interactive first-time setup (recommended for beginners)."""
    from polymarketbot.setup_wizard import run_setup

    run_setup(yes=yes)


@app.command("setup-env")
def setup_env_cmd(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Only apply safe paper defaults; do not prompt",
    ),
    show: bool = typer.Option(
        False,
        "--show",
        help="Only print how to get each variable (do not edit .env)",
    ),
) -> None:
    """Guided .env editor - explains where to get every value."""
    from polymarketbot.setup_wizard import run_setup_env

    run_setup_env(yes=yes, show_only=show)


@app.command("doctor")
def doctor_cmd(ctx: typer.Context) -> None:
    """Check that your install/.env look healthy."""
    from pathlib import Path

    from polymarketbot.setup_wizard import smoke_test_markets

    cfg = ctx.obj["config"]
    root = Path.cwd()
    console.print("[bold]Doctor check[/bold]\n")

    checks: list[tuple[str, bool, str]] = []
    checks.append(
        (".env exists", (root / ".env").exists(), "Run scripts/setup.bat or polymarketbot setup")
    )
    checks.append(
        (
            "config YAML",
            Path(cfg.settings.config_path).exists()
            or (root / "config" / "default.yaml").exists(),
            "Missing config/default.yaml",
        )
    )
    checks.append(("paper mode", cfg.paper, "LIVE_TRADING is true - real money at risk"))
    checks.append(
        (
            "private key set",
            bool(cfg.settings.private_key)
            and "your_private_key" not in cfg.settings.private_key,
            "Optional for paper; required for live",
        )
    )
    api_ok = smoke_test_markets()
    checks.append(("Polymarket API", api_ok, "Check internet / firewall"))

    for name, ok, hint in checks:
        mark = "[green]PASS[/green]" if ok else "[yellow]WARN[/yellow]"
        console.print(f"  {mark}  {name}")
        if not ok:
            console.print(f"         [dim]{hint}[/dim]")

    if cfg.paper:
        console.print("\n[cyan]You are in paper mode (fake money). Safe to experiment.[/cyan]")
    else:
        console.print("\n[bold red]LIVE TRADING is enabled.[/bold red]")


@app.command("markets")
def markets_cmd(
    ctx: typer.Context,
    query: str | None = typer.Option(None, "--query", "-q", help="Search query"),
    limit: int = typer.Option(15, "--limit", "-n", help="Max markets to show"),
) -> None:
    """Discover Polymarket markets via Gamma API."""
    cfg = ctx.obj["config"]
    filters = cfg.yaml.market_filters.model_copy(deep=True)
    if query:
        filters.query = query
    with GammaClient(cfg.settings.gamma_host) as gamma:
