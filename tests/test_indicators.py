import unittest

import numpy as np
import pandas as pd

from market_signal_lab.demo import generate_demo_prices
from market_signal_lab.indicators import (
    add_indicators,
    average_true_range,
    bollinger_bands,
    on_balance_volume,
    relative_strength_index,
)


class IndicatorTests(unittest.TestCase):
    def test_bollinger_middle_matches_rolling_average(self):
        close = pd.Series(range(1, 31), dtype="float64")
        bands = bollinger_bands(close, window=20)
        self.assertAlmostEqual(bands.iloc[-1]["bollinger_mid"], 20.5)
        self.assertGreater(bands.iloc[-1]["bollinger_upper"], bands.iloc[-1]["bollinger_mid"])

    def test_rsi_reaches_100_for_consistent_gains(self):
        close = pd.Series(np.arange(1, 40), dtype="float64")
        self.assertEqual(relative_strength_index(close).iloc[-1], 100.0)

    def test_atr_uses_gap_from_previous_close(self):
        prices = pd.DataFrame(
            {
                "High": [11.0, 15.0],
                "Low": [9.0, 13.0],
                "Close": [10.0, 14.0],
            }
        )
        atr = average_true_range(prices, period=1)
        self.assertEqual(atr.iloc[-1], 5.0)

    def test_obv_tracks_direction(self):
        close = pd.Series([10.0, 11.0, 10.0, 10.0])
        volume = pd.Series([100, 200, 50, 400])
        self.assertEqual(on_balance_volume(close, volume).tolist(), [0.0, 200.0, 150.0, 150.0])

    def test_complete_indicator_set_has_expected_columns(self):
        result = add_indicators(generate_demo_prices(100))
        expected = {"rsi_14", "macd_histogram", "atr_14", "stochastic_k", "obv", "cmf_20"}
        self.assertTrue(expected.issubset(result.columns))
        self.assertTrue(result.iloc[-1][list(expected)].notna().all())


if __name__ == "__main__":
    unittest.main()
