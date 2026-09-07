import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from market_signal_lab.archive import (
    archive_statistics,
    build_archive,
    inspect_legacy_archive,
    load_daily_prices,
    merge_provider_daily_bars,
    next_update_start,
)
from market_signal_lab.demo import generate_demo_prices
from market_signal_lab.io import save_csv


class ArchiveTests(unittest.TestCase):
    def _write_ticker(self, directory: Path, ticker: str, rows: int = 70) -> Path:
        frame = generate_demo_prices(rows, seed=len(ticker))
        frame = frame.rename(columns={"Date": "DateTime"})
        frame.insert(0, "Ticker", ticker)
        frame.insert(6, "Adj Close", frame["Close"])
        return save_csv(frame, directory / f"CurrentData({ticker}).csv")

    def test_inspects_and_builds_directory_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self._write_ticker(source, "AAA")
            self._write_ticker(source, "BBB")
            inspection = inspect_legacy_archive(source)
            self.assertEqual(inspection.price_file_count, 2)

            database = root / "market.duckdb"
            result = build_archive(source, database)
            self.assertEqual(result.symbols, 2)
            self.assertEqual(result.files_imported, 2)
            self.assertEqual(result.rows_rejected, 0)
            self.assertEqual(len(load_daily_prices(database, "aaa")), 70)
            self.assertEqual(archive_statistics(database)["symbols"], 2)

    def test_zip_source_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = self._write_ticker(root, "ZIP")
            zip_path = root / "prices.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(csv_path, f"StockTests/Data/{csv_path.name}")
            self.assertEqual(inspect_legacy_archive(zip_path).price_file_count, 1)
            result = build_archive(zip_path, root / "zip.duckdb")
            self.assertEqual(result.symbols, 1)

    def test_existing_database_is_not_overwritten_without_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self._write_ticker(source, "SAFE")
            database = root / "market.duckdb"
            database.write_text("keep me", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                build_archive(source, database)
            self.assertEqual(database.read_text(encoding="utf-8"), "keep me")

    def test_invalid_rows_are_counted_by_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            path = self._write_ticker(source, "ZERO")
            frame = generate_demo_prices(60).rename(columns={"Date": "DateTime"})
            frame.loc[0, "Low"] = 0
            frame.insert(0, "Ticker", "ZERO")
            frame.insert(6, "Adj Close", frame["Close"])
            save_csv(frame, path)
            result = build_archive(source, root / "market.duckdb")
            self.assertEqual(result.rows_rejected, 1)

    def test_successful_build_leaves_no_write_ahead_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self._write_ticker(source, "CLEAN")
            database = root / "market.duckdb"
            build_archive(source, database)
            self.assertFalse(Path(f"{database}.wal").exists())
            self.assertFalse(database.with_name(f"{database.name}.building").exists())

    def test_provider_daily_bars_extend_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self._write_ticker(source, "AAA")
            database = root / "market.duckdb"
            build_archive(source, database)
            bars = pd.DataFrame(
                [
                    {
                        "ticker": "AAA",
                        "timestamp": "2026-01-02T05:00:00Z",
                        "open": 150,
                        "high": 155,
                        "low": 149,
                        "close": 154,
                        "volume": 1000,
                        "trade_count": 100,
                        "vwap": 152,
                        "feed": "iex",
                    }
                ]
            )
            merged = merge_provider_daily_bars(database, bars)
            self.assertEqual(merged, {"rows_merged": 1, "symbols_updated": 1})
            result = load_daily_prices(database, "AAA")
            self.assertEqual(result.iloc[-1]["Close"], 154)
            self.assertEqual(str(result.iloc[-1]["Date"].date()), "2026-01-02")

    def test_update_start_uses_oldest_selected_symbol(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self._write_ticker(source, "AAA")
            self._write_ticker(source, "BBB")
            database = root / "market.duckdb"
            build_archive(source, database)
            original_start = next_update_start(database, ["AAA", "BBB"])
            bars = pd.DataFrame(
                [
                    {
                        "ticker": "AAA",
                        "timestamp": "2026-01-02T05:00:00Z",
                        "open": 150,
                        "high": 155,
                        "low": 149,
                        "close": 154,
                        "volume": 1000,
                        "trade_count": 100,
                        "vwap": 152,
                        "feed": "iex",
                    }
                ]
            )
            merge_provider_daily_bars(database, bars)
            self.assertEqual(next_update_start(database, ["AAA", "BBB"]), original_start)


if __name__ == "__main__":
    unittest.main()
