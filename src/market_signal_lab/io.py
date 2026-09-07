"""Input validation and output helpers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


def normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize and validate an OHLCV DataFrame."""
    aliases = {"datetime": "Date", "timestamp": "Date", "date": "Date"}
    canonical = {column.lower(): column for column in REQUIRED_COLUMNS}
    rename: dict[str, str] = {}
    for column in frame.columns:
        lowered = str(column).strip().lower()
        if lowered in aliases:
            rename[column] = aliases[lowered]
        elif lowered in canonical:
            rename[column] = canonical[lowered]

    result = frame.rename(columns=rename).copy()
    missing = [column for column in REQUIRED_COLUMNS if column not in result.columns]
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")

    result = result[list(REQUIRED_COLUMNS)]
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce", utc=True)
    for column in REQUIRED_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    if result.isna().any().any():
        raise ValueError("Price data contains missing or invalid values")
    if (result[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise ValueError("Prices must be greater than zero")
    if (result["Volume"] < 0).any():
        raise ValueError("Volume cannot be negative")
    if (result["High"] < result[["Open", "Close", "Low"]].max(axis=1)).any():
        raise ValueError("High must be the largest price in each row")
    if (result["Low"] > result[["Open", "Close", "High"]].min(axis=1)).any():
        raise ValueError("Low must be the smallest price in each row")

    result = result.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    if len(result) < 60:
        raise ValueError("At least 60 price rows are required for indicator warm-up")
    return result


def load_prices(path: str | Path) -> pd.DataFrame:
    """Load and validate an OHLCV CSV file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Price file not found: {source}")
    return normalize_prices(pd.read_csv(source))


def save_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    """Save a DataFrame to CSV, creating the destination directory if necessary."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False)
    return destination
