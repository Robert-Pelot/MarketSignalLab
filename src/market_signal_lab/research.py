"""Leakage-aware intraday dataset construction and walk-forward evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from market_signal_lab.indicators import relative_strength_index

SUPPORTED_INTERVALS = (5, 15, 60)
FEATURE_COLUMNS = (
    "intrabar_return",
    "return_1",
    "return_4",
    "return_16",
    "range_pct",
    "close_location",
    "volume_ratio_20",
    "realized_volatility_20",
    "sma_ratio_20",
    "rsi_14",
    "bar_coverage",
    "minute_sin",
    "minute_cos",
)


@dataclass(frozen=True)
class ResearchDatasetResult:
    """Summary of one materialized intraday research dataset."""

    interval_minutes: int
    bars: int
    feature_ready_rows: int
    symbols: int
    sessions: int
    first_session: date
    last_session: date
    table: str


@dataclass(frozen=True)
class WalkForwardResult:
    """Fold details and aggregate metrics from chronological evaluation."""

    folds: pd.DataFrame
    predictions: pd.DataFrame
    metrics: dict[str, object]


def _research_table(interval_minutes: int) -> str:
    if interval_minutes not in SUPPORTED_INTERVALS:
        supported = ", ".join(str(value) for value in SUPPORTED_INTERVALS)
        raise ValueError(f"Interval must be one of: {supported}")
    return f"research_features_{interval_minutes}m"


def _validate_date(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from error


def _aggregate_regular_session_bars(
    connection: duckdb.DuckDBPyConnection,
    *,
    interval_minutes: int,
    feed: str,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    interval = f"{interval_minutes} minutes"
    conditions = ["feed = ?"]
    parameters: list[object] = [feed]
    if start:
        conditions.append("CAST(timezone('America/New_York', timestamp) AS DATE) >= ?")
        parameters.append(start)
    if end:
        conditions.append("CAST(timezone('America/New_York', timestamp) AS DATE) <= ?")
        parameters.append(end)
    where = " AND ".join(conditions)
    return connection.execute(
        f"""
        WITH localized AS (
            SELECT
                ticker,
                timezone('America/New_York', timestamp) AS local_timestamp,
                open,
                high,
                low,
                close,
                volume,
                trade_count,
                vwap
            FROM provider_minute_bars
            WHERE {where}
        ),
        regular_session AS (
            SELECT *
            FROM localized
            WHERE CAST(local_timestamp AS TIME) >= TIME '09:30:00'
              AND CAST(local_timestamp AS TIME) < TIME '16:00:00'
        )
        SELECT
            ticker,
            time_bucket(INTERVAL '{interval}', local_timestamp, INTERVAL '30 minutes')
                AS timestamp,
            CAST(local_timestamp AS DATE) AS session_date,
            arg_min(open, local_timestamp) AS open,
            max(high) AS high,
            min(low) AS low,
            arg_max(close, local_timestamp) AS close,
            sum(volume) AS volume,
            sum(trade_count) AS trade_count,
            sum(coalesce(vwap, close) * volume) / nullif(sum(volume), 0) AS vwap,
            count(*) AS source_minutes
        FROM regular_session
        GROUP BY ticker, timestamp, session_date
        ORDER BY ticker, timestamp
        """,
        parameters,
    ).fetchdf()


def _add_research_features(bars: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    if bars.empty:
        raise ValueError("No regular-session minute bars matched the requested selection")
    result = bars.sort_values(["ticker", "timestamp"]).reset_index(drop=True).copy()
    grouped_close = result.groupby("ticker", sort=False)["close"]

    result["intrabar_return"] = result["close"] / result["open"] - 1
    result["return_1"] = grouped_close.pct_change(1, fill_method=None)
    result["return_4"] = grouped_close.pct_change(4, fill_method=None)
    result["return_16"] = grouped_close.pct_change(16, fill_method=None)
    result["range_pct"] = (result["high"] - result["low"]) / result["open"]
    spread = (result["high"] - result["low"]).replace(0, np.nan)
    result["close_location"] = (result["close"] - result["low"]) / spread

    volume_average = result.groupby("ticker", sort=False)["volume"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    result["volume_ratio_20"] = result["volume"] / volume_average.replace(0, np.nan) - 1
    result["realized_volatility_20"] = result.groupby("ticker", sort=False)["return_1"].transform(
        lambda values: values.rolling(20, min_periods=20).std(ddof=0)
    )
    moving_average = grouped_close.transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    result["sma_ratio_20"] = result["close"] / moving_average - 1
    result["rsi_14"] = grouped_close.transform(relative_strength_index)
    result["bar_coverage"] = result["source_minutes"] / interval_minutes

    minute_of_day = result["timestamp"].dt.hour * 60 + result["timestamp"].dt.minute
    session_progress = (minute_of_day - (9 * 60 + 30)) / 390
    result["minute_sin"] = np.sin(2 * np.pi * session_progress)
    result["minute_cos"] = np.cos(2 * np.pi * session_progress)

    session_group = result.groupby(["ticker", "session_date"], sort=False)
    next_timestamp = session_group["timestamp"].shift(-1)
    next_open = session_group["open"].shift(-1)
    next_close = session_group["close"].shift(-1)
    gap_seconds = (next_timestamp - result["timestamp"]).dt.total_seconds()
    session_close = pd.to_datetime(result["session_date"]) + np.timedelta64(16, "h")
    target_bar_end = next_timestamp + np.timedelta64(interval_minutes, "m")
    adjacent = (gap_seconds == interval_minutes * 60) & (target_bar_end <= session_close)
    result["future_return"] = (next_close / next_open - 1).where(adjacent)
    result.replace([np.inf, -np.inf], np.nan, inplace=True)
    return result


def build_research_dataset(
    database: str | Path,
    *,
    interval_minutes: int = 15,
    feed: str = "sip",
    start: str | None = None,
    end: str | None = None,
) -> ResearchDatasetResult:
    """Materialize regular-session bars and backward-looking research features."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    table = _research_table(interval_minutes)
    if feed not in {"iex", "sip", "otc"}:
        raise ValueError("Feed must be iex, sip, or otc")
    start_date = _validate_date(start, "Start date")
    end_date = _validate_date(end, "End date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("Start date must not be after end date")

    connection = duckdb.connect(str(path))
    try:
        exists = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name = 'provider_minute_bars'
            """
        ).fetchone()[0]
        if not exists:
            raise ValueError("The archive does not contain one-minute provider bars")
        bars = _aggregate_regular_session_bars(
            connection,
            interval_minutes=interval_minutes,
            feed=feed,
            start=start_date,
            end=end_date,
        )
        features = _add_research_features(bars, interval_minutes)
        first_session = pd.Timestamp(features["session_date"].min()).date()
        last_session = pd.Timestamp(features["session_date"].max()).date()
        connection.register("_research_features", features)
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(f"CREATE TABLE {table} AS SELECT * FROM _research_features")
            connection.execute(
                f"CREATE INDEX {table}_lookup ON {table} (ticker, session_date, timestamp)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_dataset_metadata (
                    interval_minutes INTEGER PRIMARY KEY,
                    feed VARCHAR NOT NULL,
                    start_date DATE,
                    end_date DATE,
                    bar_count BIGINT NOT NULL,
                    built_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
                )
                """
            )
            connection.execute(
                "DELETE FROM research_dataset_metadata WHERE interval_minutes = ?",
                [interval_minutes],
            )
            connection.execute(
                """
                INSERT INTO research_dataset_metadata
                    (interval_minutes, feed, start_date, end_date, bar_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                [interval_minutes, feed, first_session, last_session, len(features)],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.unregister("_research_features")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()

    ready = features[list(FEATURE_COLUMNS) + ["future_return"]].notna().all(axis=1)
    return ResearchDatasetResult(
        interval_minutes=interval_minutes,
        bars=len(features),
        feature_ready_rows=int(ready.sum()),
        symbols=int(features["ticker"].nunique()),
        sessions=int(features["session_date"].nunique()),
        first_session=first_session,
        last_session=last_session,
        table=table,
    )


def load_research_dataset(
    database: str | Path,
    *,
    interval_minutes: int = 15,
) -> pd.DataFrame:
    """Load one previously materialized research feature table."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Archive database not found: {path}")
    table = _research_table(interval_minutes)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        exists = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        if not exists:
            raise ValueError(
                f"Research dataset {table} has not been built; run research-build first"
            )
        return connection.execute(f"SELECT * FROM {table} ORDER BY timestamp, ticker").fetchdf()
    finally:
        connection.close()


