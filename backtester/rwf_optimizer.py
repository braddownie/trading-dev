#!/usr/bin/env python3
"""
Rolling walk-forward optimizer.

Runs all 45 combinations of reoptimization period / training window / drawdown threshold,
each with the full expanded 8,640-combination param grid. Produces a ranked summary
table showing which configuration produced the best out-of-sample results.

History is loaded once and shared across all 45 runs.

Usage:
    python -m backtester.rwf_optimizer [options]
    python main.py rwf-optimize [options]
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher import get_sp500_tickers
from backtester.backtest import load_history, STARTING_CASH
from backtester.db import init_db, save_optimizer_run, get_optimizer_results
from backtester.rolling_walkforward import run_rolling_walk_forward, ANNUAL_BENCH, quarterly_bench_rate

# --- Test matrix ---

REOPT_CONFIGS = [
    {"label": "monthly_3m_train",    "train_days": 63,  "test_days": 21},
    {"label": "monthly_6m_train",    "train_days": 126, "test_days": 21},
    {"label": "monthly_1y_train",    "train_days": 252, "test_days": 21},
    {"label": "quarterly_6m_train",  "train_days": 126, "test_days": 63},
    {"label": "quarterly_1y_train",  "train_days": 252, "test_days": 63},
    {"label": "quarterly_18m_train", "train_days": 378, "test_days": 63},
    {"label": "6mo_6m_train",        "train_days": 126, "test_days": 126},
    {"label": "6mo_1y_train",        "train_days": 252, "test_days": 126},
    {"label": "6mo_18m_train",       "train_days": 378, "test_days": 126},
]

DRAWDOWN_THRESHOLDS = [None, 10.0, 15.0, 16.0, 20.0]  # None = no limit


def run_optimizer(
    slippage_pct: float = 0.0005,
    spread_pct:   float = 0.0003,
    max_workers:  int   = None,
    name:         str   = "",
    notes:        str   = "",
):
    start_time = datetime.now()
    total_runs = len(REOPT_CONFIGS) * len(DRAWDOWN_THRESHOLDS)
    print(f"RWF Optimizer started — {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total runs: {total_runs}  ({len(REOPT_CONFIGS)} configs × {len(DRAWDOWN_THRESHOLDS)} drawdown thresholds)\n")

    init_db()

    # Save parent optimizer run
    opt_name = name or f"rwf_opt_{start_time.strftime('%Y%m%d_%H%M%S')}"
    opt_run_id = save_optimizer_run(name=opt_name, notes=notes)
    print(f"Optimizer run ID: {opt_run_id}  ({opt_name})\n")

    # Load history once — reused across all 45 runs
    print("Fetching ticker universe (S&P 500 only)...")
    tickers = get_sp500_tickers()
    print(f"  {len(tickers)} tickers\n")

    print("Downloading full history (max, capped to 2015-01-01 onward)...")
    history = load_history(tickers, period="max")
    history = history[history["date"] >= pd.Timestamp("2015-01-01")].copy()
    print(f"  {history['ticker'].nunique()} tickers with valid data\n")
    print("=" * 70)

    run_num = 0
    for config in REOPT_CONFIGS:
        for drawdown in DRAWDOWN_THRESHOLDS:
            run_num += 1
            dd_label = f"dd{drawdown}" if drawdown is not None else "no_dd"
            print(f"\nRUN {run_num}/{total_runs}: {config['label']} | {dd_label}")
            print(f"  train_days={config['train_days']}  test_days={config['test_days']}  drawdown={drawdown}")
            print("=" * 70)

            run_rolling_walk_forward(
                slippage_pct     = slippage_pct,
                spread_pct       = spread_pct,
                max_drawdown_pct = drawdown,
                train_days       = config["train_days"],
                test_days        = config["test_days"],
                max_workers      = max_workers,
                reopt_label      = config["label"],
                optimizer_run_id = opt_run_id,
                history          = history,
            )

    # Print final ranked summary
    _print_summary(opt_run_id, start_time)


def _print_summary(opt_run_id: int, start_time: datetime):
    results = get_optimizer_results(opt_run_id)

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'='*120}")
    print(f"RWF OPTIMIZER RESULTS  (completed in {elapsed}s)")
    print(f"{'='*120}\n")

    if results.empty:
        print("No results found.")
        return

    # Compute derived columns
    results["return_%"]    = ((results["final_balance"] - STARTING_CASH) / STARTING_CASH * 100).round(1)
    results["bench_%"]     = ((results["final_benchmark"] - STARTING_CASH) / STARTING_CASH * 100).round(1)
    results["delta_%"]     = (results["return_%"] - results["bench_%"]).round(1)
    results["beat_%"]      = (results["beat_count"] / results["windows"] * 100).round(1)
    results["profit_%"]    = (results["profitable_qtrs"] / results["windows"] * 100).round(1)
    results["drawdown_threshold"] = results["drawdown_threshold"].fillna("none")

    header = (
        f"{'Rank':>4}  {'Config':<25}  {'DD':>6}  {'Train':>5}  {'Test':>4}  "
        f"{'Final$':>10}  {'Ret%':>6}  {'Bench%':>7}  {'Delta%':>7}  "
        f"{'Beat%':>6}  {'Prof%':>6}  {'Worst Q':>8}  {'AvgQ%':>6}"
    )
    sep = "-" * 120
    print(header)
    print(sep)

    for rank, (_, row) in enumerate(results.iterrows(), 1):
        print(
            f"{rank:>4}  {row['reopt_label']:<25}  {str(row['drawdown_threshold']):>6}  "
            f"{int(row['train_days']):>5}  {int(row['test_days']):>4}  "
            f"${row['final_balance']:>9,.2f}  {row['return_%']:>+6.1f}%  "
            f"{row['bench_%']:>+6.1f}%  {row['delta_%']:>+6.1f}pp  "
            f"{row['beat_%']:>5.1f}%  {row['profit_%']:>5.1f}%  "
            f"{row['worst_quarter']:>+7.2f}%  {row['avg_test_return']:>+5.2f}%"
        )

    print(sep)
    best = results.iloc[0]
    print(f"\nWINNER: {best['reopt_label']}  |  drawdown={best['drawdown_threshold']}  "
          f"|  $5,000 → ${best['final_balance']:,.2f}  ({best['return_%']:+.1f}%)")


def cli_main(argv=None):
    parser = argparse.ArgumentParser(description="Rolling walk-forward optimizer (45-run matrix)")
    parser.add_argument("--slippage", default=0.0005, type=float, help="Slippage per side (default: 0.0005)")
    parser.add_argument("--spread",   default=0.0003, type=float, help="Spread per side (default: 0.0003)")
    parser.add_argument("--workers",  default=None,   type=int,   help="Worker processes (default: cpu_count - 2)")
    parser.add_argument("--name",     default="",                  help="Run name")
    parser.add_argument("--notes",    default="",                  help="Notes")
    args = parser.parse_args(argv)

    run_optimizer(
        slippage_pct = args.slippage,
        spread_pct   = args.spread,
        max_workers  = args.workers,
        name         = args.name,
        notes        = args.notes,
    )


if __name__ == "__main__":
    cli_main()
