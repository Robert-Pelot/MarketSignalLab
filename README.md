# MarketSignalLab

[![CI](https://github.com/Robert-Pelot/MarketSignalLab/actions/workflows/ci.yml/badge.svg)](https://github.com/Robert-Pelot/MarketSignalLab/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

MarketSignalLab is an educational Python toolkit for preserving a historical
market-data archive, calculating technical indicators, producing explainable
Buy/Hold/Sell signals, and evaluating those signals with a next-bar backtest.

The project began as a collection of stock-analysis experiments during my Python
coursework. I later rebuilt the useful ideas as a tested, reproducible portfolio
project with a command-line interface and an offline demonstration. It is designed
to demonstrate Python, pandas, data validation, test automation, and responsible
backtesting practices—not to operate a brokerage account.

> **Important:** This software is for education and software demonstration only.
> It is not financial advice, and its output should not be used as the sole basis
> for an investment decision.

## What it demonstrates

- Validated and normalized OHLCV CSV input
- Streaming migration of thousands of legacy per-ticker files into DuckDB
- Full-resolution raw storage plus consistent daily bars for analysis
- Bollinger Bands with normalized percent-B
- Relative Strength Index using Wilder smoothing
- MACD line, signal line, and histogram
- Average True Range
- Stochastic oscillator
- On-Balance Volume and Chaikin Money Flow
- Component-level scoring that explains each combined signal
- Long-only backtesting with next-bar execution and configurable costs
- Deterministic synthetic data for an entirely offline demonstration
- Automated tests and GitHub Actions on Python 3.11 and 3.12

## Quick start

Python 3.11 or newer is required.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m market_signal_lab demo
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m market_signal_lab demo
```

The demonstration uses repeatable synthetic daily prices, requires no network
connection, and does not require an API key.

To save the complete calculated dataset:

```bash
python -m market_signal_lab demo --output reports/demo-analysis.csv
```

## Analyze a CSV file

Input files must contain at least 60 rows and these columns:

| Column | Meaning |
| --- | --- |
| `Date` | Date or timestamp |
| `Open` | Opening price |
| `High` | Highest price |
| `Low` | Lowest price |
| `Close` | Closing price |
| `Volume` | Traded volume |

`DateTime` or `Timestamp` may be used in place of `Date`. Column names are
matched without regard to capitalization.

```bash
python -m market_signal_lab analyze path/to/prices.csv \
  --output reports/analysis.csv \
  --initial-cash 10000 \
  --transaction-cost-bps 5
```

Downloaded market data and generated reports belong in `data/` and `reports/`;
both directories are intentionally excluded from Git.

## Build the historical archive

The importer accepts either the original ZIP or a directory containing files
named `CurrentData(TICKER).csv`. It streams one file at a time, so the complete
archive does not need to fit in memory.

Inspect the source first:

```powershell
python -m market_signal_lab archive-inspect "C:\path\to\Python-candidates-source.zip"
```

Build the database:

```powershell
python -m market_signal_lab archive-build `
  "C:\path\to\Python-candidates-source.zip" `
  "D:\MarketData\market-history.duckdb"
```

The database contains:

- `prices`: validated, deduplicated historical source rows
- `daily_prices`: daily OHLCV bars built from the legacy mixed-frequency data
- `symbol_summary`: date range and row counts for each ticker
- `import_log`: imported and rejected row counts plus file-level errors
- `import_rejections`: rejected-row counts grouped by validation reason

The legacy files combine one-minute, five-minute, and hourly observations but do
not identify the interval on each row. MarketSignalLab therefore preserves every
raw record while using daily resampling for indicator calculations. That avoids
treating unlike time intervals as though they formed one uniform series.

Review the completed archive and analyze a ticker:

```powershell
python -m market_signal_lab archive-info "D:\MarketData\market-history.duckdb"
python -m market_signal_lab archive-analyze `
  "D:\MarketData\market-history.duckdb" AAPL `
  --output "reports\AAPL-analysis.csv"
```

## Update the archive

MarketSignalLab can extend the daily archive using Alpaca's read-only Historical
Bars endpoint. The code does not import Alpaca's trading API and cannot submit an
order.

Set the regenerated credentials only in the current PowerShell session:

```powershell
$env:APCA_API_KEY_ID = "your-new-key-id"
$env:APCA_API_SECRET_KEY = "your-new-secret-key"
```

Test a few symbols first:

```powershell
python -m market_signal_lab archive-update `
  "D:\MarketData\market-history.duckdb" `
  --symbols AAPL,MSFT,NVDA `
  --feed iex
```

After confirming that works, update every symbol already present in the archive:

```powershell
python -m market_signal_lab archive-update `
  "D:\MarketData\market-history.duckdb" `
  --all-symbols `
  --feed iex
```

The starting date defaults to the day after the least-current selected symbol,
so a recent partial update cannot cause older symbols to be skipped. The ending
date defaults to yesterday to avoid saving a still-changing daily bar. Explicit
`--start` and `--end` dates can be used to repeat or repair a range safely;
matching ticker/date records are replaced.
API pagination is followed automatically, and transient server or rate-limit
responses are retried.

The default `iex` feed is appropriate for initial use without a paid market-data
subscription, but it represents only the IEX exchange and therefore does not
provide complete US market volume. See Alpaca's
[Historical Stock Data documentation](https://docs.alpaca.markets/docs/historical-stock-data-1)
for the feed differences and subscription requirements.

The DuckDB file is a generated local data asset and is excluded from GitHub.
Keep a backup on the NAS, but avoid opening one writable database concurrently
from multiple computers.

### Verified legacy snapshot

The migration workflow was tested against the complete August 2024 legacy
snapshot:

- 3,528 ticker files processed without file-import errors
- 26,677,199 validated source rows retained
- 3,526 symbols with usable prices
- 869,859 daily bars generated for consistent analysis
- 4,190 zero-price rows rejected and documented in the import tables
- approximately 1.02 GiB final database size

Two ticker files (`MRNJ` and `NEOM`) contained no positive prices and therefore
do not appear in the curated price tables. Generated financial reports from the
old prototype are intentionally excluded because the new application can
reproduce its own outputs from the preserved prices.

## Signal model

The combined score is intentionally transparent. Every row includes the
component columns used to reach the final result.

| Component | Positive evidence | Negative evidence |
| --- | --- | --- |
| Bollinger percent-B | Price near or below lower band | Price near or above upper band |
| RSI | Oversold range | Overbought range |
| MACD histogram | Positive momentum or bullish crossover | Negative momentum or bearish crossover |
| Stochastic %K | Oversold range | Overbought range |
| Chaikin Money Flow | Positive money flow | Negative money flow |
| 50-period trend | Close above moving average | Close below moving average |

A combined score of `+3` or higher becomes **Buy**, `-3` or lower becomes
**Sell**, and the remaining values become **Hold**. Warm-up rows are always Hold.

These thresholds are heuristics for demonstrating the software workflow; they
have not been presented as a proven trading strategy.

## Backtesting approach

The backtester reads a signal after its bar has closed and executes it at the
next bar's opening price. This avoids the original prototype's unrealistic
same-bar execution. It also deducts a configurable transaction cost and compares
the result with a basic buy-and-hold benchmark.

The implementation remains intentionally limited: it does not model bid/ask
spread, market impact, dividends, taxes, partial fills, short selling, or changing
liquidity. Past simulated performance does not predict future results.

## Project layout

```text
MarketSignalLab/
├── .github/workflows/ci.yml
├── src/market_signal_lab/
│   ├── analysis.py
│   ├── archive.py
│   ├── backtest.py
│   ├── cli.py
│   ├── demo.py
│   ├── indicators.py
│   ├── io.py
│   └── signals.py
├── tests/
├── CONTRIBUTING.md
├── LICENSE
├── SECURITY.md
└── pyproject.toml
```

## Tests

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

The tests cover archive migration from folders and ZIP files, indicator
calculations, signal warm-up and repeatability, input validation, next-bar
execution, transaction costs, CSV round trips, and the CLI.

## Security

This repository deliberately contains no brokerage integration and needs no
credentials. See [SECURITY.md](SECURITY.md) for the credential-handling policy.

## License

Released under the [MIT License](LICENSE).
