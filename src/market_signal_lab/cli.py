"""Command-line interface for MarketSignalLab."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from market_signal_lab.alpaca import AlpacaCredentials, AlpacaMarketDataClient
from market_signal_lab.analysis import analyze_prices
from market_signal_lab.archive import (
    archive_statistics,
    build_archive,
    inspect_legacy_archive,
    list_archive_symbols,
    load_daily_prices,
    merge_provider_daily_bars,
    next_update_start,
)
from market_signal_lab.backtest import BacktestResult, run_backtest
from market_signal_lab.demo import generate_demo_prices
from market_signal_lab.intraday import (
    MinuteBackfillProgress,
    backfill_minute_bars,
    minute_archive_statistics,
)
from market_signal_lab.io import load_prices, save_csv
from market_signal_lab.research import (
    FEATURE_SETS,
    SUPPORTED_INTERVALS,
    build_research_dataset,
    evaluate_walk_forward,
    load_research_dataset,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-signal-lab",
        description="Analyze OHLCV prices and run an educational next-bar backtest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run an offline demonstration")
    demo.add_argument("--rows", type=int, default=260, help="Number of synthetic daily bars")
    demo.add_argument("--seed", type=int, default=42, help="Random seed for repeatability")
    demo.add_argument("--output", type=Path, help="Optional analysis CSV destination")
    _add_backtest_arguments(demo)

    analyze = subparsers.add_parser("analyze", help="Analyze an OHLCV CSV file")
    analyze.add_argument(
        "csv_file", type=Path, help="CSV containing Date/Open/High/Low/Close/Volume"
    )
    analyze.add_argument("--output", type=Path, default=Path("reports/analysis.csv"))
    _add_backtest_arguments(analyze)

    inspect = subparsers.add_parser("archive-inspect", help="Inspect a legacy directory or ZIP")
    inspect.add_argument("source", type=Path)

    build = subparsers.add_parser("archive-build", help="Build a DuckDB market-data archive")
    build.add_argument("source", type=Path, help="Legacy data directory or ZIP")
    build.add_argument("database", type=Path, help="Destination .duckdb file")
    build.add_argument("--replace", action="store_true", help="Replace an existing database")

    info = subparsers.add_parser("archive-info", help="Show statistics for an archive database")
    info.add_argument("database", type=Path)

    update = subparsers.add_parser(
        "archive-update", help="Download read-only Alpaca daily bars into an archive"
    )
    update.add_argument("database", type=Path)
    selection = update.add_mutually_exclusive_group(required=True)
    selection.add_argument("--symbols", help="Comma-separated ticker symbols")
    selection.add_argument(
        "--all-symbols", action="store_true", help="Update every symbol already in the archive"
    )
    update.add_argument("--start", help="Inclusive YYYY-MM-DD; defaults after newest archive date")
    update.add_argument("--end", help="Inclusive YYYY-MM-DD; defaults to today")
    update.add_argument("--feed", choices=("iex", "sip", "otc"), default="iex")
    update.add_argument("--batch-size", type=int, default=50)

    minute_backfill = subparsers.add_parser(
        "archive-backfill-minutes",
        help="Download Alpaca one-minute bars with resumable checkpoints",
    )
    minute_backfill.add_argument("database", type=Path)
    minute_selection = minute_backfill.add_mutually_exclusive_group(required=True)
    minute_selection.add_argument("--symbols", help="Comma-separated ticker symbols")
    minute_selection.add_argument(
        "--all-symbols",
        action="store_true",
        help="Backfill every symbol already in the archive",
    )
    minute_backfill.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD")
    minute_backfill.add_argument(
        "--end",
        help="Inclusive YYYY-MM-DD; defaults to the last completed calendar day",
    )
    minute_backfill.add_argument("--feed", choices=("iex", "sip", "otc"), default="iex")
    minute_backfill.add_argument("--batch-size", type=int, default=50)
    minute_backfill.add_argument("--chunk-days", type=int, default=7)

    minute_info = subparsers.add_parser(
        "archive-minute-info", help="Show one-minute archive counts and coverage"
    )
    minute_info.add_argument("database", type=Path)

    research_build = subparsers.add_parser(
        "research-build",
        help="Build regular-session bars and leakage-aware research features",
    )
    research_build.add_argument("database", type=Path)
    research_build.add_argument("--interval", type=int, choices=SUPPORTED_INTERVALS, default=15)
    research_build.add_argument("--feed", choices=("iex", "sip", "otc"), default="sip")
    research_build.add_argument("--start", help="Optional inclusive YYYY-MM-DD")
    research_build.add_argument("--end", help="Optional inclusive YYYY-MM-DD")

    research_evaluate = subparsers.add_parser(
        "research-evaluate",
        help="Run a chronological walk-forward logistic-regression baseline",
    )
    research_evaluate.add_argument("database", type=Path)
    research_evaluate.add_argument("--interval", type=int, choices=SUPPORTED_INTERVALS, default=15)
    research_evaluate.add_argument("--train-sessions", type=int, default=126)
    research_evaluate.add_argument("--test-sessions", type=int, default=21)
    research_evaluate.add_argument("--holdout-sessions", type=int, default=21)
    research_evaluate.add_argument(
        "--feature-set",
        choices=tuple(FEATURE_SETS),
        default="baseline",
        help="Features to evaluate: baseline or baseline plus normalized technical indicators",
    )
    research_evaluate.add_argument("--target-move-bps", type=float, default=5.0)
    research_evaluate.add_argument("--confidence", type=float, default=0.55)
    research_evaluate.add_argument("--transaction-cost-bps", type=float, default=5.0)
    research_evaluate.add_argument(
        "--output",
        type=Path,
        help="Fold-summary CSV destination",
    )

    archive_analyze = subparsers.add_parser(
        "archive-analyze", help="Analyze one ticker from an archive database"
    )
    archive_analyze.add_argument("database", type=Path)
    archive_analyze.add_argument("ticker")
    archive_analyze.add_argument("--start")
    archive_analyze.add_argument("--end")
    archive_analyze.add_argument("--output", type=Path)
    _add_backtest_arguments(archive_analyze)
    return parser


def _add_backtest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)


def _print_summary(result: BacktestResult, latest_signal: str, latest_score: int) -> None:
    print("MarketSignalLab result")
    print(f"Latest signal:        {latest_signal} (score {latest_score:+d})")
    print(f"Initial cash:         ${result.metrics['initial_cash']:,.2f}")
    print(f"Final equity:         ${result.metrics['final_equity']:,.2f}")
    print(f"Strategy return:      {result.metrics['total_return_pct']:+.2f}%")
    print(f"Buy-and-hold return:  {result.metrics['buy_and_hold_return_pct']:+.2f}%")
    print(f"Executed trades:      {result.metrics['trade_count']}")
    print("Educational use only; this is not financial advice.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "archive-inspect":
            inspection = inspect_legacy_archive(arguments.source)
            print(f"Price files:          {inspection.price_file_count:,}")
            print(f"Uncompressed size:    {inspection.uncompressed_bytes / 1024**3:.2f} GiB")
            return 0
        if arguments.command == "archive-build":

            def progress(position: int, total: int, ticker: str) -> None:
                if position == 1 or position % 100 == 0 or position == total:
                    print(f"Importing {position:,}/{total:,}: {ticker}")

            result = build_archive(
                arguments.source,
                arguments.database,
                replace=arguments.replace,
                progress=progress,
            )
            print(f"Archive created:      {result.database}")
            print(f"Files imported:       {result.files_imported:,}")
            print(f"Validated rows:       {result.rows_imported:,}")
            print(f"Rejected rows:        {result.rows_rejected:,}")
            print(f"Symbols:              {result.symbols:,}")
            print(f"Daily bars:           {result.daily_rows:,}")
            return 0
        if arguments.command == "archive-info":
            statistics = archive_statistics(arguments.database)
            print(f"Symbols:              {statistics['symbols']:,}")
            print(f"Raw rows:             {statistics['raw_rows']:,}")
            print(f"Daily bars:           {statistics['daily_rows']:,}")
            date_range = f"{statistics['first_date']} through {statistics['last_date']}"
            print(f"Date range:           {date_range}")
            print(f"Database size:        {statistics['database_bytes'] / 1024**3:.2f} GiB")
            return 0
        if arguments.command == "archive-update":
            credentials = AlpacaCredentials.from_environment()
            symbols = (
                list_archive_symbols(arguments.database)
                if arguments.all_symbols
                else arguments.symbols.split(",")
            )
            start = arguments.start or next_update_start(arguments.database, symbols)
            end = arguments.end or (date.today() - timedelta(days=1)).isoformat()
            print(f"Requesting daily bars for {len(symbols):,} symbols: {start} through {end}")
            client = AlpacaMarketDataClient(credentials)
            bars = client.fetch_daily_bars(
                symbols,
                start=start,
                end=end,
                feed=arguments.feed,
                batch_size=arguments.batch_size,
            )
            merged = merge_provider_daily_bars(arguments.database, bars)
            print(f"Rows merged:          {merged['rows_merged']:,}")
            print(f"Symbols updated:      {merged['symbols_updated']:,}")
            return 0
        if arguments.command == "archive-backfill-minutes":
            credentials = AlpacaCredentials.from_environment()
            symbols = (
                list_archive_symbols(arguments.database)
                if arguments.all_symbols
                else arguments.symbols.split(",")
            )
            end = arguments.end or (date.today() - timedelta(days=1)).isoformat()
            print(
                f"Backfilling one-minute {arguments.feed.upper()} bars for "
                f"{len(symbols):,} symbols: {arguments.start} through {end}"
            )

            def minute_progress(item: MinuteBackfillProgress) -> None:
                if item.position == 1 or item.position % 10 == 0 or item.position == item.total:
                    state = "already complete" if item.skipped else f"{item.rows:,} rows"
                    print(
                        f"Job {item.position:,}/{item.total:,}: {item.start} through "
                        f"{item.end}; {item.symbol_count:,} symbols; {state}"
                    )

            result = backfill_minute_bars(
                arguments.database,
                symbols,
                AlpacaMarketDataClient(credentials),
                start=arguments.start,
                end=end,
                feed=arguments.feed,
                batch_size=arguments.batch_size,
                chunk_days=arguments.chunk_days,
                progress=minute_progress,
            )
            print(f"Jobs completed:       {result.jobs_completed:,}")
            print(f"Jobs skipped:         {result.jobs_skipped:,}")
            print(f"Rows merged:          {result.rows_merged:,}")
            print(f"Symbols updated:      {result.symbols_updated:,}")
            return 0
        if arguments.command == "archive-minute-info":
            statistics = minute_archive_statistics(arguments.database)
            print(f"Minute rows:          {statistics['rows']:,}")
            print(f"Symbols:              {statistics['symbols']:,}")
            date_range = (
                f"{statistics['first_timestamp']} through {statistics['last_timestamp']}"
                if statistics["first_timestamp"]
                else "not started"
            )
            print(f"Timestamp range:      {date_range}")
            print(f"Coverage records:     {statistics['coverage_records']:,}")
            print(f"Database size:        {statistics['database_bytes'] / 1024**3:.2f} GiB")
            return 0
        if arguments.command == "research-build":
            result = build_research_dataset(
                arguments.database,
                interval_minutes=arguments.interval,
                feed=arguments.feed,
                start=arguments.start,
                end=arguments.end,
            )
            print(f"Research table:       {result.table}")
            print(f"Interval:             {result.interval_minutes} minutes")
            print(f"Regular-session bars: {result.bars:,}")
            print(f"Baseline-ready rows:  {result.feature_ready_rows:,}")
            print(f"Technical-ready rows: {result.technical_ready_rows:,}")
            print(f"Symbols:              {result.symbols:,}")
            print(f"Trading sessions:     {result.sessions:,}")
            print(f"Session range:        {result.first_session} through {result.last_session}")
            return 0
        if arguments.command == "research-evaluate":
            dataset = load_research_dataset(arguments.database, interval_minutes=arguments.interval)
            result = evaluate_walk_forward(
                dataset,
                train_sessions=arguments.train_sessions,
                test_sessions=arguments.test_sessions,
                holdout_sessions=arguments.holdout_sessions,
                feature_set=arguments.feature_set,
                target_move_bps=arguments.target_move_bps,
                confidence=arguments.confidence,
                transaction_cost_bps=arguments.transaction_cost_bps,
            )
            output = arguments.output or Path(
                f"reports/walk-forward-{arguments.interval}m-{arguments.feature_set}.csv"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            result.folds.to_csv(output, index=False)
            symbol_output = output.with_name(f"{output.stem}-symbols{output.suffix}")
            coefficient_output = output.with_name(f"{output.stem}-coefficients{output.suffix}")
            result.symbols.to_csv(symbol_output, index=False)
            result.coefficients.to_csv(coefficient_output, index=False)
            metrics = result.metrics
            print("Walk-forward baseline")
            print(
                f"Feature set:          {metrics['feature_set']} ({metrics['features']} features)"
            )
            print(f"Folds:                {metrics['folds']:,}")
            print(f"Unseen test rows:     {metrics['test_rows']:,}")
            print(f"Model accuracy:       {100 * metrics['accuracy']:.2f}%")
            print(f"Balanced accuracy:    {100 * metrics['balanced_accuracy']:.2f}%")
            print(f"Up precision:         {100 * metrics['precision_up']:.2f}%")
            print(f"Up recall:            {100 * metrics['recall_up']:.2f}%")
            print(f"Up/down targets:      {metrics['up_rows']:,} / {metrics['down_rows']:,}")
            print(f"Majority baseline:    {100 * metrics['majority_baseline_accuracy']:.2f}%")
            print(f"Persistence baseline: {100 * metrics['persistence_accuracy']:.2f}%")
            print(
                f"Reserved holdout:     {metrics['holdout_sessions']:,} sessions; "
                f"{metrics['holdout_rows']:,} rows"
            )
            print(
                f"Holdout range:        {metrics['holdout_start']} through {metrics['holdout_end']}"
            )
            print(f"Confident signals:    {metrics['confident_rows']:,}")
            print(f"Signal coverage:      {metrics['confident_coverage_pct']:.2f}%")
            print(f"Confident accuracy:   {100 * metrics['confident_accuracy']:.2f}%")
            print(
                "Average gross outcome:"
                f" {metrics['average_gross_bps_per_confident_signal']:+.2f} bps/signal"
            )
            print(
                "Average net outcome:  "
                f"{metrics['average_net_bps_per_confident_signal']:+.2f} bps/signal"
            )
            print(f"Fold report:          {output}")
            print(f"Symbol report:        {symbol_output}")
            print(f"Coefficient report:   {coefficient_output}")
            print("Educational research only; no orders are submitted.")
            return 0
        if arguments.command == "demo":
            prices = generate_demo_prices(rows=arguments.rows, seed=arguments.seed)
        elif arguments.command == "archive-analyze":
            prices = load_daily_prices(
                arguments.database,
                arguments.ticker,
                start=arguments.start,
                end=arguments.end,
            )
        else:
            prices = load_prices(arguments.csv_file)

        analysis = analyze_prices(prices)
        result = run_backtest(
            analysis,
            initial_cash=arguments.initial_cash,
            transaction_cost_bps=arguments.transaction_cost_bps,
        )
        if arguments.output:
            destination = save_csv(analysis, arguments.output)
            print(f"Analysis saved to:    {destination}")
        latest = analysis.iloc[-1]
        _print_summary(result, str(latest["signal"]), int(latest["signal_score"]))
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.error(str(error))
        return 2
