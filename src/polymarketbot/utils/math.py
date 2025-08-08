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

