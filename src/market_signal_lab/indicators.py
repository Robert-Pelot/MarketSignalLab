"""Technical-indicator calculations built with pandas and NumPy."""

from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _validate_prices(prices: pd.DataFrame) -> None:
    missing = [column for column in PRICE_COLUMNS if column not in prices.columns]
    if missing:
        raise ValueError(f"Missing required price columns: {', '.join(missing)}")
    if prices.empty:
        raise ValueError("Price data is empty")


def bollinger_bands(
    close: pd.Series, window: int = 20, standard_deviations: float = 2.0
) -> pd.DataFrame:
    """Return the Bollinger middle, upper, lower, and normalized percent-B series."""
    middle = close.rolling(window, min_periods=window).mean()
    deviation = close.rolling(window, min_periods=window).std(ddof=0)
    upper = middle + standard_deviations * deviation
    lower = middle - standard_deviations * deviation
    width = (upper - lower).replace(0, np.nan)
    percent_b = (close - lower) / width
    return pd.DataFrame(
        {
            "bollinger_mid": middle,
            "bollinger_upper": upper,
            "bollinger_lower": lower,
            "bollinger_percent_b": percent_b,
        },
        index=close.index,
    )


def relative_strength_index(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Wilder's relative strength index (RSI)."""
    change = close.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)
    average_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    average_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + relative_strength))
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    result = result.mask((average_loss == 0) & (average_gain == 0), 50.0)
    return result.rename("rsi_14")


def moving_average_convergence_divergence(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Return the MACD line, signal line, and histogram."""
    fast = close.ewm(span=fast_period, adjust=False).mean()
    slow = close.ewm(span=slow_period, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=signal_period, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd,
            "macd_signal": signal,
            "macd_histogram": macd - signal,
        },
        index=close.index,
    )


def average_true_range(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Wilder's average true range (ATR)."""
    previous_close = prices["Close"].shift(1)
    ranges = pd.concat(
        [
            prices["High"] - prices["Low"],
            (prices["High"] - previous_close).abs(),
            (prices["Low"] - previous_close).abs(),
        ],
        axis=1,
    )
    true_range = ranges.max(axis=1)
    return (
        true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().rename("atr_14")
    )


def stochastic_oscillator(prices: pd.DataFrame, period: int = 14, smooth: int = 3) -> pd.DataFrame:
    """Return stochastic percent-K and smoothed percent-D."""
    lowest = prices["Low"].rolling(period, min_periods=period).min()
    highest = prices["High"].rolling(period, min_periods=period).max()
    spread = (highest - lowest).replace(0, np.nan)
    percent_k = 100 * (prices["Close"] - lowest) / spread
    percent_d = percent_k.rolling(smooth, min_periods=smooth).mean()
    return pd.DataFrame({"stochastic_k": percent_k, "stochastic_d": percent_d}, index=prices.index)


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Calculate on-balance volume (OBV)."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum().rename("obv")


def chaikin_money_flow(prices: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate Chaikin money flow (CMF)."""
    spread = prices["High"] - prices["Low"]
    multiplier = (
        (2 * prices["Close"] - prices["Low"] - prices["High"]) / spread.replace(0, np.nan)
    ).fillna(0)
    money_flow_volume = multiplier * prices["Volume"]
    volume_sum = prices["Volume"].rolling(period, min_periods=period).sum().replace(0, np.nan)
    return (money_flow_volume.rolling(period, min_periods=period).sum() / volume_sum).rename(
        "cmf_20"
    )


def add_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of *prices* with the project's supported indicators added."""
    _validate_prices(prices)
    result = prices.copy()
    result["sma_20"] = result["Close"].rolling(20, min_periods=20).mean()
    result["sma_50"] = result["Close"].rolling(50, min_periods=50).mean()
    result = result.join(bollinger_bands(result["Close"]))
    result["rsi_14"] = relative_strength_index(result["Close"])
    result = result.join(moving_average_convergence_divergence(result["Close"]))
    result["atr_14"] = average_true_range(result)
    result = result.join(stochastic_oscillator(result))
    result["obv"] = on_balance_volume(result["Close"], result["Volume"])
    result["cmf_20"] = chaikin_money_flow(result)
    return result
