"""Deterministic demonstration data for offline use and testing."""

from __future__ import annotations

import numpy as np
import pandas as pd

from market_signal_lab.io import normalize_prices


def generate_demo_prices(rows: int = 260, seed: int = 42) -> pd.DataFrame:
    """Create repeatable synthetic daily OHLCV data without network access."""
    if rows < 60:
        raise ValueError("Demo data requires at least 60 rows")

    generator = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=rows, freq="B", tz="UTC")
    trend = np.linspace(0, 0.10, rows)
    cycle = 0.035 * np.sin(np.linspace(0, 8 * np.pi, rows))
    daily_noise = generator.normal(0, 0.008, rows)
    close = 100 * np.exp(np.cumsum(daily_noise / 3) + trend + cycle)
    gap = generator.normal(0, 0.0025, rows)
    open_price = close * (1 + gap)
    spread = generator.uniform(0.002, 0.012, rows)
    high = np.maximum(open_price, close) * (1 + spread)
    low = np.minimum(open_price, close) * (1 - spread)
    volume = generator.integers(500_000, 2_500_000, rows)

    return normalize_prices(
        pd.DataFrame(
            {
                "Date": dates,
                "Open": open_price,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
            }
        )
    )
