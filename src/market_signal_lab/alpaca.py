"""Read-only Alpaca Market Data API client.

This module intentionally contains no brokerage or order-placement endpoints.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

HISTORICAL_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


@dataclass(frozen=True)
class AlpacaCredentials:
    """Alpaca credentials loaded from environment variables."""

    key_id: str
    secret_key: str

    @classmethod
    def from_environment(cls) -> AlpacaCredentials:
        key_id = os.environ.get("APCA_API_KEY_ID", "").strip()
        secret_key = os.environ.get("APCA_API_SECRET_KEY", "").strip()
        if not key_id or not secret_key:
            raise ValueError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in the environment")
        return cls(key_id=key_id, secret_key=secret_key)


class AlpacaMarketDataClient:
    """Minimal authenticated client for historical stock bars."""

    def __init__(
        self,
        credentials: AlpacaCredentials,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.credentials = credentials
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def _request_json(self, parameters: dict[str, str | int]) -> dict[str, Any]:
        url = f"{HISTORICAL_BARS_URL}?{urlencode(parameters)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "APCA-API-KEY-ID": self.credentials.key_id,
                "APCA-API-SECRET-KEY": self.credentials.secret_key,
                "User-Agent": "MarketSignalLab/1.0",
            },
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == self.max_retries:
                    raise RuntimeError(f"Alpaca request failed with HTTP {error.code}") from error
                retry_after = error.headers.get("Retry-After") if error.headers else None
                delay = float(retry_after) if retry_after else min(2**attempt, 10)
                time.sleep(delay)
            except URLError as error:
                if attempt == self.max_retries:
                    raise RuntimeError("Unable to reach the Alpaca Market Data API") from error
                time.sleep(min(2**attempt, 10))
        raise RuntimeError("Alpaca request failed")

    def fetch_daily_bars(
        self,
        symbols: list[str],
        *,
        start: str | date,
        end: str | date,
        feed: str = "iex",
        adjustment: str = "all",
        batch_size: int = 50,
    ) -> pd.DataFrame:
        """Download paginated daily bars for one or more symbols."""
        normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        if not normalized:
            raise ValueError("At least one ticker symbol is required")
        if feed not in {"iex", "sip", "otc"}:
            raise ValueError("Feed must be iex, sip, or otc")
        if batch_size < 1 or batch_size > 200:
            raise ValueError("Batch size must be between 1 and 200")

        rows: list[dict[str, object]] = []
        for offset in range(0, len(normalized), batch_size):
            batch = normalized[offset : offset + batch_size]
            token: str | None = None
            seen_tokens: set[str] = set()
            while True:
                parameters: dict[str, str | int] = {
                    "symbols": ",".join(batch),
                    "timeframe": "1Day",
                    "start": str(start),
                    "end": str(end),
                    "limit": 10_000,
                    "adjustment": adjustment,
                    "feed": feed,
                    "sort": "asc",
                }
                if token:
                    parameters["page_token"] = token
                payload = self._request_json(parameters)
                bars_by_symbol = payload.get("bars") or {}
                if not isinstance(bars_by_symbol, dict):
                    raise RuntimeError("Alpaca returned an unexpected bars response")
                for ticker, bars in bars_by_symbol.items():
                    for bar in bars or []:
                        rows.append(
                            {
                                "ticker": ticker.upper(),
                                "timestamp": bar["t"],
                                "open": bar["o"],
                                "high": bar["h"],
                                "low": bar["l"],
                                "close": bar["c"],
                                "volume": bar["v"],
                                "trade_count": bar.get("n"),
                                "vwap": bar.get("vw"),
                                "feed": feed,
                            }
                        )
                token_value = payload.get("next_page_token")
                token = str(token_value) if token_value else None
                if not token:
                    break
                if token in seen_tokens:
                    raise RuntimeError("Alpaca returned a repeated pagination token")
                seen_tokens.add(token)

        columns = [
            "ticker",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
            "feed",
        ]
        frame = pd.DataFrame(rows, columns=columns)
        if not frame.empty:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            frame = frame.sort_values(["ticker", "timestamp"]).drop_duplicates(
                ["ticker", "timestamp"], keep="last"
            )
        return frame.reset_index(drop=True)
