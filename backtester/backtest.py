#!/usr/bin/env python3
"""
Backtester — replays market history and grid-searches strategy parameters
to find the most profitable configuration.

Results saved to SQLite DB at backtester/results/trading.db
CSV export also written for each run.
"""
import os
import sys
import itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

import pandas as pd
import yfinance as yf

# Allow imports from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from fetcher import get_sp500_tickers
from backtester.db import init_db, save_run, save_results, save_equity_curves

STARTING_CASH = 5000.0
MIN_PRICE     = 5.0
MIN_AVG_VOL   = 500_000

# --- Parameter grid ---
PARAM_GRID = {
    "min_rel_volume":   [1.0, 1.25, 1.5, 1.75, 2.0],
    "min_change_pct":   [0.0, 0.5, 1.0],
    "take_profit_pct":  [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0],
    "stop_loss_pct":    [-1.0, -2.0, -3.0, -5.0, -7.0, -10.0],
    "max_position_pct": [0.10, 0.15, 0.20],
    "vol_lookback":     [10, 20, 30, 60],
}


# --- Data loading ---

def load_history(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Download historical OHLCV data. Returns tidy DataFrame."""
    print(f"Downloading historical data ({period})...")
    raw = yf.download(
        tickers,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    rows = []
    for ticker in tickers:
        try:
            df = raw[ticker].dropna() if len(tickers) > 1 else raw.dropna()
            if len(df) < 10:
                continue
            for date, row in df.iterrows():
                rows.append({
                    "date":   date,
                    "ticker": ticker,
                    "open":   row["Open"],
                    "high":   row["High"],
                    "low":    row["Low"],
                    "close":  row["Close"],
                    "volume": row["Volume"],
                })
        except Exception:
            continue

    return pd.DataFrame(rows)


# --- Per-day scan ---

# Module-level globals for scan worker processes (required for pickling)
_worker_history = None

def _init_scan_worker(history: pd.DataFrame):
    global _worker_history
    _worker_history = history

def _scan_one_date(args: tuple) -> tuple:
    """Compute scan DataFrame for a single date and vol_lookback. Runs in worker process."""
    sim_date, vol_lookback = args
    rows = []
    for ticker, grp in _worker_history[_worker_history["date"] <= sim_date].groupby("ticker"):
        grp = grp.sort_values("date")
        if len(grp) < 2:
            continue

        today      = grp.iloc[-1]
        prev       = grp.iloc[-2]
        price      = today["close"]
        prev_close = prev["close"]

        if price < MIN_PRICE:
            continue

        vol_today = today["volume"]
        avg_vol   = grp["volume"].iloc[-(vol_lookback + 1):-1].mean()

        if avg_vol < MIN_AVG_VOL:
            continue

        rel_volume = round(vol_today / avg_vol, 2) if avg_vol > 0 else 0
        change_pct = round(((price - prev_close) / prev_close) * 100, 2)

        highs  = grp["high"].iloc[-15:]
        lows   = grp["low"].iloc[-15:]
        closes = grp["close"].iloc[-15:]
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift()).abs(),
            (lows - closes.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = round(tr.iloc[-14:].mean(), 2)

        rows.append({
            "ticker":     ticker,
            "price":      round(price, 2),
            "change_%":   change_pct,
            "rel_volume": rel_volume,
            "atr_14":     atr,
        })

    if rows:
        return sim_date, (
            pd.DataFrame(rows)
            .sort_values("rel_volume", ascending=False)
            .reset_index(drop=True)
        )
    return sim_date, None


def compute_daily_scans(
    history: pd.DataFrame,
    simulation_days: int = 252,
    sim_dates: list = None,
    vol_lookback: int = 30,
    max_workers: int = None,
) -> dict:
    """
    Pre-compute a scan DataFrame for each simulation day at a given vol_lookback.
    Returns {date: scan_df}.
    """
    all_dates = sorted(history["date"].unique())
    if sim_dates is None:
        sim_dates = all_dates[-simulation_days:]

    workers = max_workers or max(1, (os.cpu_count() or 2) - 2)
    print(f"  Running across {workers} workers (vol_lookback={vol_lookback})...")

    daily_scans = {}
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_scan_worker,
        initargs=(history,),
    ) as pool:
        for sim_date, scan_df in pool.map(_scan_one_date, [(d, vol_lookback) for d in sim_dates]):
            if scan_df is not None:
                daily_scans[sim_date] = scan_df

    return daily_scans


def compute_all_scan_lookbacks(
    history: pd.DataFrame,
    sim_dates: list,
    max_workers: int = None,
) -> dict:
    """
    Pre-compute scan dicts for every vol_lookback value in PARAM_GRID.
    Returns {vol_lookback: {date: scan_df}}.
    Computed once per window, reused across all 8,640 param combos.
    """
    result = {}
    for lb in PARAM_GRID["vol_lookback"]:
        print(f"  Pre-computing scans for vol_lookback={lb}...")
        result[lb] = compute_daily_scans(history, sim_dates=sim_dates, vol_lookback=lb, max_workers=max_workers)
    return result


# --- In-memory portfolio ---

class BacktestPortfolio:
    def __init__(self, slippage_pct: float = 0.0, spread_pct: float = 0.0, max_position_pct: float = 0.20):
        self.cash            = STARTING_CASH
        self.positions       = {}
        self.slippage_pct    = slippage_pct
        self.spread_pct      = spread_pct
        self.max_position_pct = max_position_pct

    def portfolio_value(self, prices: dict) -> float:
        return self.cash + sum(
            pos["shares"] * prices.get(t, pos["avg_cost"])
            for t, pos in self.positions.items()
        )

    def _size(self, price: float, atr: float) -> float:
        pv       = self.portfolio_value({})
        atr_pct  = atr / price if price > 0 else 0.05
        fraction = min(self.max_position_pct, max(0.05, 0.01 / atr_pct))
        dollars  = min(pv * fraction, self.cash)
        return round(dollars / price, 6)

    def buy(self, ticker: str, price: float, atr: float) -> bool:
        fill_price = round(price * (1 + self.slippage_pct + self.spread_pct / 2), 4)
        shares     = self._size(fill_price, atr)
        cost       = round(shares * fill_price, 4)
        if cost < 0.01 or self.cash < cost:
            return False
        if ticker in self.positions:
            ex    = self.positions[ticker]
            total = ex["shares"] + shares
            avg   = ((ex["shares"] * ex["avg_cost"]) + cost) / total
            self.positions[ticker] = {"shares": round(total, 6), "avg_cost": round(avg, 4)}
        else:
            self.positions[ticker] = {"shares": shares, "avg_cost": fill_price}
        self.cash = round(self.cash - cost, 4)
        return True

    def sell(self, ticker: str, price: float) -> float:
        if ticker not in self.positions:
            return 0.0
        fill_price = round(price * (1 - self.slippage_pct - self.spread_pct / 2), 4)
        pos        = self.positions.pop(ticker)
        proceeds   = round(pos["shares"] * fill_price, 4)
        pnl        = round(proceeds - (pos["shares"] * pos["avg_cost"]), 4)
        self.cash  = round(self.cash + proceeds, 4)
        return pnl


# --- Single simulation run ---

def run_simulation(
    daily_scans: dict,
    params: dict,
    slippage_pct: float = 0.0,
    spread_pct: float = 0.0,
    max_drawdown_pct: float = None,
) -> dict:
    min_rel_vol      = params["min_rel_volume"]
    min_chg          = params["min_change_pct"]
    take_profit      = params["take_profit_pct"]
    stop_loss        = params["stop_loss_pct"]
    max_position_pct = params.get("max_position_pct", 0.20)

    if not daily_scans:
        return {
            **params,
            "total_return_%": 0.0,
            "final_value":    STARTING_CASH,
            "realized_pnl":   0.0,
            "total_trades":   0,
            "wins":           0,
            "losses":         0,
            "win_rate_%":     0.0,
            "_equity_curve":  [],
        }

    portfolio    = BacktestPortfolio(slippage_pct, spread_pct, max_position_pct)
    peak_value   = STARTING_CASH
    total_trades = 0
    wins         = 0
    losses       = 0
    total_pnl    = 0.0
    equity_curve = []

    for date, scan in sorted(daily_scans.items()):
        prices        = dict(zip(scan["ticker"], scan["price"]))
        current_value = portfolio.portfolio_value(prices)
        peak_value    = max(peak_value, current_value)

        # Exits — always process regardless of drawdown state
        for ticker in list(portfolio.positions.keys()):
            price = prices.get(ticker)
            if price is None:
                continue
            pos     = portfolio.positions[ticker]
            pnl_pct = ((price - pos["avg_cost"]) / pos["avg_cost"]) * 100
            if pnl_pct >= take_profit or pnl_pct <= stop_loss:
                pnl = portfolio.sell(ticker, price)
                total_pnl    += pnl
                total_trades += 1
                wins   += pnl >= 0
                losses += pnl < 0

        # Entries — suppressed when portfolio is in drawdown
        in_drawdown = (
            max_drawdown_pct is not None and
            peak_value > 0 and
            (current_value - peak_value) / peak_value * 100 < -max_drawdown_pct
        )
        if not in_drawdown:
            for _, row in scan.iterrows():
                ticker = row["ticker"]
                if ticker in portfolio.positions:
                    continue
                if row["rel_volume"] >= min_rel_vol and row["change_%"] > min_chg:
                    portfolio.buy(ticker, row["price"], row["atr_14"])

        # Snapshot equity
        equity_curve.append({
            "date":            str(date)[:10],
            "portfolio_value": round(portfolio.portfolio_value(prices), 4),
        })

    # Liquidate remaining positions
    last_prices = dict(zip(
        list(daily_scans.values())[-1]["ticker"],
        list(daily_scans.values())[-1]["price"],
    ))
    for ticker in list(portfolio.positions.keys()):
        price = last_prices.get(ticker, portfolio.positions[ticker]["avg_cost"])
        pnl   = portfolio.sell(ticker, price)
        total_pnl    += pnl
        total_trades += 1
        wins   += pnl >= 0
        losses += pnl < 0

    final_value  = portfolio.portfolio_value({})
    total_return = round(((final_value - STARTING_CASH) / STARTING_CASH) * 100, 4)
    win_rate     = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0

    equity_curve.append({
        "date":            equity_curve[-1]["date"] if equity_curve else "",
        "portfolio_value": round(final_value, 4),
    })

    return {
        **params,
        "total_return_%": total_return,
        "final_value":    round(final_value, 4),
        "realized_pnl":   round(total_pnl, 4),
        "total_trades":   total_trades,
        "wins":           wins,
        "losses":         losses,
        "win_rate_%":     win_rate,
        "_equity_curve":  equity_curve,
    }


# --- Grid search ---

def _run_combo(args: tuple) -> dict:
    """Top-level worker function (required for ProcessPoolExecutor pickling)."""
    daily_scans, params, slippage_pct, spread_pct, max_drawdown_pct = args
    return run_simulation(daily_scans, params, slippage_pct, spread_pct, max_drawdown_pct)


def run_grid_search(
    scans_by_lookback: dict,
    slippage_pct: float = 0.0,
    spread_pct: float = 0.0,
    max_drawdown_pct: float = None,
    max_workers: int = None,
) -> tuple[pd.DataFrame, list[list[dict]]]:
    """
    Run grid search across all param combinations.
    scans_by_lookback: {vol_lookback: {date: scan_df}} — from compute_all_scan_lookbacks().
    Returns (results_df, equity_curves).
    """
    keys   = list(PARAM_GRID.keys())
    combos = [dict(zip(keys, values)) for values in itertools.product(*PARAM_GRID.values())]
    total   = len(combos)
    workers = max_workers or max(1, (os.cpu_count() or 2) - 2)

    print(f"Running {total} parameter combinations across {workers} workers...\n")

    work = [
        (scans_by_lookback[params["vol_lookback"]], params, slippage_pct, spread_pct, max_drawdown_pct)
        for params in combos
    ]

    indexed_results = [None] * total
    done = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_combo, args): i for i, args in enumerate(work)}
        for future in as_completed(futures):
            i = futures[future]
            indexed_results[i] = future.result()
            done += 1
            if done % 500 == 0 or done == total:
                print(f"  {done}/{total} complete...")

    # Sort by return, reorder equity curves to match
    df = pd.DataFrame(indexed_results).sort_values("total_return_%", ascending=False)
    sorted_indices = df.index.tolist()
    equity_curves  = [indexed_results[i].get("_equity_curve", []) for i in sorted_indices]

    df = df.reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    # Strip equity curve from df
    if "_equity_curve" in df.columns:
        df = df.drop(columns=["_equity_curve"])

    return df, equity_curves


# --- Main ---

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run backtest grid search")
    parser.add_argument("--period",   default="2y",  help="yfinance period (default: 2y)")
    parser.add_argument("--days",     default=252,   type=int, help="Simulation days (default: 252)")
    parser.add_argument("--name",     default="",    help="Run name (default: auto-generated)")
    parser.add_argument("--slippage", default=0.0,   type=float, help="Slippage per side (default: 0.0)")
    parser.add_argument("--spread",   default=0.0,   type=float, help="Spread per side (default: 0.0)")
    parser.add_argument("--drawdown", default=None,  type=float, help="Max drawdown %% before suppressing buys (default: None)")
    parser.add_argument("--notes",    default="",    help="Notes to store with this run")
    parser.add_argument("--workers",  default=None,  type=int, help="Worker processes (default: cpu_count - 2)")
    args = parser.parse_args()

    start_time = datetime.now()
    print(f"Backtester started — {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    init_db()

    # 1. Universe
    print("Fetching ticker universe (S&P 500 only)...")
    tickers = get_sp500_tickers()
    print(f"  {len(tickers)} tickers\n")

    # 2. History
    history = load_history(tickers, period=args.period)
    all_dates   = sorted(history["date"].unique())
    train_start = str(all_dates[0])[:10]
    train_end   = str(all_dates[-1])[:10]
    print(f"  {history['ticker'].nunique()} tickers with valid data\n")

    # 3. Daily scans — one set per vol_lookback
    sim_dates = all_dates[-args.days:]
    print(f"Pre-computing daily scans for {len(sim_dates)} simulation days (4 vol lookbacks)...")
    scans_by_lookback = compute_all_scan_lookbacks(history, sim_dates, max_workers=args.workers)
    print()

    # 4. Grid search
    results_df, equity_curves = run_grid_search(
        scans_by_lookback,
        slippage_pct    = args.slippage,
        spread_pct      = args.spread,
        max_drawdown_pct= args.drawdown,
        max_workers     = args.workers,
    )

    # 5. Save to DB
    run_name = args.name or f"grid_{args.days}d_{start_time.strftime('%Y%m%d_%H%M%S')}"
    run_id   = save_run(
        name          = run_name,
        run_type      = "grid",
        train_start   = train_start,
        train_end     = train_end,
        test_start    = None,
        test_end      = None,
        starting_cash = STARTING_CASH,
        slippage_pct  = args.slippage,
        spread_pct    = args.spread,
        notes         = args.notes,
    )
    result_dicts = results_df.to_dict("records")
    result_ids   = save_results(run_id, result_dicts)
    for result_id, curve in zip(result_ids, equity_curves):
        save_equity_curves(run_id, result_id, curve)

    # 6. Export CSV
    csv_path = Path(__file__).parent / "results" / f"{run_name}.csv"
    results_df.to_csv(csv_path, index=False)

    # 7. Print summary
    elapsed = (datetime.now() - start_time).seconds
    print(f"\nCompleted in {elapsed}s")
    print(f"DB run id : {run_id}  ({run_name})")
    print(f"CSV       : {csv_path}\n")
    print("Top 10 parameter combinations:\n")
    cols = ["rank", "min_rel_volume", "min_change_pct", "take_profit_pct",
            "stop_loss_pct", "max_position_pct", "vol_lookback", "total_return_%", "win_rate_%", "total_trades"]
    print(results_df.head(10)[cols].to_string(index=False))

    print(f"\nBest configuration:")
    best = results_df.iloc[0]
    print(f"  min_rel_volume  : {best['min_rel_volume']}")
    print(f"  min_change_pct  : {best['min_change_pct']}%")
    print(f"  take_profit_pct : {best['take_profit_pct']}%")
    print(f"  stop_loss_pct   : {best['stop_loss_pct']}%")
    print(f"  max_position_pct: {best['max_position_pct']}")
    print(f"  vol_lookback    : {best['vol_lookback']} days")
    print(f"  total_return    : {best['total_return_%']:+.4f}%")
    print(f"  win_rate        : {best['win_rate_%']}%")
    print(f"  trades          : {int(best['total_trades'])}")
