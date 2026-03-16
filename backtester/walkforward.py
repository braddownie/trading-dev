#!/usr/bin/env python3
"""
Walk-forward analysis — trains a grid search on Year 1, then tests the top 3
parameter combos out-of-sample on Year 2.

Usage:
    python -m backtester.walkforward [options]
    python main.py walkforward [options]

DB storage:
    Train results : rank 1–300  (full grid)
    Test results  : rank 1001–1003  (top 3 combos only)
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher import get_sp500_tickers
from backtester.backtest import (
    load_history, compute_daily_scans, run_grid_search, run_simulation, STARTING_CASH,
)
from backtester.db import init_db, save_run, save_results, save_equity_curves

TOP_N                 = 3
TRADING_DAYS_PER_YEAR = 252


def run_walk_forward(
    train_years:  int   = 1,
    test_years:   int   = 1,
    slippage_pct: float = 0.0,
    spread_pct:   float = 0.0,
    max_workers:  int   = None,
    name:         str   = "",
    notes:        str   = "",
):
    start_time = datetime.now()
    print(f"Walk-forward analysis started — {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    init_db()

    # 1. Universe
    print("Fetching ticker universe (S&P 500 only)...")
    tickers = get_sp500_tickers()
    print(f"  {len(tickers)} tickers\n")

    # 2. History — extra year of buffer for rolling metric warmup
    period  = f"{train_years + test_years + 1}y"
    history = load_history(tickers, period=period)
    print(f"  {history['ticker'].nunique()} tickers with valid data\n")

    # 3. Split into non-overlapping train / test windows
    all_dates  = sorted(history["date"].unique())
    train_days = train_years * TRADING_DAYS_PER_YEAR
    test_days  = test_years  * TRADING_DAYS_PER_YEAR
    needed     = train_days + test_days

    if len(all_dates) < needed:
        print(f"ERROR: only {len(all_dates)} trading days available, need {needed}.")
        sys.exit(1)

    sim_dates_all = all_dates[-needed:]
    train_dates   = sim_dates_all[:train_days]
    test_dates    = sim_dates_all[train_days:]

    train_start = str(train_dates[0])[:10]
    train_end   = str(train_dates[-1])[:10]
    test_start  = str(test_dates[0])[:10]
    test_end    = str(test_dates[-1])[:10]

    print(f"Train period : {train_start} → {train_end}  ({len(train_dates)} days)")
    print(f"Test period  : {test_start} → {test_end}  ({len(test_dates)} days)\n")

    # 4. Pre-compute daily scans for both windows
    print("Pre-computing daily scans for train period...")
    train_scans = compute_daily_scans(history, sim_dates=train_dates, max_workers=max_workers)
    print(f"  {len(train_scans)} days ready")

    print("Pre-computing daily scans for test period...")
    test_scans = compute_daily_scans(history, sim_dates=test_dates, max_workers=max_workers)
    print(f"  {len(test_scans)} days ready\n")

    # 5. Grid search on train window
    print("--- TRAIN: Grid search ---")
    train_df, train_curves = run_grid_search(
        train_scans,
        slippage_pct=slippage_pct,
        spread_pct=spread_pct,
        max_workers=max_workers,
    )

    # 6. Test top 3 params out-of-sample
    print(f"\n--- TEST: Top {TOP_N} param combos out-of-sample ---\n")
    top_rows   = train_df.head(TOP_N)
    top_params = top_rows[
        ["min_rel_volume", "min_change_pct", "take_profit_pct", "stop_loss_pct"]
    ].to_dict("records")

    test_results = []
    test_curves  = []
    for params in top_params:
        result = run_simulation(test_scans, params, slippage_pct, spread_pct)
        test_curves.append(result.pop("_equity_curve"))
        test_results.append(result)

    # 7. Print comparison table
    sep = "-" * 68
    print(f"{'#':<4} {'min_rel_vol':>11}  {'min_chg%':>8}  {'tp%':>5}  {'sl%':>6}  {'Train%':>8}  {'Test%':>8}  {'Held up?':>8}")
    print(sep)
    for i, (params, test_r) in enumerate(zip(top_params, test_results), 1):
        train_ret = top_rows.iloc[i - 1]["total_return_%"]
        test_ret  = test_r["total_return_%"]
        held_up   = "YES" if test_ret > 0 else "NO"
        print(
            f"{i:<4} {params['min_rel_volume']:>11}  {params['min_change_pct']:>8}  "
            f"{params['take_profit_pct']:>5}  {params['stop_loss_pct']:>6}  "
            f"{train_ret:>+8.2f}%  {test_ret:>+8.2f}%  {held_up:>8}"
        )

    # 8. Save to DB
    run_name = name or f"wf_{train_years}y_{test_years}y_{start_time.strftime('%Y%m%d_%H%M%S')}"
    run_id   = save_run(
        name          = run_name,
        run_type      = "walk_forward",
        train_start   = train_start,
        train_end     = train_end,
        test_start    = test_start,
        test_end      = test_end,
        starting_cash = STARTING_CASH,
        slippage_pct  = slippage_pct,
        spread_pct    = spread_pct,
        notes         = notes,
    )

    # Train results — full grid (ranks 1–300)
    train_dicts = train_df.to_dict("records")
    train_ids   = save_results(run_id, train_dicts)
    for result_id, curve in zip(train_ids, train_curves):
        save_equity_curves(run_id, result_id, curve)

    # Test results — top 3 only (ranks 1001–1003 to distinguish from train grid)
    for i, (result, curve) in enumerate(zip(test_results, test_curves), 1):
        test_ids = save_results(run_id, [{**result, "rank": 1000 + i}])
        save_equity_curves(run_id, test_ids[0], curve)

    elapsed = (datetime.now() - start_time).seconds
    print(f"\nCompleted in {elapsed}s")
    print(f"DB run id : {run_id}  ({run_name})")


def cli_main(argv=None):
    parser = argparse.ArgumentParser(description="Walk-forward analysis")
    parser.add_argument("--train-years", default=1,    type=int,   help="Training window in years (default: 1)")
    parser.add_argument("--test-years",  default=1,    type=int,   help="Test window in years (default: 1)")
    parser.add_argument("--slippage",    default=0.0,  type=float, help="Slippage %% per side (default: 0.0)")
    parser.add_argument("--spread",      default=0.0,  type=float, help="Spread %% per side (default: 0.0)")
    parser.add_argument("--workers",     default=None, type=int,   help="Worker processes (default: cpu_count - 2)")
    parser.add_argument("--name",        default="",               help="Run name")
    parser.add_argument("--notes",       default="",               help="Notes")
    args = parser.parse_args(argv)

    run_walk_forward(
        train_years  = args.train_years,
        test_years   = args.test_years,
        slippage_pct = args.slippage,
        spread_pct   = args.spread,
        max_workers  = args.workers,
        name         = args.name,
        notes        = args.notes,
    )


if __name__ == "__main__":
    cli_main()