def evaluate_walk_forward(
    dataset: pd.DataFrame,
    *,
    train_sessions: int = 126,
    test_sessions: int = 21,
    holdout_sessions: int = 21,
    target_move_bps: float = 5.0,
    confidence: float = 0.55,
    transaction_cost_bps: float = 5.0,
) -> WalkForwardResult:
    """Evaluate a logistic baseline using rolling, strictly chronological folds."""
    required = {"ticker", "timestamp", "session_date", "future_return", *FEATURE_COLUMNS}
    missing = sorted(required - set(dataset.columns))
    if missing:
        raise ValueError(f"Missing research columns: {', '.join(missing)}")
    if train_sessions < 20:
        raise ValueError("Training window must contain at least 20 sessions")
    if test_sessions < 1:
        raise ValueError("Test window must contain at least one session")
    if holdout_sessions < 0:
        raise ValueError("Holdout window cannot be negative")
    if target_move_bps < 0:
        raise ValueError("Target move cannot be negative")
    if not 0.5 <= confidence < 1:
        raise ValueError("Confidence must be at least 0.5 and less than 1")
    if transaction_cost_bps < 0:
        raise ValueError("Transaction cost cannot be negative")

    columns = ["ticker", "timestamp", "session_date", "future_return", *FEATURE_COLUMNS]
    data = dataset[columns].replace([np.inf, -np.inf], np.nan).dropna().copy()
    threshold = target_move_bps / 10_000
    data = data[(data["future_return"] > threshold) | (data["future_return"] < -threshold)]
    if data.empty:
        raise ValueError("No rows exceed the requested target-move threshold")
    data["target_up"] = (data["future_return"] > threshold).astype(int)
    data["session_date"] = pd.to_datetime(data["session_date"]).dt.date
    sessions = sorted(data["session_date"].unique())
    required_sessions = train_sessions + test_sessions + holdout_sessions
    if len(sessions) < required_sessions:
        raise ValueError(
            f"At least {required_sessions} sessions are required; dataset contains {len(sessions)}"
        )

    holdout_dates = sessions[-holdout_sessions:] if holdout_sessions else []
    evaluation_sessions = sessions[:-holdout_sessions] if holdout_sessions else sessions
    holdout_rows = int(data["session_date"].isin(holdout_dates).sum())
    folds: list[dict[str, object]] = []
    prediction_frames: list[pd.DataFrame] = []
    test_offset = train_sessions
    fold_number = 0
    while test_offset < len(evaluation_sessions):
        training_dates = evaluation_sessions[test_offset - train_sessions : test_offset]
        testing_dates = evaluation_sessions[test_offset : test_offset + test_sessions]
        if not testing_dates:
            break
        train = data[data["session_date"].isin(training_dates)]
        test = data[data["session_date"].isin(testing_dates)].copy()
        if train["target_up"].nunique() < 2:
            raise ValueError("A training fold contains only one target class")

        model = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=500,
                        random_state=42,
                    ),
                ),
            ]
        )
        model.fit(train[list(FEATURE_COLUMNS)], train["target_up"])
        probability = model.predict_proba(test[list(FEATURE_COLUMNS)])[:, 1]
        predicted = (probability >= 0.5).astype(int)
        persistence = (test["return_1"].to_numpy() > 0).astype(int)
        action = np.where(
            probability >= confidence,
            1,
            np.where(probability <= 1 - confidence, -1, 0),
        )
        test["probability_up"] = probability
        test["predicted_up"] = predicted
        test["persistence_up"] = persistence
        test["action"] = action
        prediction_frames.append(test)

        fold_number += 1
        y_test = test["target_up"].to_numpy()
        confident = action != 0
        folds.append(
            {
                "fold": fold_number,
                "train_start": training_dates[0],
                "train_end": training_dates[-1],
                "test_start": testing_dates[0],
                "test_end": testing_dates[-1],
                "train_rows": len(train),
                "test_rows": len(test),
                "up_target_pct": 100 * float(y_test.mean()),
                "accuracy": accuracy_score(y_test, predicted),
                "balanced_accuracy": balanced_accuracy_score(y_test, predicted),
                "persistence_accuracy": accuracy_score(y_test, persistence),
                "confident_rows": int(confident.sum()),
                "confident_accuracy": (
                    accuracy_score(y_test[confident], (action[confident] == 1).astype(int))
                    if confident.any()
                    else np.nan
                ),
            }
        )
        test_offset += test_sessions

    predictions = pd.concat(prediction_frames, ignore_index=True)
    target = predictions["target_up"].to_numpy()
    predicted = predictions["predicted_up"].to_numpy()
    persistence = predictions["persistence_up"].to_numpy()
    action = predictions["action"].to_numpy()
    confident = action != 0
    majority_accuracy = max(float(target.mean()), 1 - float(target.mean()))
    if confident.any():
        confident_accuracy = accuracy_score(target[confident], (action[confident] == 1).astype(int))
        net_bps = (
            action[confident] * predictions.loc[confident, "future_return"].to_numpy() * 10_000
            - 2 * transaction_cost_bps
        )
        average_net_bps = float(net_bps.mean())
    else:
        confident_accuracy = float("nan")
        average_net_bps = float("nan")
    metrics: dict[str, object] = {
        "folds": len(folds),
        "test_rows": len(predictions),
        "accuracy": float(accuracy_score(target, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(target, predicted)),
        "precision_up": float(precision_score(target, predicted, zero_division=0)),
        "recall_up": float(recall_score(target, predicted, zero_division=0)),
        "f1_up": float(f1_score(target, predicted, zero_division=0)),
        "up_rows": int(target.sum()),
        "down_rows": int(len(target) - target.sum()),
        "majority_baseline_accuracy": majority_accuracy,
        "persistence_accuracy": float(accuracy_score(target, persistence)),
        "confident_rows": int(confident.sum()),
        "confident_coverage_pct": float(100 * confident.mean()),
        "confident_accuracy": float(confident_accuracy),
        "average_net_bps_per_confident_signal": average_net_bps,
        "holdout_sessions": len(holdout_dates),
        "holdout_rows": holdout_rows,
        "holdout_start": holdout_dates[0] if holdout_dates else None,
        "holdout_end": holdout_dates[-1] if holdout_dates else None,
    }
    return WalkForwardResult(
        folds=pd.DataFrame(folds),
        predictions=predictions,
        metrics=metrics,
    )
