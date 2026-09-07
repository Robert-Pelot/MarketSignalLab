"""Import and query the legacy multi-ticker market-data archive."""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import duckdb
import pandas as pd

CURRENT_DATA_PATTERN = re.compile(r"^CurrentData\((?P<ticker>[A-Z0-9.\-^=]+)\)\.csv$")
RAW_COLUMNS = ("DateTime", "Open", "High", "Low", "Close", "Adj Close", "Volume")


@dataclass(frozen=True)
class ArchiveInspection:
    """Basic facts about a directory or ZIP containing legacy CSV files."""

    source: Path
    price_file_count: int
    compressed_bytes: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class ArchiveBuildResult:
    """Summary of a completed archive build."""

    database: Path
    files_imported: int
    rows_imported: int
    rows_rejected: int
    symbols: int
    daily_rows: int


@dataclass(frozen=True)
class _CsvSource:
    display_name: str
    ticker: str
    opener: Callable[[], BinaryIO]


def _ticker_from_name(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    match = CURRENT_DATA_PATTERN.fullmatch(PurePosixPath(normalized).name)
    return match.group("ticker") if match else None


def inspect_legacy_archive(source: str | Path) -> ArchiveInspection:
    """Count usable price files without extracting or importing their contents."""
    path = Path(source)
    if path.is_dir():
        files = [item for item in path.rglob("*.csv") if _ticker_from_name(item.name)]
        size = sum(item.stat().st_size for item in files)
        return ArchiveInspection(path, len(files), size, size)
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = [item for item in archive.infolist() if _ticker_from_name(item.filename)]
            return ArchiveInspection(
                path,
                len(entries),
                path.stat().st_size,
                sum(item.file_size for item in entries),
            )
    raise ValueError("Archive source must be a directory or ZIP file")


def _directory_sources(path: Path) -> Iterator[_CsvSource]:
    for file_path in sorted(path.rglob("*.csv")):
        ticker = _ticker_from_name(file_path.name)
        if ticker:
            yield _CsvSource(str(file_path), ticker, lambda item=file_path: item.open("rb"))


def _zip_sources(path: Path, archive: zipfile.ZipFile) -> Iterator[_CsvSource]:
    for entry in sorted(archive.infolist(), key=lambda item: item.filename):
        ticker = _ticker_from_name(entry.filename)
        if ticker:
            yield _CsvSource(entry.filename, ticker, lambda item=entry: archive.open(item, "r"))


def _prepare_chunk(
    chunk: pd.DataFrame, ticker: str, source_file: str
) -> tuple[pd.DataFrame, Counter[str]]:
    chunk = chunk.rename(columns={"DateTime": "timestamp", "Adj Close": "adjusted_close"})
    required = ("timestamp", "Open", "High", "Low", "Close", "Volume")
    missing = [column for column in required if column not in chunk.columns]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], errors="coerce", utc=True)
    for column in ("Open", "High", "Low", "Close", "adjusted_close", "Volume"):
        if column in chunk:
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
    if "adjusted_close" not in chunk:
        chunk["adjusted_close"] = chunk["Close"]

    reason = pd.Series("", index=chunk.index, dtype="object")
    missing_values = ~chunk[list(required)].notna().all(axis=1)
    reason.loc[missing_values] = "missing_or_invalid_value"
    non_positive = (chunk[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
    reason.loc[(reason == "") & non_positive] = "non_positive_price"
    negative_volume = chunk["Volume"] < 0
    reason.loc[(reason == "") & negative_volume] = "negative_volume"
    invalid_high = chunk["High"] < chunk[["Open", "Low", "Close"]].max(axis=1)
    reason.loc[(reason == "") & invalid_high] = "inconsistent_high"
    invalid_low = chunk["Low"] > chunk[["Open", "High", "Close"]].min(axis=1)
    reason.loc[(reason == "") & invalid_low] = "inconsistent_low"
    valid = reason == ""
    rejected = Counter(reason.loc[~valid].value_counts().to_dict())
    chunk = chunk.loc[valid].copy()
    chunk.insert(0, "ticker", ticker)
    chunk["source_file"] = source_file
    chunk = chunk.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    return chunk[
        [
            "ticker",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
            "source_file",
        ]
    ], rejected


def _create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE raw_prices (
            ticker VARCHAR NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            open DOUBLE NOT NULL,
            high DOUBLE NOT NULL,
            low DOUBLE NOT NULL,
            close DOUBLE NOT NULL,
            adjusted_close DOUBLE,
            volume DOUBLE NOT NULL,
            source_file VARCHAR NOT NULL
        );
        CREATE TABLE import_log (
            source_file VARCHAR NOT NULL,
            ticker VARCHAR NOT NULL,
            imported_rows BIGINT NOT NULL,
            rejected_rows BIGINT NOT NULL,
            error VARCHAR
        );
        CREATE TABLE import_rejections (
            source_file VARCHAR NOT NULL,
            ticker VARCHAR NOT NULL,
            reason VARCHAR NOT NULL,
            rejected_rows BIGINT NOT NULL
        );
        """
    )


def _finish_archive(connection: duckdb.DuckDBPyConnection) -> tuple[int, int, int]:
    connection.execute(
        """
        CREATE TABLE prices AS
        SELECT ticker, timestamp, open, high, low, close, adjusted_close, volume, source_file
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY ticker, timestamp ORDER BY source_file
            ) AS occurrence
            FROM raw_prices
        )
        WHERE occurrence = 1;

        DROP TABLE raw_prices;

        CREATE TABLE daily_prices AS
        SELECT
            ticker,
            CAST(timezone('America/New_York', timestamp) AS DATE) AS trading_date,
            first(open ORDER BY timestamp) AS open,
            max(high) AS high,
            min(low) AS low,
            last(close ORDER BY timestamp) AS close,
            last(adjusted_close ORDER BY timestamp) AS adjusted_close,
            sum(volume) AS volume,
            count(*) AS source_rows
        FROM prices
        GROUP BY ticker, trading_date
        ORDER BY ticker, trading_date;

        CREATE TABLE symbol_summary AS
        SELECT
            ticker,
            min(trading_date) AS first_date,
            max(trading_date) AS last_date,
            count(*) AS trading_days,
            sum(source_rows) AS source_rows
        FROM daily_prices
        GROUP BY ticker
        ORDER BY ticker;

        """
    )
    symbols, daily_rows, price_rows = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM symbol_summary),
            (SELECT count(*) FROM daily_prices),
            (SELECT count(*) FROM prices)
        """
    ).fetchone()
    connection.execute("CHECKPOINT")
    return int(symbols), int(daily_rows), int(price_rows)


