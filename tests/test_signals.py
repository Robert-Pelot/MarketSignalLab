import unittest

from market_signal_lab.analysis import analyze_prices
from market_signal_lab.demo import generate_demo_prices


class SignalTests(unittest.TestCase):
    def test_warmup_rows_are_hold(self):
        result = analyze_prices(generate_demo_prices(100))
        self.assertTrue((result.iloc[:49]["signal"] == "Hold").all())

    def test_signals_and_component_scores_are_explainable(self):
        result = analyze_prices(generate_demo_prices(160))
        self.assertTrue(set(result["signal"]).issubset({"Buy", "Hold", "Sell"}))
        components = [column for column in result if column.startswith("score_")]
        self.assertEqual(
            result.iloc[-1][components].sum(),
            result.iloc[-1]["signal_score"],
        )

    def test_analysis_is_repeatable(self):
        first = analyze_prices(generate_demo_prices(120, seed=7))
        second = analyze_prices(generate_demo_prices(120, seed=7))
        self.assertEqual(first.iloc[-1]["signal"], second.iloc[-1]["signal"])
        self.assertEqual(first.iloc[-1]["signal_score"], second.iloc[-1]["signal_score"])


if __name__ == "__main__":
    unittest.main()
