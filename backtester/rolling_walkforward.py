#!/usr/bin/env python3
"""
Rolling walk-forward analysis.

Slides a 1-year train / 1-quarter test window across all available history
(~2015 to present), shifting by one quarter each iteration.

For each window:
  1. Grid-search the train year to find the best params
  2. Run those params out-of-sample on the test quarter
  3. Apply the test return to a compounding $5,000 portfolio

Produces a quarter-by-quarter table and a final summary comparing the
strategy's compounded return against a 7% annual benchmark.

Usage:
    python -m backtester.rolling_walkforward [options]
    python main.py rolling-walkforward [options]
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetcher import get_sp500_tickers
from backtester.backtest import (
    load_history, compute_all_scan_lookbacks, run_grid_search, run_simulation, STARTING_CASH,
)
from backtester.db import init_db, save_rolling_wf_run, save_rolling_wf_window

ANNUAL_BENCH = 0.07  # 7% annual benchmark


def quarterly_bench_rate(annual: float = ANNUAL_BENCH, test_days: int = 63) -> float:
    """Convert annual benchmark rate to per-test-period rate."""
    periods_per_year = 252 / test_days
    return (1 + annual) ** (1 / periods_per_year) - 1


def generate_windows(all_dates: list, train_days: int, test_days: int) -> list[tuple]:
    """
    Slide train/test windows across all_dates, shifting by test_days each iteration.
    Returns list of (train_dates, test_dates) tuples.
    """
    windows = []
    start = 0
    while True:
        train_end = start + train_days
        test_end  = train_end + test_days
        if test_end > len(all_dates):
            break
        windows.append((all_dates[start:train_end], all_dates[train_end:test_end]))
        start += test_days
    return windows


def run_rolling_walk_forward(
    slippage_pct:      float = 0.0005,
    spread_pct:        float = 0.0003,
    max_drawdown_pct:  float = None,
    train_days:        int   = 252,
    test_days:         int   = 63,
    max_workers:       int   = None,
    name:              str   = "",
    notes:             str   = "",
    reopt_label:       str   = "",
    optimizer_run_id:  int   = None,
    history:           object = None,  # pass pre-loaded history to avoid re-downloading
):
    start_time = datetime.now()
    label = reopt_label or name or "rwf"
    dd_label = f"dd{max_drawdown_pct}" if max_drawdown_pct is not None else "no_dd"
    print(f"Rolling walk-forward [{label} | {dd_label}] started — {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    init_db()

    # 1. Universe + full history (reuse if passed in)
    if history is None:
        print("Fetching ticker universe (S&P 500 only)...")
        tickers = get_sp500_tickers()
        print(f"  {len(tickers)} tickers\n")
        print("Downloading full history (max, capped to 2015-01-01 onward)...")
        history = load_history(tickers, period="max")
        history = history[history["date"] >= pd.Timestamp("2015-01-01")].copy()
        print(f"  {history['ticker'].nunique()} tickers with valid data\n")

    all_dates  = sorted(history["date"].unique())
    bench_rate = quarterly_bench_rate(ANNUAL_BENCH, test_days)
    print(f"  Date range : {str(all_dates[0])[:10]} → {str(all_dates[-1])[:10]}  ({len(all_dates)} trading days)")
    print(f"  Train days : {train_days}  |  Test days : {test_days}  |  Bench/period: {bench_rate*100:.3f}%")

    # 2. Generate windows
    windows = generate_windows(all_dates, train_days, test_days)
    if not windows:
        print("ERROR: not enough history to generate any windows.")
        sys.exit(1)
    print(f"  Windows    : {len(windows)}\n")

    # 3. Save parent run record
    run_name = name or f"rwf_{label}_{dd_label}_{start_time.strftime('%Y%m%d_%H%M%S')}"
    run_id = save_rolling_wf_run(
        name               = run_name,
        train_days         = train_days,
        test_days          = test_days,
        slippage_pct       = slippage_pct,
        spread_pct         = spread_pct,
        starting_cash      = STARTING_CASH,
        notes              = notes,
        drawdown_threshold = max_drawdown_pct,
        reopt_label        = reopt_label,
        optimizer_run_id   = optimizer_run_id,
    )

    # 4. Process each window sequentially
    strategy_balance  = STARTING_CASH
    benchmark_balance = STARTING_CASH
    window_rows = []

    for i, (train_dates, test_dates) in enumerate(windows, 1):
        train_start = str(train_dates[0])[:10]
        train_end   = str(train_dates[-1])[:10]
        test_start  = str(test_dates[0])[:10]
        test_end    = str(test_dates[-1])[:10]

        print(f"{'='*60}")
        print(f"Window {i}/{len(windows)}  train {train_start}→{train_end}  test {test_start}→{test_end}")

        # Train: compute all lookback scans + grid search
        print(f"  Pre-computing train scans...")
        train_scans = compute_all_scan_lookbacks(history, sim_dates=train_dates, max_workers=max_workers)
        train_df, _ = run_grid_search(
            train_scans,
            slippage_pct     = slippage_pct,
            spread_pct       = spread_pct,
            max_drawdown_pct = max_drawdown_pct,
            max_workers      = max_workers,
        )
        best_row    = train_df.iloc[0]
        best_params = {
            "min_rel_volume":   best_row["min_rel_volume"],
            "min_change_pct":   best_row["min_change_pct"],
            "take_profit_pct":  best_row["take_profit_pct"],
            "stop_loss_pct":    best_row["stop_loss_pct"],
            "max_position_pct": best_row["max_position_pct"],
            "vol_lookback":     int(best_row["vol_lookback"]),
        }
        train_return = best_row["total_return_%"]

        # Test: run best params out-of-sample
        print(f"  Pre-computing test scans...")
        test_scans  = compute_all_scan_lookbacks(history, sim_dates=test_dates, max_workers=max_workers)
        test_result = run_simulation(
            test_scans[best_params["vol_lookback"]], best_params,
            slippage_pct, spread_pct, max_drawdown_pct,
        )
        test_result.pop("_equity_curve", None)
        test_return = test_result["total_return_%"]

        # Compound balances
        strategy_balance  = round(strategy_balance  * (1 + test_return / 100), 2)
        benchmark_balance = round(benchmark_balance * (1 + bench_rate), 2)
        beat              = test_return > (bench_rate * 100)

        # Save window
        save_rolling_wf_window(
            run_id            = run_id,
            window_num        = i,
            train_start       = train_start,
            train_end         = train_end,
            test_start        = test_start,
            test_end          = test_end,
            best_params_json  = json.dumps(best_params),
            train_return_pct  = train_return,
            test_return_pct   = test_return,
            strategy_balance  = strategy_balance,
            benchmark_balance = benchmark_balance,
            beat_benchmark    = int(beat),
        )

        window_rows.append({
            "window":     i,
            "test_start": test_start,
            "test_end":   test_end,
            "params":     (f"{best_params['min_rel_volume']}/{best_params['min_change_pct']}/"
                           f"{best_params['take_profit_pct']}/{best_params['stop_loss_pct']}/"
                           f"{best_params['max_position_pct']}/{best_params['vol_lookback']}"),
            "train_ret":  train_return,
            "test_ret":   test_return,
            "strat_bal":  strategy_balance,
            "bench_bal":  benchmark_balance,
            "beat":       "YES" if beat else "NO",
        })

        print(
            f"  Best params : rv={best_params['min_rel_volume']} chg={best_params['min_change_pct']}% "
            f"tp={best_params['take_profit_pct']}% sl={best_params['stop_loss_pct']}% "
            f"maxpos={best_params['max_position_pct']} vol_lb={best_params['vol_lookback']}\n"
            f"  Train {train_return:+.2f}%  |  Test {test_return:+.2f}%  |  "
            f"Strategy ${strategy_balance:,.2f}  Benchmark ${benchmark_balance:,.2f}  Beat: {'YES' if beat else 'NO'}"
        )

    # 5. Print full summary
    _print_summary(window_rows, STARTING_CASH)

    elapsed = (datetime.now() - start_time).seconds
    print(f"Completed in {elapsed}s")
    print(f"DB run id : {run_id}  ({run_name})")


def _print_summary(rows: list[dict], starting_cash: float):
    sep = "-" * 108

    print(f"\n{'='*108}")
    print("ROLLING WALK-FORWARD SUMMARY")
    print(f"{'='*108}\n")

    print(f"{'Win':>3}  {'Test Quarter':<23}  {'rv/chg/tp/sl':>20}  {'Train%':>7}  {'Test%':>7}  {'Strategy$':>11}  {'Benchmark$':>11}  {'Beat?':>5}")
    print(sep)

    for r in rows:
        period = f"{r['test_start']}→{r['test_end']}"
        print(
            f"{r['window']:>3}  {period:<23}  {r['params']:>20}  "
            f"{r['train_ret']:>+7.2f}%  {r['test_ret']:>+7.2f}%  "
            f"${r['strat_bal']:>10,.2f}  ${r['bench_bal']:>10,.2f}  {r['beat']:>5}"
        )

    print(sep)

    test_rets   = [r["test_ret"] for r in rows]
    profitable  = sum(1 for x in test_rets if x > 0)
    beat_count  = sum(1 for r in rows if r["beat"] == "YES")
    final_strat = rows[-1]["strat_bal"]
    final_bench = rows[-1]["bench_bal"]
    strat_total = (final_strat - starting_cash) / starting_cash * 100
    bench_total = (final_bench - starting_cash) / starting_cash * 100

    # Max drawdown on strategy balance
    peak   = starting_cash
    max_dd = 0.0
    for r in rows:
        peak   = max(peak, r["strat_bal"])
        dd     = (r["strat_bal"] - peak) / peak * 100
        max_dd = min(max_dd, dd)

    print(f"""