def build_archive(
    source: str | Path,
    database: str | Path,
    *,
    replace: bool = False,
    chunk_size: int = 100_000,
    progress: Callable[[int, int, str], None] | None = None,
) -> ArchiveBuildResult:
    """Build a queryable DuckDB archive from legacy per-ticker CSV files.

    Both a directory and the original ZIP are supported. The importer streams one
    CSV at a time, validates rows, retains the original mixed-granularity records,
    and creates a separate daily table for consistent indicator calculations.
    """
    source_path = Path(source)
    destination = Path(database)
    staging = destination.with_name(f"{destination.name}.building")
    inspection = inspect_legacy_archive(source_path)
    if inspection.price_file_count == 0:
        raise ValueError("No CurrentData(TICKER).csv files were found")
    if destination.exists() and not replace:
        raise FileExistsError(f"Database already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        staging.unlink()
    staging_wal = Path(f"{staging}.wal")
    if staging_wal.exists():
        staging_wal.unlink()

    connection = duckdb.connect(str(staging))
    connection.execute("SET TimeZone='UTC'")
    _create_schema(connection)
    files_imported = 0
    rows_imported = 0
    rows_rejected = 0

    archive: zipfile.ZipFile | None = None
    try:
        if source_path.is_dir():
            sources = _directory_sources(source_path)
        else:
            archive = zipfile.ZipFile(source_path)
            sources = _zip_sources(source_path, archive)

        for position, csv_source in enumerate(sources, start=1):
            file_rows = 0
            file_rejected = 0
            file_rejections: Counter[str] = Counter()
            error: str | None = None
            try:
                with csv_source.opener() as stream:
                    reader = pd.read_csv(
                        stream,
                        usecols=lambda column: column in RAW_COLUMNS,
                        chunksize=chunk_size,
                        low_memory=False,
                    )
                    for raw_chunk in reader:
                        prepared, rejected = _prepare_chunk(
                            raw_chunk, csv_source.ticker, csv_source.display_name
                        )
                        file_rejections.update(rejected)
                        file_rejected += sum(rejected.values())
                        if not prepared.empty:
                            connection.register("_import_batch", prepared)
                            connection.execute("INSERT INTO raw_prices SELECT * FROM _import_batch")
                            connection.unregister("_import_batch")
                            file_rows += len(prepared)
                files_imported += 1
                rows_imported += file_rows
                rows_rejected += file_rejected
            except Exception as exception:  # preserve other tickers and record the failure
                error = f"{type(exception).__name__}: {exception}"
            connection.execute(
                "INSERT INTO import_log VALUES (?, ?, ?, ?, ?)",
                [csv_source.display_name, csv_source.ticker, file_rows, file_rejected, error],
            )
            for reason, rejected_count in sorted(file_rejections.items()):
                connection.execute(
                    "INSERT INTO import_rejections VALUES (?, ?, ?, ?)",
                    [csv_source.display_name, csv_source.ticker, reason, rejected_count],
                )
            if progress:
                progress(position, inspection.price_file_count, csv_source.ticker)

        symbols, daily_rows, deduplicated_rows = _finish_archive(connection)
        connection.close()
        connection = None

        # A successful build must be independently reopenable before it replaces
        # an existing archive. This also detects an incomplete write-ahead log.
        verification = duckdb.connect(str(staging), read_only=True)
        try:
            verified_rows = int(verification.execute("SELECT count(*) FROM prices").fetchone()[0])
        finally:
            verification.close()
        if verified_rows != deduplicated_rows:
            raise RuntimeError("Archive verification row count did not match the completed build")
        staging.replace(destination)

        result = ArchiveBuildResult(
            database=destination,
            files_imported=files_imported,
            rows_imported=deduplicated_rows,
            rows_rejected=rows_rejected,
            symbols=symbols,
            daily_rows=daily_rows,
        )
        return result
    except Exception:
        if connection is not None:
            connection.close()
        if staging.exists():
            staging.unlink()
        if staging_wal.exists():
            staging_wal.unlink()
        raise
    finally:
        if archive:
            archive.close()
        if connection is not None:
            connection.close()


def archive_statistics(database: str | Path) -> dict[str, object]:
    """Return the principal archive counts and date range."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        row = connection.execute(
            """
            SELECT
                count(DISTINCT ticker), count(*), min(trading_date), max(trading_date),
                (SELECT count(*) FROM prices)
            FROM daily_prices
            """
        ).fetchone()
    finally:
        connection.close()
    return {
        "symbols": int(row[0]),
        "daily_rows": int(row[1]),
        "first_date": row[2],
        "last_date": row[3],
        "raw_rows": int(row[4]),
        "database_bytes": path.stat().st_size,
    }


def list_archive_symbols(database: str | Path) -> list[str]:
    """Return all symbols that currently have usable daily prices."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return [
            row[0] for row in connection.execute("SELECT ticker FROM symbol_summary").fetchall()
        ]
    finally:
        connection.close()


