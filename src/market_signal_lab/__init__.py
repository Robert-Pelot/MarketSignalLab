"""Educational market analysis and backtesting tools."""

from market_signal_lab.analysis import analyze_prices
from market_signal_lab.archive import build_archive, load_daily_prices
from market_signal_lab.backtest import BacktestResult, run_backtest
from market_signal_lab.demo import generate_demo_prices

__all__ = [
    "BacktestResult",
    "analyze_prices",
    "build_archive",
    "generate_demo_prices",
    "load_daily_prices",
    "run_backtest",
]
__version__ = "1.3.0"
