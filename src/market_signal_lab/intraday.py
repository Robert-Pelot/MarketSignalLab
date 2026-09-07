"""Resumable one-minute market-data backfills."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from market_signal_lab.alpaca import AlpacaMarketDataClient


@dataclass(frozen=True)
class MinuteBackfillProgress:
    """Progress for one committed or previously completed backfill job."""

    position: int
    total: int
    start: date
    end: date
    symbol_count: int
    rows: int
    skipped: bool


@dataclass(frozen=True)
class MinuteBackfillResult:
    """Summary of a resumable minute-bar backfill."""

    jobs_completed: int
    jobs_skipped: int
    rows_merged: int
    symbols_updated: int


def _as_date(value: str | date, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from error


def _date_chunks(start: date, end: date, chunk_days: int) -> Iterator[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _normalize_symbols(symbols: list[str]) -> list[str]:
    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not normalized:
        raise ValueError("At least one ticker symbol is required")
    return normalized


def _api_bounds(start: date, end: date) -> tuple[str, str]:
    """Return an inclusive date range as explicit New York API boundaries."""
    market_timezone = ZoneInfo("America/New_York")
    start_at = datetime.combine(start, time.min, tzinfo=market_timezone)
    end_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=market_timezone)
    return start_at.isoformat(), end_at.isoformat()


def _create_intraday_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_minute_bars (
            ticker VARCHAR NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            open DOUBLE NOT NULL,
            high DOUBLE NOT NULL,
            low DOUBLE NOT NULL,
            close DOUBLE NOT NULL,
            volume DOUBLE NOT NULL,
            trade_count DOUBLE,
            vwap DOUBLE,
            feed VARCHAR NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        );
        CREATE TABLE IF NOT EXISTS provider_minute_coverage (
            ticker VARCHAR NOT NULL,
            feed VARCHAR NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            row_count BIGINT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
        );
        CREATE TABLE IF NOT EXISTS provider_minute_update_log (
            completed_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
            feed VARCHAR NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            symbols_requested BIGINT NOT NULL,
            symbols_with_bars BIGINT NOT NULL,
            rows_merged BIGINT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS provider_minute_coverage_lookup
            ON provider_minute_coverage (feed, start_date, end_date, ticker);
        """
    )


