import tempfile
import unittest
from pathlib import Path

import pandas as pd

from market_signal_lab.archive import build_archive
from market_signal_lab.demo import generate_demo_prices
from market_signal_lab.intraday import backfill_minute_bars, minute_archive_statistics
from market_signal_lab.io import save_csv


class _FakeMinuteClient:
    def __init__(self):
        self.calls = []

    def fetch_minute_bars(self, symbols, *, start, end, feed, batch_size):
        self.calls.append((tuple(symbols), str(start), str(end), feed, batch_size))
        first_date = str(start)[:10]
        return pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "timestamp": f"{first_date}T14:30:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 1000,
                    "trade_count": 20,
                    "vwap": 100.5,
                    "feed": feed,
                }
                for ticker in symbols
            ]
        )


class _EmptyMinuteClient:
    def __init__(self):
        self.calls = 0

    def fetch_minute_bars(self, symbols, *, start, end, feed, batch_size):
        self.calls += 1
        return pd.DataFrame(
            columns=[
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
        )


class IntradayArchiveTests(unittest.TestCase):
    def _build_archive(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        for ticker in ("AAA", "BBB"):
            frame = generate_demo_prices(70, seed=len(ticker)).rename(columns={"Date": "DateTime"})
            frame.insert(0, "Ticker", ticker)
            frame.insert(6, "Adj Close", frame["Close"])
            save_csv(frame, source / f"CurrentData({ticker}).csv")
        database = root / "market.duckdb"
        build_archive(source, database)
        return database

    def test_backfill_commits_small_jobs_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = self._build_archive(Path(temporary))
            client = _FakeMinuteClient()
            result = backfill_minute_bars(
                database,
                ["BBB", "AAA"],
                client,
                start="2026-01-01",
                end="2026-01-14",
                feed="sip",
                batch_size=1,
                chunk_days=7,
            )
            self.assertEqual(result.jobs_completed, 4)
            self.assertEqual(result.jobs_skipped, 0)
            self.assertEqual(result.rows_merged, 4)
            self.assertEqual(result.symbols_updated, 2)
            self.assertEqual(len(client.calls), 4)

            repeated = backfill_minute_bars(
                database,
                ["AAA", "BBB"],
                client,
                start="2026-01-01",
                end="2026-01-14",
                feed="sip",
                batch_size=1,
                chunk_days=7,
            )
            self.assertEqual(repeated.jobs_completed, 0)
            self.assertEqual(repeated.jobs_skipped, 4)
            self.assertEqual(repeated.rows_merged, 0)
            self.assertEqual(len(client.calls), 4)

            statistics = minute_archive_statistics(database)
            self.assertEqual(statistics["rows"], 4)
            self.assertEqual(statistics["symbols"], 2)
            self.assertEqual(statistics["coverage_records"], 4)

    def test_empty_provider_result_is_recorded_and_skipped_later(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = self._build_archive(Path(temporary))
            client = _EmptyMinuteClient()
            first = backfill_minute_bars(
                database,
                ["AAA"],
                client,
                start="2026-01-01",
                end="2026-01-07",
            )
            second = backfill_minute_bars(
                database,
                ["AAA"],
                client,
                start="2026-01-01",
                end="2026-01-07",
            )
            self.assertEqual(first.jobs_completed, 1)
            self.assertEqual(first.rows_merged, 0)
            self.assertEqual(second.jobs_skipped, 1)
            self.assertEqual(client.calls, 1)
            self.assertEqual(minute_archive_statistics(database)["coverage_records"], 1)

    def test_backfill_rejects_reversed_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = self._build_archive(Path(temporary))
            with self.assertRaisesRegex(ValueError, "Start date"):
                backfill_minute_bars(
                    database,
                    ["AAA"],
                    _FakeMinuteClient(),
                    start="2026-02-01",
                    end="2026-01-01",
                )


if __name__ == "__main__":
    unittest.main()
