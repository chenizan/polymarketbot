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