def _prepare_minute_bars(bars: pd.DataFrame, feed: str) -> pd.DataFrame:
    required = {
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
    }
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"Missing provider columns: {', '.join(missing)}")
    incoming = bars.copy()
    incoming["ticker"] = incoming["ticker"].astype(str).str.strip().str.upper()
    incoming["timestamp"] = pd.to_datetime(incoming["timestamp"], errors="coerce", utc=True)
    for column in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        incoming[column] = pd.to_numeric(incoming[column], errors="coerce")
    required_values = ["ticker", "timestamp", "open", "high", "low", "close", "volume", "feed"]
    if incoming[required_values].isna().any().any():
        raise ValueError("Provider bars contain missing or invalid required values")
    if not incoming.empty and set(incoming["feed"].astype(str)) != {feed}:
        raise ValueError("Provider bars do not match the requested feed")
    if (incoming[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Provider bars contain non-positive prices")
    if (incoming["volume"] < 0).any():
        raise ValueError("Provider bars contain negative volume")
    if (incoming["high"] < incoming[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("Provider bars contain an inconsistent high price")
    if (incoming["low"] > incoming[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("Provider bars contain an inconsistent low price")
    return (
        incoming.sort_values(["ticker", "timestamp"])
        .drop_duplicates(["feed", "ticker", "timestamp"], keep="last")
        .reset_index(drop=True)
    )


def _merge_minute_job(
    connection: duckdb.DuckDBPyConnection,
    bars: pd.DataFrame,
    requested_symbols: list[str],
    *,
    feed: str,
    start: date,
    end: date,
) -> tuple[int, int]:
    incoming = _prepare_minute_bars(bars, feed)
    counts = incoming.groupby("ticker").size().to_dict() if not incoming.empty else {}
    connection.execute("BEGIN TRANSACTION")
    registered = False
    try:
        if not incoming.empty:
            connection.register("_minute_batch", incoming)
            registered = True
            connection.execute(
                """
                DELETE FROM provider_minute_bars
                USING _minute_batch
                WHERE provider_minute_bars.feed = _minute_batch.feed
                  AND provider_minute_bars.ticker = _minute_batch.ticker
                  AND provider_minute_bars.timestamp = _minute_batch.timestamp
                """
            )
            connection.execute(
                """
                INSERT INTO provider_minute_bars
                    (ticker, timestamp, open, high, low, close, volume, trade_count, vwap, feed)
                SELECT ticker, timestamp, open, high, low, close, volume, trade_count, vwap, feed
                FROM _minute_batch
                """
            )
        for ticker in requested_symbols:
            connection.execute(
                """
                DELETE FROM provider_minute_coverage
                WHERE ticker = ? AND feed = ? AND start_date = ? AND end_date = ?
                """,
                [ticker, feed, start, end],
            )
            connection.execute(
                """
                INSERT INTO provider_minute_coverage
                    (ticker, feed, start_date, end_date, row_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                [ticker, feed, start, end, int(counts.get(ticker, 0))],
            )
        connection.execute(
            """
            INSERT INTO provider_minute_update_log
                (feed, start_date, end_date, symbols_requested, symbols_with_bars, rows_merged)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [feed, start, end, len(requested_symbols), len(counts), len(incoming)],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        if registered:
            connection.unregister("_minute_batch")
    return len(incoming), len(counts)


def backfill_minute_bars(
    database: str | Path,
    symbols: list[str],
    client: AlpacaMarketDataClient,
    *,
    start: str | date,
    end: str | date,
    feed: str = "iex",
    batch_size: int = 50,
    chunk_days: int = 7,
    progress: Callable[[MinuteBackfillProgress], None] | None = None,
) -> MinuteBackfillResult:
    """Backfill one-minute bars in restartable symbol/date jobs.

    Each successful job commits its bars and coverage record in one transaction.
    Repeating the command with the same dates, chunk size, and feed skips completed
    jobs, including symbols that legitimately returned no bars.
    """
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    normalized = _normalize_symbols(symbols)
    start_date = _as_date(start, "Start date")
    end_date = _as_date(end, "End date")
    if start_date > end_date:
        raise ValueError("Start date must not be after end date")
    if feed not in {"iex", "sip", "otc"}:
        raise ValueError("Feed must be iex, sip, or otc")
    if batch_size < 1 or batch_size > 200:
        raise ValueError("Batch size must be between 1 and 200")
    if chunk_days < 1 or chunk_days > 31:
        raise ValueError("Chunk days must be between 1 and 31")

    chunks = list(_date_chunks(start_date, end_date, chunk_days))
    batches = [
        normalized[offset : offset + batch_size] for offset in range(0, len(normalized), batch_size)
    ]
    total_jobs = len(chunks) * len(batches)
    jobs_completed = 0
    jobs_skipped = 0
    rows_merged = 0
    updated_symbols: set[str] = set()
    position = 0

    connection = duckdb.connect(str(path))
    connection.execute("SET TimeZone='UTC'")
    _create_intraday_schema(connection)
    try:
        for chunk_start, chunk_end in chunks:
            covered = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT ticker FROM provider_minute_coverage
                    WHERE feed = ? AND start_date = ? AND end_date = ?
                    """,
                    [feed, chunk_start, chunk_end],
                ).fetchall()
            }
            for batch in batches:
                position += 1
                missing = [ticker for ticker in batch if ticker not in covered]
                if not missing:
                    jobs_skipped += 1
                    if progress:
                        progress(
                            MinuteBackfillProgress(
                                position,
                                total_jobs,
                                chunk_start,
                                chunk_end,
                                len(batch),
                                0,
                                True,
                            )
                        )
                    continue
                api_start, api_end = _api_bounds(chunk_start, chunk_end)
                bars = client.fetch_minute_bars(
                    missing,
                    start=api_start,
                    end=api_end,
                    feed=feed,
                    batch_size=len(missing),
                )
                merged, _ = _merge_minute_job(
                    connection,
                    bars,
                    missing,
                    feed=feed,
                    start=chunk_start,
                    end=chunk_end,
                )
                jobs_completed += 1
                rows_merged += merged
                if not bars.empty:
                    updated_symbols.update(bars["ticker"].astype(str).str.upper())
                if progress:
                    progress(
                        MinuteBackfillProgress(
                            position,
                            total_jobs,
                            chunk_start,
                            chunk_end,
                            len(missing),
                            merged,
                            False,
                        )
                    )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return MinuteBackfillResult(
        jobs_completed=jobs_completed,
        jobs_skipped=jobs_skipped,
        rows_merged=rows_merged,
        symbols_updated=len(updated_symbols),
    )


def minute_archive_statistics(database: str | Path) -> dict[str, object]:
    """Return counts and coverage for the one-minute provider archive."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        exists = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name = 'provider_minute_bars'
            """
        ).fetchone()[0]
        if not exists:
            return {
                "rows": 0,
                "symbols": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "coverage_records": 0,
                "database_bytes": path.stat().st_size,
            }
        row = connection.execute(
            """
            SELECT count(*), count(DISTINCT ticker), min(timestamp), max(timestamp)
            FROM provider_minute_bars
            """
        ).fetchone()
        coverage_records = connection.execute(
            "SELECT count(*) FROM provider_minute_coverage"
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "rows": int(row[0]),
        "symbols": int(row[1]),
        "first_timestamp": row[2],
        "last_timestamp": row[3],
        "coverage_records": int(coverage_records),
        "database_bytes": path.stat().st_size,
    }
