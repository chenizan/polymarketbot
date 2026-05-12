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
        found = gamma.discover(filters, max_markets=limit)
    table = Table(title="Markets")
    table.add_column("Question", overflow="fold")
    table.add_column("Condition", max_width=14)
    table.add_column("Liq")
    table.add_column("Vol24h")
    table.add_column("Tick")
    for m in found:
        table.add_row(
            m.question[:80],
            m.condition_id[:12] + "...",
            f"{m.liquidity:,.0f}",
            f"{m.volume_24h:,.0f}",
            m.tick_size,
        )
    console.print(table)
    if not found:
        console.print("[yellow]No markets matched filters[/yellow]")


@app.command("derive-keys")
def derive_keys_cmd(ctx: typer.Context) -> None:
    """Derive L2 API credentials from PRIVATE_KEY (prints once; store in .env)."""
    cfg = ctx.obj["config"]
    if not cfg.settings.private_key:
        console.print("[red]PRIVATE_KEY is required in .env[/red]")
        raise typer.Exit(1)
    clob = ClobService(
        host=cfg.settings.clob_host,
        chain_id=cfg.settings.chain_id,
        private_key=cfg.settings.private_key,
    )
    creds = clob.derive_api_key()
    console.print("[green]Derived API credentials - add these to your .env:[/green]")
    console.print(f"CLOB_API_KEY={creds['api_key']}")
    console.print(f"CLOB_API_SECRET={creds['api_secret']}")
    console.print(f"CLOB_API_PASSPHRASE={creds['api_passphrase']}")
    console.print(
        "[dim]Never commit these values. Rotate if exposed.[/dim]"
    )


@app.command("run")
def run_cmd(
    ctx: typer.Context,
    strategy: list[str] | None = typer.Option(
        None,
        "--strategy",
        "-s",
        help="Strategy name(s) to enable (repeatable). Default: YAML enabled set.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Enable live trading (also requires LIVE_TRADING=true in env).",
    ),
) -> None:
    """Run the trading bot (paper mode by default)."""
    from polymarketbot.engine.bot import run_bot

    cfg = ctx.obj["config"]
    if live:
        if not cfg.settings.live_trading:
            console.print(
                "[red]Refusing --live without LIVE_TRADING=true in environment[/red]"
            )
            raise typer.Exit(1)
        console.print("[bold red]LIVE TRADING - real funds at risk[/bold red]")
    else:
        # Force paper even if env accidentally set (unless --live)
        cfg.settings.live_trading = False
        console.print("[cyan]Paper trading mode (dry-run)[/cyan]")

    names = list(strategy) if strategy else None
    asyncio.run(run_bot(cfg, strategy_names=names))


@app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    """Show latest PnL snapshot, open orders, and recent fills from SQLite."""
    cfg = ctx.obj["config"]
    store = Store(cfg.settings.db_path)
    try:
        pnl = store.latest_pnl()
        if pnl:
            console.print(
                f"[bold]Equity[/bold]={pnl.get('equity'):.2f}  "
                f"cash={pnl.get('cash'):.2f}  "
                f"daily_pnl={pnl.get('daily_pnl'):.2f}  "
                f"exposure={pnl.get('exposure'):.2f}"
            )
        else:
            console.print("[yellow]No PnL snapshots yet - run the bot first[/yellow]")

        orders = store.open_orders()
        table = Table(title=f"Open orders ({len(orders)})")
        table.add_column("Strategy")
        table.add_column("Side")
        table.add_column("Size")
        table.add_column("Price")
        table.add_column("Status")
        for o in orders[:30]:
            table.add_row(
                str(o.get("strategy")),
                str(o.get("side")),
                f"{float(o.get('size') or 0):.2f}",
                f"{float(o.get('price') or 0):.4f}",
                str(o.get("status")),
            )
        console.print(table)

        fills = store.recent_fills(15)
        ft = Table(title="Recent fills")
        ft.add_column("When")
        ft.add_column("Side")
        ft.add_column("Size")
        ft.add_column("Price")
        ft.add_column("Strategy")
        for f in fills:
            ft.add_row(
                str(f.get("created_at", ""))[:19],
                str(f.get("side")),
                f"{float(f.get('size') or 0):.2f}",
                f"{float(f.get('price') or 0):.4f}",
                str(f.get("strategy")),
            )
        console.print(ft)
    finally:
        store.close()


@app.command("cancel-all")
def cancel_all_cmd(ctx: typer.Context) -> None:
    """Cancel all open orders on the CLOB (live only)."""
    cfg = ctx.obj["config"]
    if cfg.paper and not cfg.settings.live_trading:
        console.print("[yellow]Paper mode - nothing to cancel on exchange[/yellow]")
        raise typer.Exit(0)
    if not cfg.settings.private_key:
        console.print("[red]PRIVATE_KEY required[/red]")
        raise typer.Exit(1)
    clob = ClobService(
        host=cfg.settings.clob_host,
        chain_id=cfg.settings.chain_id,
        private_key=cfg.settings.private_key,
        api_key=cfg.settings.clob_api_key,
        api_secret=cfg.settings.clob_api_secret,
        api_passphrase=cfg.settings.clob_api_passphrase,
        signature_type=cfg.settings.signature_type,
        funder=cfg.settings.funder_address,
    )
    clob.connect(require_auth=True)
    result = clob.cancel_all()
    console.print(f"[green]cancel_all submitted[/green]: {result}")


if __name__ == "__main__":
    app()

