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
- Resumable one-minute SIP/IEX/OTC history with coverage checkpoints
- Regular-session 5-, 15-, and 60-minute research datasets
- Backward-looking normalized features and next-bar prediction targets
- Leakage-aware rolling walk-forward logistic-regression evaluation
- Baseline-versus-technical feature experiments with fold, symbol, and coefficient reports
- Bollinger Bands with normalized percent-B
- Relative Strength Index using Wilder smoothing
- MACD line, signal line, and histogram
- Average True Range
- Stochastic oscillator
- On-Balance Volume and Chaikin Money Flow
- Component-level scoring that explains each combined signal
- Long-only backtesting with next-bar execution and configurable costs
- Deterministic synthetic data for an entirely offline demonstration
- Automated tests and GitHub Actions on Python 3.11, 3.12, and 3.13

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

Set credentials only in the current PowerShell session. `Read-Host` prevents
their values from appearing on screen or in PowerShell command history:

```powershell
$KeySecure = Read-Host "Enter Alpaca API key ID" -AsSecureString
$SecretSecure = Read-Host "Enter Alpaca secret key" -AsSecureString
$env:APCA_API_KEY_ID = [Net.NetworkCredential]::new("", $KeySecure).Password
$env:APCA_API_SECRET_KEY = [Net.NetworkCredential]::new("", $SecretSecure).Password
Remove-Variable KeySecure, SecretSecure
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
[Historical Stock Data documentation](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
for the feed differences and subscription requirements.

The DuckDB file is a generated local data asset and is excluded from GitHub.
Keep a backup on the NAS, but avoid opening one writable database concurrently
from multiple computers.

### Backfill one-minute bars

The daily updater is useful for daily signals, but it does not replace the
original project's high-resolution collection goal. A separate resumable
backfill stores Alpaca one-minute bars without mixing them into the legacy raw
table or the curated daily table.

Start with a short, three-symbol SIP test:

```powershell
python -m market_signal_lab archive-backfill-minutes `
  "D:\MarketData\market-history.duckdb" `
  --symbols AAPL,MSFT,NVDA `
  --start 2026-08-31 `
  --end 2026-09-06 `
  --feed sip
```

Inspect the result:

```powershell
python -m market_signal_lab archive-minute-info `
  "D:\MarketData\market-history.duckdb"
```

After validating a small range, backfill every symbol already represented in
the archive:

```powershell
python -m market_signal_lab archive-backfill-minutes `
  "D:\MarketData\market-history.duckdb" `
  --all-symbols `
  --start 2024-08-31 `
  --feed sip `
  --batch-size 50 `
  --chunk-days 7
```

Each symbol/date chunk is downloaded, validated, and committed separately. If
the process is interrupted, repeat the same command; completed chunks, including
valid requests that returned no bars, are skipped. Keeping the same start date,
end date, feed, batch size, and chunk size gives the most efficient resume.

One-minute history for thousands of symbols can require hundreds of millions of
rows, many API pages, and tens of gigabytes. Keep the computer awake, retain a
pre-backfill database backup, and use only one writer. The application requests
explicit New York date boundaries and follows every Alpaca pagination token.

The one-minute layer is intentionally separate:

- `provider_minute_bars` stores validated SIP/IEX/OTC OHLCV bars.
- `provider_minute_coverage` records completed symbol/date chunks for restart.
- `provider_minute_update_log` records committed batch totals.
- `daily_prices` remains the stable input for the existing daily analysis.

The research workflow below derives consistent 5-, 15-, and 60-minute features
from this layer. A later version can append live bars from a WebSocket collector.

## Intraday prediction research

The first prediction-research workflow uses the consistent one-minute provider
layer rather than the mixed-frequency legacy files. It keeps only the regular US
equity session from 9:30 a.m. through 4:00 p.m. America/New_York time and can
materialize 5-, 15-, or 60-minute bars. The source minute table is never changed.

Build a five-minute dataset for the baseline-versus-technical comparison:

```powershell
python -m market_signal_lab research-build `
  ".\data\market-history.duckdb" `
  --interval 5 `
  --feed sip `
  --start 2025-09-08 `
  --end 2026-09-07
```

The generated `research_features_5m` table contains OHLCV bars, source-minute
coverage, and two feature sets:

| Feature set | Contents |
| --- | --- |
| `baseline` | Returns, range, close location, volume, volatility, 20-period trend, RSI, bar coverage, and time of day |
| `technical` | Every baseline feature plus normalized 50-period trend, Bollinger Bands, MACD, ATR, stochastic oscillator, OBV pressure, Chaikin Money Flow, VWAP distance, and trade-count activity |

