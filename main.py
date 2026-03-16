#!/usr/bin/env python3
import sys
from datetime import datetime

from fetcher import get_universe, fetch_history, calculate_metrics
from simulator import Portfolio
from strategy import buy_signals, sell_signals


def cmd_scan():
    """Fetch market data and display top candidates."""
    tickers = get_universe()
    raw = fetch_history(tickers)
    df = calculate_metrics(tickers, raw)

    if df.empty:
        print("No candidates found after filtering.")
        return

    print(f"\nTop 30 candidates by relative volume ({len(df)} passed filters):\n")
    print(df.head(30).to_string(index=False))

    print(f"\nTop 10 gainers:")
    print(df.nlargest(10, "change_%")[["ticker", "price", "change_%", "rel_volume", "atr_14"]].to_string(index=False))

    print(f"\nTop 10 losers:")
    print(df.nsmallest(10, "change_%")[["ticker", "price", "change_%", "rel_volume", "atr_14"]].to_string(index=False))


def cmd_run():
    """Full cycle: scan → strategy → execute trades → show portfolio."""
    print(f"\n{'='*60}")
    print(f"  Trading cycle — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 1. Scan
    print("Step 1/3 — Scanning market...")
    tickers = get_universe()
    raw = fetch_history(tickers)
    df = calculate_metrics(tickers, raw)

    if df.empty:
        print("No candidates found. Exiting.")
        return

    current_prices = dict(zip(df["ticker"], df["price"]))

    # 2. Strategy
    print("\nStep 2/3 — Evaluating signals...")
    portfolio = Portfolio()

    sells = sell_signals(portfolio.positions, current_prices)
    buys = buy_signals(df, portfolio.positions)

    # 3. Execute sells first (free up cash), then buys
    print("\nStep 3/3 — Executing trades...")
    executed = 0

    if not sells and not buys:
        print("  No signals this cycle.")
    else:
        for sig in sells:
            snapshot = sig.snapshot
            trade = portfolio.sell(sig.ticker, sig.price, snapshot)
            if trade:
                print(f"  SELL {sig.ticker:<10}  @ ${sig.price:.2f}  P&L: ${trade['realized_pnl']:+.4f}  ({sig.reason})")
                executed += 1

        for sig in buys:
            snapshot = sig.snapshot if isinstance(sig.snapshot, dict) else sig.snapshot.to_dict()
            trade = portfolio.buy(sig.ticker, sig.price, sig.snapshot.get("atr_14", 1.0), snapshot)
            if trade:
                print(f"  BUY  {sig.ticker:<10}  {trade['shares']:.4f} shares @ ${sig.price:.2f}  = ${trade['value']:.2f}  ({sig.reason})")
                executed += 1
            else:
                print(f"  SKIP {sig.ticker:<10}  insufficient cash")

        print(f"\n  {executed} trade(s) executed.")

    portfolio.summary(current_prices)


def cmd_portfolio():
    """Show current portfolio and open positions with live prices."""
    print(f"\nPortfolio snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    portfolio = Portfolio()

    if not portfolio.positions:
        portfolio.summary()
        return

    # Fetch current prices for held tickers only
    print("Fetching current prices for open positions...")
    held = list(portfolio.positions.keys())
    raw = fetch_history(held, period="2d")
    df = calculate_metrics(held, raw)
    current_prices = dict(zip(df["ticker"], df["price"])) if not df.empty else {}

    portfolio.summary(current_prices)


def cmd_help():
    print("""
Usage: python main.py <command>

Commands:
  scan        Scan the market and display top candidates
  run         Full cycle: scan → strategy signals → execute trades
  portfolio   Show current positions and P&L
  report      Tax report (coming soon)
  help        Show this message
""")


COMMANDS = {
    "scan": cmd_scan,
    "run": cmd_run,
    "portfolio": cmd_portfolio,
    "help": cmd_help,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)
    COMMANDS[cmd]()
