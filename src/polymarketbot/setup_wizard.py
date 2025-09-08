"""Interactive first-time setup and .env guided configuration."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


_configure_stdio()

console = Console(legacy_windows=False)


def _ascii_safe(text: str) -> str:
    """Replace fancy punctuation that breaks legacy Windows consoles."""
    replacements = {
        "\u2192": "->",  # →
        "\u2190": "<-",  # ←
        "\u2014": ",",  # em dash
        "\u2013": "-",  # en dash
        "\u2026": "...",  # …
        "\u2022": "*",  # •
        "\u22ef": "...",  # ⋯
        "\u00b7": "-",  # ·
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text

ROOT = Path(__file__).resolve().parents[2]

# (key, required_for_live, how_to_get_short, detailed_help)
ENV_GUIDE: list[tuple[str, bool, str, str]] = [
    (
        "LIVE_TRADING",
        False,
        "Type false (paper) or true (real money)",
        "Keep **false** for fake-money paper trading.\n"
        "Set **true** only when you intentionally want real orders.\n"
        "Even with true, `polymarketbot run` stays paper unless you also pass `--live`.",
    ),
    (
        "KILL_SWITCH",
        False,
        "Type false normally; true freezes trading",
        "Emergency stop. You can also create an empty file `data/KILL`.\n"
        "When active, the bot refuses new trades.",
    ),
    (
        "PRIVATE_KEY",
        True,
        "Export from MetaMask (Account details -> Show private key)",
        "1. Open MetaMask -> account ... -> **Account details** -> **Show private key**\n"
        "2. Copy the key (usually starts with `0x`)\n"
        "3. Use a **dedicated bot wallet**, not your main funds\n"
        "4. Never paste this in Discord/GitHub\n"
        "Guide: fill .env or run polymarketbot setup-env",
    ),
    (
        "SIGNATURE_TYPE",
        True,
        "0=EOA, 1=proxy, 2=Safe, 3=deposit wallet",
        "**0** EOA - normal MetaMask wallet (needs POL for gas)\n"
        "**1** POLY_PROXY - legacy Magic/email Polymarket proxy\n"
        "**2** GNOSIS_SAFE - Safe wallet\n"
        "**3** POLY_1271 - deposit wallet (common for new API users)\n"
        "Docs: https://docs.polymarket.com/trading/overview",
    ),
    (
        "FUNDER_ADDRESS",
        True,
        "Copy from polymarket.com Settings (address that holds funds)",
        "1. Log into https://polymarket.com\n"
        "2. Open Settings / wallet section\n"
        "3. Copy the address that **holds your balance**\n"
        "   (proxy / safe / deposit - may differ from the browser EOA)\n"
        "4. For plain EOA (`SIGNATURE_TYPE=0`), this is usually your own address",
    ),
    (
        "CLOB_API_KEY",
        True,
        "Auto: run polymarketbot derive-keys (do not invent these)",
        "These three CLOB_* values are created from your PRIVATE_KEY.\n"
        "Run `polymarketbot derive-keys` or say Yes when this wizard offers to derive them.\n"
        "You do not get them from a Polymarket website form.",
    ),
    (
        "CLOB_API_SECRET",
        True,
        "Auto: derive-keys",
        "Filled together with CLOB_API_KEY by derive-keys.",
    ),
    (
        "CLOB_API_PASSPHRASE",
        True,
        "Auto: derive-keys",
        "Filled together with CLOB_API_KEY by derive-keys.",
    ),
    (
        "TELEGRAM_BOT_TOKEN",
        False,
        "Optional - create a bot with @BotFather on Telegram",
        "1. Open Telegram -> @BotFather -> /newbot\n"
        "2. Copy the token like `123456:ABC...`\n"
        "3. Paste here. Leave blank to skip alerts.",
    ),
    (
        "TELEGRAM_CHAT_ID",
        False,
        "Optional - from getUpdates after messaging your bot",
        "1. Message your bot (press Start)\n"
        "2. Open: https://api.telegram.org/bot<TOKEN>/getUpdates\n"
        "3. Find chat id number -> paste here",
    ),
]


def _repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "config" / "default.yaml").exists():
        return cwd
    if (ROOT / "pyproject.toml").exists():
        return ROOT
    return cwd


def ensure_env_file(root: Path) -> Path:
    env_path = root / ".env"
    example = root / ".env.example"
    if env_path.exists():
        console.print(f"[green]OK[/green] Found .env at {env_path}")
        return env_path
    if not example.exists():
        console.print("[red]Missing .env.example - are you in the project folder?[/red]")
        raise SystemExit(1)
    shutil.copy(example, env_path)
    console.print(f"[green]Created[/green] {env_path} from .env.example")
    return env_path


def read_env_map(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        values[key.strip()] = val.strip()
    return values


def upsert_env(env_path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def smoke_test_markets() -> bool:
    try:
        from polymarketbot.clients.gamma import GammaClient
        from polymarketbot.config import MarketFilters

        with GammaClient() as gamma:
            filters = MarketFilters(min_liquidity=0, min_volume_24h=0)
            markets = gamma.discover(filters, max_markets=3)
        if not markets:
            console.print("[yellow]Connected, but no markets returned (filters/API).[/yellow]")
            return True
        console.print("[green]Internet + Polymarket API OK[/green] - sample markets:")
        for m in markets[:3]:
            console.print(f"  - {m.question[:70]}")
        return True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not reach Polymarket:[/red] {exc}")
        return False


def print_env_cheatsheet() -> None:
    table = Table(title="Where to get each .env value", show_lines=True)
    table.add_column("Variable", style="cyan", no_wrap=True)
    table.add_column("Live?", justify="center")
    table.add_column("How to get it")
    for key, live, short, _detail in ENV_GUIDE:
        table.add_row(key, "yes" if live else "no", short)
    console.print(table)
    console.print(
        "\nFor live keys later: [bold]polymarketbot setup-env[/bold]\n"
    )


def _mask(value: str) -> str:
    if not value:
        return "[dim](empty)[/dim]"
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def _prompt_value(key: str, current: str, *, secret: bool = False) -> str | None:
    """Return new value, empty string to clear, or None to skip."""
    shown = _mask(current) if secret else (current or "(empty)")
    console.print(f"Current [cyan]{key}[/cyan]: {shown}")
    if not Confirm.ask(f"Change {key}?", default=False):
        return None
    if secret:
        raw = Prompt.ask(f"New {key} (leave blank to keep current)", password=True, default="")
    else:
        raw = Prompt.ask(f"New {key} (leave blank to keep current)", default="")
    if raw == "":
        return None
    return raw.strip()


def derive_and_save_keys(env_path: Path, private_key: str) -> bool:
    try:
        from polymarketbot.clients.clob import ClobService
        from polymarketbot.config import load_config

        cfg = load_config(env_file=env_path)
        clob = ClobService(
            host=cfg.settings.clob_host,
            chain_id=cfg.settings.chain_id,
            private_key=private_key,
        )
        creds = clob.derive_api_key()
        upsert_env(env_path, "CLOB_API_KEY", creds["api_key"])
        upsert_env(env_path, "CLOB_API_SECRET", creds["api_secret"])
        upsert_env(env_path, "CLOB_API_PASSPHRASE", creds["api_passphrase"])
        console.print("[green]Saved CLOB_API_KEY / SECRET / PASSPHRASE to .env[/green]")
        return True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Could not derive keys:[/yellow] {exc}")
        console.print("Fix PRIVATE_KEY / network, then run: [bold]polymarketbot derive-keys[/bold]")
        return False


def run_setup_env(*, yes: bool = False, show_only: bool = False) -> None:
    """Guided .env configuration with how-to text for every variable."""
    root = _repo_root()
    console.print(
        Panel.fit(
            "[bold].env setup helper[/bold]\n"
            "Shows where each value comes from, then can write your .env for you.",
            border_style="cyan",
        )
    )
    print_env_cheatsheet()
    if show_only:
        print_env_cheatsheet()
        return

    env_path = ensure_env_file(root)
    values = read_env_map(env_path)

    if yes:
        upsert_env(env_path, "LIVE_TRADING", "false")
        upsert_env(env_path, "KILL_SWITCH", "false")
        console.print("[green]Applied safe paper defaults (LIVE_TRADING=false).[/green]")
        console.print("Re-run without [cyan]--yes[/cyan] to fill wallet keys interactively.")
        return

    console.print(
        "\n[bold]Walkthrough[/bold] - press Enter to skip any value you do not have yet.\n"
        "Paper mode only needs LIVE_TRADING=false.\n"
    )

    # Mode first
    console.print(Panel(ENV_GUIDE[0][3], title="LIVE_TRADING", border_style="blue"))
    if Confirm.ask("Stay on paper trading (recommended)?", default=True):
        upsert_env(env_path, "LIVE_TRADING", "false")
    else:
        if Confirm.ask("Enable LIVE_TRADING=true? (real money)", default=False):
            upsert_env(env_path, "LIVE_TRADING", "true")
        else:
            upsert_env(env_path, "LIVE_TRADING", "false")

    console.print(Panel(ENV_GUIDE[1][3], title="KILL_SWITCH", border_style="blue"))
    kill = Prompt.ask("KILL_SWITCH", default=values.get("KILL_SWITCH") or "false")
    upsert_env(env_path, "KILL_SWITCH", kill.strip().lower())

    want_live_creds = Confirm.ask(
        "Set up wallet + API keys now? (needed for live trading)",
        default=False,
    )
    if want_live_creds:
        for key, _live, _short, detail in ENV_GUIDE[2:5]:
            console.print(Panel(detail, title=key, border_style="magenta"))
            secret = key == "PRIVATE_KEY"
            new_val = _prompt_value(key, values.get(key, ""), secret=secret)
            if new_val is not None:
                upsert_env(env_path, key, new_val)
                values[key] = new_val

        values = read_env_map(env_path)
        pk = values.get("PRIVATE_KEY", "")
        if pk and Confirm.ask(
            "Derive CLOB_API_KEY / SECRET / PASSPHRASE automatically?",
            default=True,
        ):
            console.print(
                Panel(
                    ENV_GUIDE[5][3],
                    title="CLOB API credentials",
                    border_style="magenta",
                )
            )
            derive_and_save_keys(env_path, pk)
        else:
            console.print(
                "[dim]Skipped derive-keys. You can run polymarketbot derive-keys later.[/dim]"
            )
    else:
        console.print("[dim]Skipped wallet keys - paper trading still works.[/dim]")

    if Confirm.ask("Configure optional Telegram alerts?", default=False):
        for key, _live, _short, detail in ENV_GUIDE[8:10]:
            console.print(Panel(detail, title=key, border_style="green"))
            new_val = _prompt_value(key, values.get(key, ""), secret=False)
            if new_val is not None:
                upsert_env(env_path, key, new_val)

    console.print(f"\n[green]Saved[/green] {env_path}")
    console.print("Next: [cyan]scripts/run-paper.bat[/cyan]  or  [cyan]polymarketbot doctor[/cyan]")


def run_setup(*, yes: bool = False) -> None:
    root = _repo_root()
    console.print(
        Panel.fit(
            "[bold]Polymarket Bot - Easy Setup[/bold]\n"
            "Fake money first. No wallet needed to try paper trading.",
            border_style="cyan",
        )
    )
    console.print(f"Project folder: [bold]{root}[/bold]\n")

    (root / "data").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)

    env_path = ensure_env_file(root)
    upsert_env(env_path, "LIVE_TRADING", "false")
    upsert_env(env_path, "KILL_SWITCH", "false")

    console.print("\n[bold]Step 1 - Mode[/bold]")
    console.print("Paper mode uses fake money. This is what almost everyone should use first.")
    if yes or Confirm.ask("Stay on paper trading (recommended)?", default=True):
        upsert_env(env_path, "LIVE_TRADING", "false")
        console.print("[green]Paper trading ON[/green] - no real money will be spent.")
    else:
        console.print("[bold red]Live trading is dangerous.[/bold red]")
        if Confirm.ask("I understand the risk and still want LIVE_TRADING=true?", default=False):
            upsert_env(env_path, "LIVE_TRADING", "true")
        else:
            upsert_env(env_path, "LIVE_TRADING", "false")

    console.print("\n[bold]Step 2 - .env / wallet[/bold]")
    console.print(
        "Paper mode needs no keys. For live later, run [cyan]polymarketbot setup-env[/cyan]"
    )
    if not yes and Confirm.ask(
        "Run guided .env setup now (shows how to get each value)?",
        default=False,
    ):
        run_setup_env(yes=False)
    elif not yes and Confirm.ask("Only add a PRIVATE_KEY quickly?", default=False):
        console.print(Panel(ENV_GUIDE[2][3], title="PRIVATE_KEY", border_style="magenta"))
        pk = Prompt.ask("Paste PRIVATE_KEY (starts with 0x)", password=True)
        pk = pk.strip()
        if pk:
            upsert_env(env_path, "PRIVATE_KEY", pk)
            console.print("[green]Saved PRIVATE_KEY[/green]")
            if Confirm.ask("Derive API keys now?", default=True):
                derive_and_save_keys(env_path, pk)
    else:
        console.print("[dim]Skipped wallet. Run polymarketbot setup-env whenever you want.[/dim]")

    console.print("\n[bold]Step 3 - Connection test[/bold]")
    ok = smoke_test_markets()

    console.print("\n[bold]Done![/bold] Next:\n")
    console.print("  - Paper trade:  [cyan]scripts/run-paper.bat[/cyan]")
    console.print("  - Fill .env:    [cyan]polymarketbot setup-env[/cyan]")
    console.print("  - Health check: [cyan]polymarketbot doctor[/cyan]")
    console.print("\nStop the bot anytime with [bold]Ctrl+C[/bold].")
    if not ok:
        console.print("\n[yellow]API test failed - check internet and retry.[/yellow]")
        raise SystemExit(1)


def ensure_installed(root: Path) -> None:
    try:
        import polymarketbot  # noqa: F401
    except ImportError:
        console.print("[yellow]Package not installed in this Python. Installing...[/yellow]")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(root)])

