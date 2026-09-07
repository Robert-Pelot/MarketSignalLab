"""A small long-only backtester with next-bar execution."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    """Backtest outputs and summary metrics."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float | int]


def run_backtest(
    analysis: pd.DataFrame,
    initial_cash: float = 10_000.0,
    transaction_cost_bps: float = 5.0,
) -> BacktestResult:
    """Run a long-only simulation using the prior bar's signal at the next open.

    The strategy moves fully between cash and a fractional-share long position.
    This intentionally simple model does not represent a production trading system.
    """
    required = {"Date", "Open", "Close", "signal"}
    missing = sorted(required - set(analysis.columns))
    if missing:
        raise ValueError(f"Missing required backtest columns: {', '.join(missing)}")
    if analysis.empty:
        raise ValueError("Analysis data is empty")
    if initial_cash <= 0:
        raise ValueError("Initial cash must be greater than zero")
    if transaction_cost_bps < 0:
        raise ValueError("Transaction cost cannot be negative")

    data = analysis.sort_values("Date").reset_index(drop=True)
    cash = float(initial_cash)
    shares = 0.0
    cost_rate = transaction_cost_bps / 10_000
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for index, row in data.iterrows():
        prior_signal = "Hold" if index == 0 else str(data.iloc[index - 1]["signal"])
        open_price = float(row["Open"])

        if prior_signal == "Buy" and cash > 0:
            shares = cash / (open_price * (1 + cost_rate))
            gross_value = shares * open_price
            fee = gross_value * cost_rate
            cash = 0.0
            trade_rows.append(
                {
                    "Date": row["Date"],
                    "Action": "Buy",
                    "Price": open_price,
                    "Shares": shares,
                    "Fee": fee,
                }
            )
        elif prior_signal == "Sell" and shares > 0:
            gross_value = shares * open_price
            fee = gross_value * cost_rate
            cash = gross_value - fee
            trade_rows.append(
                {
                    "Date": row["Date"],
                    "Action": "Sell",
                    "Price": open_price,
                    "Shares": shares,
                    "Fee": fee,
                }
            )
            shares = 0.0

        close_price = float(row["Close"])
        equity_rows.append(
            {
                "Date": row["Date"],
                "Cash": cash,
                "Shares": shares,
                "Equity": cash + shares * close_price,
            }
        )

    equity_curve = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(trade_rows, columns=["Date", "Action", "Price", "Shares", "Fee"])
    final_equity = float(equity_curve.iloc[-1]["Equity"])
    benchmark_return = (float(data.iloc[-1]["Close"]) / float(data.iloc[0]["Open"])) - 1
    metrics: dict[str, float | int] = {
        "initial_cash": float(initial_cash),
        "final_equity": final_equity,
        "total_return_pct": 100 * (final_equity / initial_cash - 1),
        "buy_and_hold_return_pct": 100 * benchmark_return,
        "trade_count": len(trades),
        "transaction_cost_bps": float(transaction_cost_bps),
    }
    return BacktestResult(equity_curve=equity_curve, trades=trades, metrics=metrics)
