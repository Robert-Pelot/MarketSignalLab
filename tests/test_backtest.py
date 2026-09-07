import unittest

import pandas as pd

from market_signal_lab.backtest import run_backtest


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.analysis = pd.DataFrame(
            {
                "Date": pd.date_range("2026-01-01", periods=5, tz="UTC"),
                "Open": [10.0, 11.0, 12.0, 13.0, 14.0],
                "Close": [10.5, 11.5, 12.5, 13.5, 14.5],
                "signal": ["Buy", "Hold", "Sell", "Hold", "Hold"],
            }
        )

    def test_signal_executes_at_next_bar_open(self):
        result = run_backtest(self.analysis, initial_cash=100.0, transaction_cost_bps=0)
        self.assertEqual(result.trades.iloc[0]["Date"], self.analysis.iloc[1]["Date"])
        self.assertEqual(result.trades.iloc[0]["Price"], 11.0)
        self.assertEqual(result.trades.iloc[1]["Date"], self.analysis.iloc[3]["Date"])

    def test_transaction_cost_reduces_equity(self):
        free = run_backtest(self.analysis, initial_cash=100.0, transaction_cost_bps=0)
        paid = run_backtest(self.analysis, initial_cash=100.0, transaction_cost_bps=25)
        self.assertLess(paid.metrics["final_equity"], free.metrics["final_equity"])

    def test_invalid_starting_cash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_backtest(self.analysis, initial_cash=0)


if __name__ == "__main__":
    unittest.main()
