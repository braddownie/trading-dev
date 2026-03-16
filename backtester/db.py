"""
Database layer for storing backtest runs, results, and equity curves.
Uses SQLite — no server required, single file at backtester/results/trading.db
"""
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "results" / "trading.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS test_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT,
                type         TEXT,      -- grid | walk_forward | rolling_walk_forward
                train_start  TEXT,
                train_end    TEXT,
                test_start   TEXT,
                test_end     TEXT,
                starting_cash REAL,
                slippage_pct  REAL DEFAULT 0.0,
                spread_pct    REAL DEFAULT 0.0,
                created_at   TEXT,
                notes        TEXT
            );

            CREATE TABLE IF NOT EXISTS test_results (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER REFERENCES test_runs(id),
                rank             INTEGER,
                min_rel_volume   REAL,
                min_change_pct   REAL,
                take_profit_pct  REAL,
                stop_loss_pct    REAL,
                total_return_pct REAL,
                final_value      REAL,
                realized_pnl     REAL,
                total_trades     INTEGER,
                wins             INTEGER,
                losses           INTEGER,
                win_rate_pct     REAL
            );

            CREATE TABLE IF NOT EXISTS equity_curves (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER REFERENCES test_runs(id),
                result_id        INTEGER REFERENCES test_results(id),
                date             TEXT,
                portfolio_value  REAL
            );

            CREATE INDEX IF NOT EXISTS idx_results_run
                ON test_results(run_id);
            CREATE INDEX IF NOT EXISTS idx_curves_result
                ON equity_curves(result_id);

            CREATE TABLE IF NOT EXISTS rolling_wf_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT,
                train_days    INTEGER,
                test_days     INTEGER,
                slippage_pct  REAL DEFAULT 0.0,
                spread_pct    REAL DEFAULT 0.0,
                starting_cash REAL,
                created_at    TEXT,
                notes         TEXT
            );

            CREATE TABLE IF NOT EXISTS rolling_wf_windows (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER REFERENCES rolling_wf_runs(id),
                window_num        INTEGER,
                train_start       TEXT,
                train_end         TEXT,
                test_start        TEXT,
                test_end          TEXT,
                best_params       TEXT,
                train_return_pct  REAL,
                test_return_pct   REAL,
                strategy_balance  REAL,
                benchmark_balance REAL,
                beat_benchmark    INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_rwf_windows_run
                ON rolling_wf_windows(run_id);
        """)


def save_run(
    name: str,
    run_type: str,
    train_start: str,
    train_end: str,
    test_start: str | None,
    test_end: str | None,
    starting_cash: float,
    slippage_pct: float = 0.0,
    spread_pct: float = 0.0,
    notes: str = "",
) -> int:
    """Insert a test run and return its id."""
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO test_runs
                (name, type, train_start, train_end, test_start, test_end,
                 starting_cash, slippage_pct, spread_pct, created_at, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            name, run_type, train_start, train_end,
            test_start, test_end,
            starting_cash, slippage_pct, spread_pct,
            datetime.now().isoformat(), notes,
        ))
        return cur.lastrowid


def save_results(run_id: int, results: list[dict]) -> list[int]:
    """Insert all result rows for a run. Returns list of inserted result ids."""
    result_ids = []
    with get_connection() as conn:
        for r in results:
            cur = conn.execute("""
                INSERT INTO test_results
                    (run_id, rank, min_rel_volume, min_change_pct,
                     take_profit_pct, stop_loss_pct, total_return_pct,
                     final_value, realized_pnl, total_trades,
                     wins, losses, win_rate_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_id, r["rank"], r["min_rel_volume"], r["min_change_pct"],
                r["take_profit_pct"], r["stop_loss_pct"], r["total_return_%"],
                r["final_value"], r["realized_pnl"], r["total_trades"],
                r["wins"], r["losses"], r["win_rate_%"],
            ))
            result_ids.append(cur.lastrowid)
    return result_ids


def save_equity_curves(run_id: int, result_id: int, curve: list[dict]):
    """Insert daily equity curve rows for one result."""
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO equity_curves (run_id, result_id, date, portfolio_value)
            VALUES (?,?,?,?)
        """, [
            (run_id, result_id, row["date"], row["portfolio_value"])
            for row in curve
        ])


def save_rolling_wf_run(
    name: str,
    train_days: int,
    test_days: int,
    slippage_pct: float,
    spread_pct: float,
    starting_cash: float,
    notes: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO rolling_wf_runs
                (name, train_days, test_days, slippage_pct, spread_pct,
                 starting_cash, created_at, notes)
            VALUES (?,?,?,?,?,?,?,?)
        """, (name, train_days, test_days, slippage_pct, spread_pct,
              starting_cash, datetime.now().isoformat(), notes))
        return cur.lastrowid


def save_rolling_wf_window(
    run_id: int,
    window_num: int,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    best_params_json: str,
    train_return_pct: float,
    test_return_pct: float,
    strategy_balance: float,
    benchmark_balance: float,
    beat_benchmark: int,
) -> int:
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO rolling_wf_windows
                (run_id, window_num, train_start, train_end, test_start, test_end,
                 best_params, train_return_pct, test_return_pct,
                 strategy_balance, benchmark_balance, beat_benchmark)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (run_id, window_num, train_start, train_end, test_start, test_end,
              best_params_json, train_return_pct, test_return_pct,
              strategy_balance, benchmark_balance, beat_benchmark))
        return cur.lastrowid


# --- Query helpers (used by web UI) ---

def list_runs() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM test_runs ORDER BY created_at DESC", conn)


def get_results(run_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM test_results WHERE run_id=? ORDER BY rank",
            conn, params=(run_id,)
        )


def get_equity_curve(result_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT date, portfolio_value FROM equity_curves WHERE result_id=? ORDER BY date",
            conn, params=(result_id,)
        )


def get_top_results(run_id: int, n: int = 10) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM test_results WHERE run_id=? ORDER BY total_return_pct DESC LIMIT ?",
            conn, params=(run_id, n)
        )