def next_update_start(database: str | Path, symbols: list[str]) -> str:
    """Return the day after the oldest last date among selected archive symbols.

    Using the least-current selected symbol prevents a recent partial update from
    causing older symbols to be skipped during a later batch update.
    """
    selected = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
    if not selected:
        raise ValueError("At least one ticker symbol is required")
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        latest_dates = {
            ticker: last_date
            for ticker, last_date in connection.execute(
                "SELECT ticker, last_date FROM symbol_summary"
            ).fetchall()
            if ticker in selected
        }
    finally:
        connection.close()
    if len(latest_dates) != len(selected):
        missing = sorted(selected - set(latest_dates))
        raise ValueError(
            "Provide --start when adding symbols not already in the archive: " + ", ".join(missing)
        )
    return (min(latest_dates.values()) + timedelta(days=1)).isoformat()


def merge_provider_daily_bars(database: str | Path, bars: pd.DataFrame) -> dict[str, int]:
    """Merge validated provider daily bars into an existing archive.

    The original legacy tables remain intact. Provider rows are retained in their
    own table and replace the same symbol/date in the curated daily table.
    """
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
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
    if bars.empty:
        return {"rows_merged": 0, "symbols_updated": 0}

    incoming = bars.copy()
    incoming["ticker"] = incoming["ticker"].astype(str).str.strip().str.upper()
    incoming["timestamp"] = pd.to_datetime(incoming["timestamp"], errors="coerce", utc=True)
    for column in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        incoming[column] = pd.to_numeric(incoming[column], errors="coerce")
    required_values = ["ticker", "timestamp", "open", "high", "low", "close", "volume", "feed"]
    if incoming[required_values].isna().any().any():
        raise ValueError("Provider bars contain missing or invalid required values")
    if (incoming[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Provider bars contain non-positive prices")
    if (incoming["volume"] < 0).any():
        raise ValueError("Provider bars contain negative volume")
    if (incoming["high"] < incoming[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("Provider bars contain an inconsistent high price")
    if (incoming["low"] > incoming[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("Provider bars contain an inconsistent low price")
    incoming = incoming.sort_values(["ticker", "timestamp"]).drop_duplicates(
        ["ticker", "timestamp"], keep="last"
    )

    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_daily_bars (
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
            CREATE TABLE IF NOT EXISTS provider_update_log (
                updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
                feed VARCHAR NOT NULL,
                first_timestamp TIMESTAMPTZ NOT NULL,
                last_timestamp TIMESTAMPTZ NOT NULL,
                rows_merged BIGINT NOT NULL,
                symbols_updated BIGINT NOT NULL
            );
            """
        )
        connection.register("_provider_batch", incoming)
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            DELETE FROM provider_daily_bars
            USING _provider_batch
            WHERE provider_daily_bars.ticker = _provider_batch.ticker
              AND provider_daily_bars.timestamp = _provider_batch.timestamp
            """
        )
        connection.execute(
            """
            INSERT INTO provider_daily_bars
                (ticker, timestamp, open, high, low, close, volume, trade_count, vwap, feed)
            SELECT ticker, timestamp, open, high, low, close, volume, trade_count, vwap, feed
            FROM _provider_batch
            """
        )
        connection.execute(
            """
            DELETE FROM daily_prices
            USING _provider_batch
            WHERE daily_prices.ticker = _provider_batch.ticker
              AND daily_prices.trading_date =
                  CAST(timezone('America/New_York', _provider_batch.timestamp) AS DATE)
            """
        )
        connection.execute(
            """
            INSERT INTO daily_prices
                (ticker, trading_date, open, high, low, close, adjusted_close, volume, source_rows)
            SELECT
                ticker,
                CAST(timezone('America/New_York', timestamp) AS DATE),
                open, high, low, close, close, volume, 1
            FROM _provider_batch
            """
        )
        connection.execute(
            """
            DELETE FROM symbol_summary
            WHERE ticker IN (SELECT DISTINCT ticker FROM _provider_batch);
            INSERT INTO symbol_summary
            SELECT
                ticker, min(trading_date), max(trading_date), count(*), sum(source_rows)
            FROM daily_prices
            WHERE ticker IN (SELECT DISTINCT ticker FROM _provider_batch)
            GROUP BY ticker;
            """
        )
        rows_merged = len(incoming)
        symbols_updated = int(incoming["ticker"].nunique())
        connection.execute(
            """
            INSERT INTO provider_update_log
                (feed, first_timestamp, last_timestamp, rows_merged, symbols_updated)
            SELECT feed, min(timestamp), max(timestamp), ?, ?
            FROM _provider_batch
            GROUP BY feed
            """,
            [rows_merged, symbols_updated],
        )
        connection.execute("COMMIT")
        connection.unregister("_provider_batch")
        connection.execute("CHECKPOINT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        connection.close()
    return {"rows_merged": rows_merged, "symbols_updated": symbols_updated}


def load_daily_prices(
    database: str | Path,
    ticker: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load a ticker's daily bars from the archive in analysis-ready form."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    normalized_ticker = ticker.strip().upper()
    if not CURRENT_DATA_PATTERN.fullmatch(f"CurrentData({normalized_ticker}).csv"):
        raise ValueError("Ticker contains unsupported characters")

    predicates = ["ticker = ?"]
    parameters: list[str] = [normalized_ticker]
    if start:
        predicates.append("trading_date >= CAST(? AS DATE)")
        parameters.append(start)
    if end:
        predicates.append("trading_date <= CAST(? AS DATE)")
        parameters.append(end)
    query = f"""
        SELECT trading_date AS Date, open AS Open, high AS High, low AS Low,
               close AS Close, volume AS Volume
        FROM daily_prices
        WHERE {" AND ".join(predicates)}
        ORDER BY trading_date
    """
    connection = duckdb.connect(str(path), read_only=True)
    try:
        frame = connection.execute(query, parameters).fetchdf()
    finally:
        connection.close()
    if frame.empty:
        raise ValueError(f"No daily prices found for ticker {normalized_ticker}")
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
    return frame
