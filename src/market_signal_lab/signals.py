"""Explainable signal scoring based on normalized technical indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_INDICATORS = (
    "Close",
    "sma_50",
    "bollinger_percent_b",
    "rsi_14",
    "macd_histogram",
    "stochastic_k",
    "cmf_20",
)


def _ternary_score(
    values: pd.Series,
    strong_buy: pd.Series,
    buy: pd.Series,
    sell: pd.Series,
    strong_sell: pd.Series,
) -> pd.Series:
    scores = np.select(
        [strong_buy, buy, strong_sell, sell],
        [2, 1, -2, -1],
        default=0,
    )
    return pd.Series(scores, index=values.index, dtype="int64")


def add_signal_scores(indicators: pd.DataFrame) -> pd.DataFrame:
    """Add component scores, a combined score, and Buy/Hold/Sell labels.

    A score of three or greater produces ``Buy``; minus three or lower produces
    ``Sell``. Rows without a complete indicator warm-up remain ``Hold``.
    """
    missing = [name for name in REQUIRED_INDICATORS if name not in indicators.columns]
    if missing:
        raise ValueError(f"Missing required indicator columns: {', '.join(missing)}")

    result = indicators.copy()
    pb = result["bollinger_percent_b"]
    result["score_bollinger"] = _ternary_score(
        pb,
        pb < 0,
        (pb >= 0) & (pb < 0.20),
        (pb > 0.80) & (pb <= 1),
        pb > 1,
    )

    rsi = result["rsi_14"]
    result["score_rsi"] = _ternary_score(
        rsi,
        rsi < 30,
        (rsi >= 30) & (rsi < 40),
        (rsi > 60) & (rsi <= 70),
        rsi > 70,
    )

    histogram = result["macd_histogram"]
    result["score_macd"] = np.select(
        [
            (histogram > 0) & (histogram.shift(1) <= 0),
            (histogram < 0) & (histogram.shift(1) >= 0),
            histogram > 0,
            histogram < 0,
        ],
        [2, -2, 1, -1],
        default=0,
    )

    stochastic = result["stochastic_k"]
    result["score_stochastic"] = np.select([stochastic < 20, stochastic > 80], [1, -1], default=0)

    cmf = result["cmf_20"]
    result["score_money_flow"] = np.select([cmf > 0.10, cmf < -0.10], [1, -1], default=0)

    result["score_trend"] = np.select(
        [result["Close"] > result["sma_50"], result["Close"] < result["sma_50"]],
        [1, -1],
        default=0,
    )

    score_columns = [column for column in result.columns if column.startswith("score_")]
    result["signal_score"] = result[score_columns].sum(axis=1).astype("int64")
    warmed_up = result[list(REQUIRED_INDICATORS)].notna().all(axis=1)
    result["signal"] = np.select(
        [warmed_up & (result["signal_score"] >= 3), warmed_up & (result["signal_score"] <= -3)],
        ["Buy", "Sell"],
        default="Hold",
    )
    return result
