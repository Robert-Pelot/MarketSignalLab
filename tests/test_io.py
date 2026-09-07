import tempfile
import unittest
from pathlib import Path

from market_signal_lab.demo import generate_demo_prices
from market_signal_lab.io import load_prices, normalize_prices, save_csv


class InputOutputTests(unittest.TestCase):
    def test_lowercase_columns_are_normalized(self):
        frame = generate_demo_prices(60).rename(columns=str.lower)
        result = normalize_prices(frame)
        self.assertEqual(
            result.columns.tolist(), ["Date", "Open", "High", "Low", "Close", "Volume"]
        )

    def test_impossible_high_is_rejected(self):
        frame = generate_demo_prices(60)
        frame.loc[0, "High"] = frame.loc[0, "Low"]
        with self.assertRaisesRegex(ValueError, "High must"):
            normalize_prices(frame)

    def test_csv_round_trip(self):
        frame = generate_demo_prices(60)
        with tempfile.TemporaryDirectory() as directory:
            path = save_csv(frame, Path(directory) / "nested" / "prices.csv")
            result = load_prices(path)
        self.assertEqual(len(result), 60)
        self.assertEqual(result.iloc[-1]["Close"], frame.iloc[-1]["Close"])


if __name__ == "__main__":
    unittest.main()
