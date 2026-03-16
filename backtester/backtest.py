#!/usr/bin/env python3
"""
Backtester — replays 30 days of market history and grid-searches strategy
parameters to find the most profitable configuration.

Results saved to backtester/results/results.csv
"""
import sys
import itertools
from pathlib import Path
from datetime import datetime

import pandas as pd
import yfinance as yf

# Allow imports from parent directory (fetcher, etc.)
sys.path.insert(0, str(Path(__file__).parent.parent))
from fetcher import get_universe

RESULTS_FILE = Path(__file__).parent / "results" / "results_1y.csv"
STARTING_CASH = 100.0
MIN_PRICE     = 5.0
MIN_AVG_VOL   = 500_000

# --- Parameter grid ---
PARAM_GRID = {
    "min_rel_volume":  [1.0, 1.25, 1.5, 1.75, 2.0],
    "min_change_pct":  [0.0, 0.5, 1.0],
    "take_profit_pct": [1.0, 2.0, 3.0, 5.0, 7.0],
    "stop_loss_pct":   [-1.0, -2.0, -3.0, -5.0],
}


# --- Data loading ---

def load_history(tickers: list[str]) -> pd.DataFrame:
    """
    Download ~14 months of daily data so we have 252 simulation days
    plus a 30-day lookback window for metrics on each of those days.
    Returns a tidy DataFrame: date | ticker | open | high | low | close | volume
    """
    print("Downloading historical data (14 months)...")
    raw = yf.download(
        tickers,
        period="2y",
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

def compute_daily_scans(history: pd.DataFrame) -> dict:
    """
    Pre-compute a scan DataFrame for each of the last 30 trading days.
    Returns {date: scan_df} where scan_df has the same shape as fetcher output.
    This is computed once and reused across all parameter combinations.
    """
    all_dates = sorted(history["date"].unique())
    simulation_dates = all_dates[-252:]  # last ~1 year of trading days

    daily_scans = {}

    for sim_date in simulation_dates:
        rows = []
        # For each ticker, use all data up to and including sim_date
        for ticker, grp in history[history["date"] <= sim_date].groupby("ticker"):
            grp = grp.sort_values("date")
            if len(grp) < 2:
                continue

            today    = grp.iloc[-1]
            prev     = grp.iloc[-2]
            price    = today["close"]
            prev_close = prev["close"]

            if price < MIN_PRICE:
                continue

            vol_today = today["volume"]
            avg_vol   = grp["volume"].iloc[-31:-1].mean()

            if avg_vol < MIN_AVG_VOL:
                continue

            rel_volume  = round(vol_today / avg_vol, 2) if avg_vol > 0 else 0
            change      = round(price - prev_close, 2)
            change_pct  = round((change / prev_close) * 100, 2)

            # ATR-14
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
                "change":     change,
                "change_%":   change_pct,
                "volume":     int(vol_today),
                "avg_volume": int(avg_vol),
                "rel_volume": rel_volume,
                "atr_14":     atr,
            })

        if rows:
            df = pd.DataFrame(rows).sort_values("rel_volume", ascending=False)
            daily_scans[sim_date] = df

    return daily_scans


# --- In-memory portfolio ---

class BacktestPortfolio:
    def __init__(self):
        self.cash      = STARTING_CASH
        self.positions = {}  # ticker -> {shares, avg_cost}

    def portfolio_value(self, prices: dict) -> float:
        return self.cash + sum(
            pos["shares"] * prices.get(t, pos["avg_cost"])
            for t, pos in self.positions.items()
        )

    def _size(self, price: float, atr: float) -> float:
        pv       = self.portfolio_value({})
        atr_pct  = atr / price if price > 0 else 0.05
        fraction = min(0.20, max(0.05, 0.01 / atr_pct))
        dollars  = min(pv * fraction, self.cash)
        return round(dollars / price, 6)

    def buy(self, ticker: str, price: float, atr: float) -> bool:
        shares = self._size(price, atr)
        cost   = round(shares * price, 4)
        if cost < 0.01 or self.cash < cost:
            return False
        if ticker in self.positions:
            existing = self.positions[ticker]
            total    = existing["shares"] + shares
            avg      = ((existing["shares"] * existing["avg_cost"]) + cost) / total
            self.positions[ticker] = {"shares": round(total, 6), "avg_cost": round(avg, 4)}
        else:
            self.positions[ticker] = {"shares": shares, "avg_cost": price}
        self.cash = round(self.cash - cost, 4)
        return True

    def sell(self, ticker: str, price: float) -> float:
        """Returns realized P&L."""
        if ticker not in self.positions:
            return 0.0
        pos      = self.positions.pop(ticker)
        proceeds = round(pos["shares"] * price, 4)
        pnl      = round(proceeds - (pos["shares"] * pos["avg_cost"]), 4)
        self.cash = round(self.cash + proceeds, 4)
        return pnl


