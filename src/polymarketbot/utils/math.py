"""Numeric helpers for books and indicators."""

from __future__ import annotations

import math
import statistics

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def safe_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0

def safe_spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return ask - bid

def zscore(values: list[float], latest: float | None = None) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0 or math.isnan(stdev):
        return 0.0
    x = values[-1] if latest is None else latest
    return (x - mean) / stdev

def pct_change(older: float, newer: float) -> float:
    if older == 0:
        return 0.0
    return (newer - older) / older

def round_to_tick(price: float, tick_size: str | float) -> float:
    tick = float(tick_size)
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 8)