FINAL RESULTS
  Strategy  : ${starting_cash:,.0f} → ${final_strat:,.2f}  ({strat_total:+.1f}%)
  Benchmark : ${starting_cash:,.0f} → ${final_bench:,.2f}  ({bench_total:+.1f}%)  [7% annual]
  Delta     : ${final_strat - final_bench:+,.2f}  ({strat_total - bench_total:+.1f} percentage points)

QUARTERLY STATS ({len(rows)} windows)
  Profitable quarters     : {profitable}/{len(rows)}  ({profitable/len(rows)*100:.1f}%)
  Beat benchmark          : {beat_count}/{len(rows)}  ({beat_count/len(rows)*100:.1f}%)
  Best quarter            : {max(test_rets):+.2f}%
  Worst quarter           : {min(test_rets):+.2f}%
  Average quarterly return: {sum(test_rets)/len(test_rets):+.2f}%
  Max drawdown (strategy) : {max_dd:.2f}%
""")


def cli_main(argv=None):
    parser = argparse.ArgumentParser(description="Rolling walk-forward analysis")
    parser.add_argument("--slippage",   default=0.0005, type=float, help="Slippage per side (default: 0.0005)")
    parser.add_argument("--spread",     default=0.0003, type=float, help="Spread per side (default: 0.0003)")
    parser.add_argument("--drawdown",   default=None,   type=float, help="Max drawdown %% before suppressing buys (default: None)")
    parser.add_argument("--train-days", default=252,    type=int,   help="Training window in trading days (default: 252)")
    parser.add_argument("--test-days",  default=63,     type=int,   help="Test window in trading days (default: 63)")
    parser.add_argument("--workers",    default=None,   type=int,   help="Worker processes (default: cpu_count - 2)")
    parser.add_argument("--name",       default="",                  help="Run name")
    parser.add_argument("--notes",      default="",                  help="Notes")
    args = parser.parse_args(argv)

    run_rolling_walk_forward(
        slippage_pct     = args.slippage,
        spread_pct       = args.spread,
        max_drawdown_pct = args.drawdown,
        train_days       = args.train_days,
        test_days        = args.test_days,
        max_workers      = args.workers,
        name             = args.name,
        notes            = args.notes,
    )


if __name__ == "__main__":
    cli_main()