Every feature is available when its bar closes. Price-level indicators are
normalized so a model can compare different securities. The target is the
following adjacent bar's open-to-close return, so the code never treats a
same-bar closing price as an executable earlier price.

Run the chronological baseline first:

```powershell
python -m market_signal_lab research-evaluate `
  ".\data\market-history.duckdb" `
  --interval 5 `
  --feature-set baseline `
  --train-sessions 126 `
  --test-sessions 21 `
  --holdout-sessions 21 `
  --target-move-bps 5 `
  --confidence 0.55 `
  --transaction-cost-bps 5 `
  --output ".\reports\walk-forward-5m-baseline.csv"
```

Then repeat the same experiment with the original technical-analysis ideas:

```powershell
python -m market_signal_lab research-evaluate `
  ".\data\market-history.duckdb" `
  --interval 5 `
  --feature-set technical `
  --train-sessions 126 `
  --test-sessions 21 `
  --holdout-sessions 21 `
  --target-move-bps 5 `
  --confidence 0.55 `
  --transaction-cost-bps 5 `
  --output ".\reports\walk-forward-5m-technical.csv"
```

Each fold trains on approximately six earlier trading months and evaluates the
following month. The newest 21 sessions are reserved and are not scored, fitted,
or used for model selection. Scaling and logistic-regression fitting occur inside
each fold using its training rows only. Moves between -5 and +5 basis points are
excluded from directional scoring so the initial experiment focuses on movement
larger than the target threshold.

The command reports model accuracy, balanced accuracy, a majority-class
baseline, a momentum-persistence baseline, high-confidence coverage and
accuracy, and average gross and cost-adjusted one-bar outcomes. The latter are
independent-signal diagnostics—not portfolio returns—because many ticker
signals overlap in time.

Each run writes three reports:

- the requested CSV contains fold-by-fold accuracy, coverage, and gross/net outcomes;
- a sibling `-symbols.csv` file shows whether results are broad or concentrated by ticker;
- a sibling `-coefficients.csv` file ranks standardized model weights and their stability.

Compare the two feature sets on the same dates and parameters. A technical
feature is useful only if it improves results across multiple unseen folds and
symbols after costs—not merely the combined headline accuracy.

The logistic model is deliberately a transparent baseline, not the final
algorithm and not evidence of a profitable system. It establishes a repeatable
measurement floor before testing more complex features or models.

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

## Prediction research roadmap

The original combined score remains an explainable heuristic. The intraday
logistic model is a measured baseline, not a claim that future movement has been
solved. The initial research target is whether the next 15-minute open-to-close
return exceeds a cost-aware threshold in either direction.

Evaluation must remain chronological:

1. Establish the 50-symbol baseline with six-month training and one-month tests.
2. Compare 5-, 15-, and 60-minute horizons without choosing from test results.
3. Add an explicit validation period for feature and model selection.
4. Expand to 300–500 symbols and measure performance across liquidity groups.
5. Repeat across multiple market regimes and reserve the newest period as a
   final untouched test.
6. Build a portfolio simulation only after the independent-signal results are
   stable, including spread, turnover, concurrent positions, and drawdown.

Raw directional accuracy is not enough. Results should also report class
balance, precision and recall, probability calibration, return after costs,
maximum drawdown, turnover, and performance versus simple baselines. An
unexpectedly high accuracy is treated as a reason to check for look-ahead bias,
target leakage, duplicate timestamps, or survivorship bias.

The 2023–2024 legacy data remains valuable for daily experiments and historical
context. Because its minute, five-minute, and hourly rows are not labeled by
interval, one-minute models should be trained and tested on the consistent Alpaca
minute layer rather than pretending the legacy rows have uniform granularity.

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
│   ├── intraday.py
│   ├── io.py
│   ├── research.py
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

The tests cover archive migration from folders and ZIP files, resumable minute
backfills, Alpaca pagination and timeframe selection, regular-session filtering,
intraday aggregation, adjacent next-bar targets, chronological walk-forward
folds, indicator calculations, signal warm-up and repeatability, input
validation, next-bar execution, transaction costs, CSV round trips, and the CLI.

## Security

This repository deliberately contains no order-placement integration. Optional
read-only market-data updates load credentials from the process environment. See
[SECURITY.md](SECURITY.md) for the credential-handling policy.

## License

Released under the [MIT License](LICENSE).
