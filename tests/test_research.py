import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd

from market_signal_lab.research import (
    FEATURE_COLUMNS,
    build_research_dataset,
    evaluate_walk_forward,
    load_research_dataset,
)


class ResearchDatasetTests(unittest.TestCase):
    def _minute_database(self, root: Path) -> Path:
        database = root / "market.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute(
            """
            CREATE TABLE provider_minute_bars (
                ticker VARCHAR NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                trade_count DOUBLE,
                vwap DOUBLE,
                feed VARCHAR NOT NULL
            )
            """
        )
        rows = [
            ("AAA", "2026-01-05T14:29:00Z", 90, 91, 89, 90, 10, 1, 90, "sip"),
            ("AAA", "2026-01-05T14:30:00Z", 100, 101, 99, 100.5, 10, 1, 100, "sip"),
            ("AAA", "2026-01-05T14:31:00Z", 100.5, 102, 100, 101, 20, 2, 101, "sip"),
            ("AAA", "2026-01-05T14:44:00Z", 101, 103, 100, 102, 30, 3, 102, "sip"),
            ("AAA", "2026-01-05T14:45:00Z", 102, 104, 101, 103, 40, 4, 103, "sip"),
            ("AAA", "2026-01-05T21:00:00Z", 110, 111, 109, 110, 50, 5, 110, "sip"),
        ]
        connection.executemany(
            """
            INSERT INTO provider_minute_bars
                (ticker, timestamp, open, high, low, close, volume, trade_count, vwap, feed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.close()
        return database

    def test_builds_regular_session_bars_and_next_bar_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = self._minute_database(Path(temporary))
            result = build_research_dataset(database, interval_minutes=15, feed="sip")
            dataset = load_research_dataset(database, interval_minutes=15)

            self.assertEqual(result.bars, 2)
            self.assertEqual(result.symbols, 1)
            self.assertEqual(dataset.iloc[0]["timestamp"], pd.Timestamp("2026-01-05 09:30:00"))
            self.assertEqual(dataset.iloc[0]["open"], 100)
            self.assertEqual(dataset.iloc[0]["high"], 103)
            self.assertEqual(dataset.iloc[0]["low"], 99)
            self.assertEqual(dataset.iloc[0]["close"], 102)
            self.assertEqual(dataset.iloc[0]["volume"], 60)
            self.assertEqual(dataset.iloc[0]["source_minutes"], 3)
            self.assertAlmostEqual(dataset.iloc[0]["future_return"], 103 / 102 - 1)
            self.assertTrue(pd.isna(dataset.iloc[1]["future_return"]))

    def test_rejects_unsupported_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = self._minute_database(Path(temporary))
            with self.assertRaisesRegex(ValueError, "Interval"):
                build_research_dataset(database, interval_minutes=30)


class WalkForwardTests(unittest.TestCase):
    def _dataset(self, sessions: int = 60) -> pd.DataFrame:
        rows = []
        dates = pd.bdate_range("2026-01-01", periods=sessions)
        for session_index, session in enumerate(dates):
            for sample in range(6):
                direction = 1 if (session_index + sample) % 2 == 0 else -1
                row = {
                    "ticker": f"T{sample % 3}",
                    "timestamp": session.to_pydatetime() + timedelta(hours=10, minutes=sample * 15),
                    "session_date": session.date(),
                    "future_return": direction * 0.001,
                }
                for feature_index, feature in enumerate(FEATURE_COLUMNS):
                    row[feature] = direction * (1 + feature_index / 100)
                rows.append(row)
        return pd.DataFrame(rows)

    def test_walk_forward_fits_only_earlier_sessions(self):
        result = evaluate_walk_forward(
            self._dataset(),
            train_sessions=20,
            test_sessions=10,
            holdout_sessions=10,
            target_move_bps=5,
        )

        self.assertEqual(result.metrics["folds"], 3)
        self.assertEqual(result.metrics["test_rows"], 180)
        self.assertEqual(result.metrics["holdout_sessions"], 10)
        self.assertEqual(result.metrics["holdout_rows"], 60)
        self.assertGreater(result.metrics["accuracy"], 0.99)
        self.assertTrue((result.folds["train_end"] < result.folds["test_start"]).all())
        self.assertEqual(len(result.predictions), 180)

    def test_walk_forward_requires_enough_sessions(self):
        with self.assertRaisesRegex(ValueError, "sessions are required"):
            evaluate_walk_forward(
                self._dataset(sessions=25),
                train_sessions=20,
                test_sessions=10,
                holdout_sessions=0,
            )

    def test_walk_forward_rejects_invalid_confidence(self):
        with self.assertRaisesRegex(ValueError, "Confidence"):
            evaluate_walk_forward(self._dataset(), holdout_sessions=0, confidence=1.0)


if __name__ == "__main__":
    unittest.main()