# --- Single simulation run ---

def run_simulation(daily_scans: dict, params: dict) -> dict:
    min_rel_vol  = params["min_rel_volume"]
    min_chg      = params["min_change_pct"]
    take_profit  = params["take_profit_pct"]
    stop_loss    = params["stop_loss_pct"]

    portfolio    = BacktestPortfolio()
    total_trades = 0
    wins         = 0
    losses       = 0
    total_pnl    = 0.0

    for date, scan in sorted(daily_scans.items()):
        prices = dict(zip(scan["ticker"], scan["price"]))

        # Check exits first
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

        # Check entries
        for _, row in scan.iterrows():
            ticker = row["ticker"]
            if ticker in portfolio.positions:
                continue
            if row["rel_volume"] >= min_rel_vol and row["change_%"] > min_chg:
                portfolio.buy(ticker, row["price"], row["atr_14"])

    # Liquidate any remaining open positions at last known price
    for ticker in list(portfolio.positions.keys()):
        last_scan = list(daily_scans.values())[-1]
        prices    = dict(zip(last_scan["ticker"], last_scan["price"]))
        price     = prices.get(ticker, portfolio.positions[ticker]["avg_cost"])
        pnl       = portfolio.sell(ticker, price)
        total_pnl    += pnl
        total_trades += 1
        wins   += pnl >= 0
        losses += pnl < 0

    final_value  = portfolio.portfolio_value({})
    total_return = round(((final_value - STARTING_CASH) / STARTING_CASH) * 100, 4)
    win_rate     = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0

    return {
        **params,
        "total_return_%":  total_return,
        "final_value":     round(final_value, 4),
        "realized_pnl":    round(total_pnl, 4),
        "total_trades":    total_trades,
        "wins":            wins,
        "losses":          losses,
        "win_rate_%":      win_rate,
    }


# --- Grid search ---

def run_grid_search(daily_scans: dict) -> pd.DataFrame:
    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))
    total  = len(combos)

    print(f"Running {total} parameter combinations...\n")
    results = []

    for i, values in enumerate(combos, 1):
        params = dict(zip(keys, values))
        result = run_simulation(daily_scans, params)
        results.append(result)
        if i % 50 == 0 or i == total:
            print(f"  {i}/{total} complete...")

    df = pd.DataFrame(results).sort_values("total_return_%", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df


# --- Main ---

if __name__ == "__main__":
    start_time = datetime.now()
    print(f"Backtester started — {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. Get universe
    print("Fetching ticker universe...")
    tickers = get_universe()
    print(f"  {len(tickers)} tickers\n")

    # 2. Download history
    history = load_history(tickers)
    print(f"  {history['ticker'].nunique()} tickers with valid data\n")

    # 3. Pre-compute daily scans (done once, reused across all combinations)
    print("Pre-computing daily scan data for 252 simulation days (this takes a few minutes)...")
    daily_scans = compute_daily_scans(history)
    print(f"  {len(daily_scans)} trading days ready\n")

    # 4. Grid search
    results_df = run_grid_search(daily_scans)

    # 5. Save results
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(RESULTS_FILE, index=False)

    # 6. Print top 20
    elapsed = (datetime.now() - start_time).seconds
    print(f"\nCompleted in {elapsed}s — results saved to {RESULTS_FILE}\n")
    print(f"Top 20 parameter combinations by total return:\n")
    print(results_df.head(20).to_string(index=False))

    print(f"\nBest configuration:")
    best = results_df.iloc[0]
    print(f"  min_rel_volume  : {best['min_rel_volume']}")
    print(f"  min_change_pct  : {best['min_change_pct']}%")
    print(f"  take_profit_pct : {best['take_profit_pct']}%")
    print(f"  stop_loss_pct   : {best['stop_loss_pct']}%")
    print(f"  total_return    : {best['total_return_%']:+.4f}%")
    print(f"  win_rate        : {best['win_rate_%']}%")
    print(f"  trades          : {int(best['total_trades'])}")
    print(f"\nTo apply: update strategy.py in v1.0.0 with these values.")
