#!/usr/bin/env python3
import sys
import time
from datetime import datetime

from fetcher import get_universe, fetch_history, calculate_metrics
from market import open_markets, filter_tickers_to_open_markets, market_status_line
from simulator import Portfolio
from strategy import buy_signals, sell_signals
from tax_report import generate_report

CYCLE_INTERVAL_SECONDS = 15 * 60  # 15 minutes


def run_cycle(markets: list[str]):
    """One full scan → strategy → execute cycle, filtered to open markets."""
    print(f"\n{'='*60}")
    print(f"  Trading cycle — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {market_status_line(markets)}")
    print(f"{'='*60}\n")

    # 1. Scan — fetch full universe then filter to open markets
    print("Step 1/3 — Scanning market...")
    all_tickers = get_universe()
    active_tickers = filter_tickers_to_open_markets(all_tickers, markets)
    print(f"  {len(active_tickers)} tickers active ({market_status_line(markets)})")

    raw = fetch_history(active_tickers)
    df = calculate_metrics(active_tickers, raw)

    if df.empty:
        print("  No candidates found after filtering.")
        return

    current_prices = dict(zip(df["ticker"], df["price"]))

    # 2. Strategy
    print("\nStep 2/3 — Evaluating signals...")
    portfolio = Portfolio()

    # Only evaluate sell signals for positions in open markets
    open_positions = {
        t: p for t, p in portfolio.positions.items()
        if t in current_prices
    }
    sells = sell_signals(open_positions, current_prices)
    buys = buy_signals(df, portfolio.positions)

    # 3. Execute sells first (free up cash), then buys
    print("\nStep 3/3 — Executing trades...")
    executed = 0

    if not sells and not buys:
        print("  No signals this cycle.")
    else:
        for sig in sells:
            trade = portfolio.sell(sig.ticker, sig.price, sig.snapshot)
            if trade:
                print(f"  SELL {sig.ticker:<10}  @ ${sig.price:.2f}  P&L: ${trade['realized_pnl']:+.4f}  ({sig.reason})")
                executed += 1

        for sig in buys:
            snapshot = sig.snapshot if isinstance(sig.snapshot, dict) else sig.snapshot.to_dict()
            trade = portfolio.buy(sig.ticker, sig.price, snapshot.get("atr_14", 1.0), snapshot)
            if trade:
                print(f"  BUY  {sig.ticker:<10}  {trade['shares']:.4f} shares @ ${sig.price:.2f}  = ${trade['value']:.2f}  ({sig.reason})")
                executed += 1
            else:
                print(f"  SKIP {sig.ticker:<10}  insufficient cash")

        print(f"\n  {executed} trade(s) executed.")

    portfolio.summary(current_prices)


def cmd_start():
    """Run automatically every 15 minutes during market hours."""
    print(f"Bot started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running every {CYCLE_INTERVAL_SECONDS // 60} minutes during market hours.")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            markets = open_markets()

            if not markets:
                next_check = datetime.now().strftime('%H:%M:%S')
                print(f"[{next_check}] Markets closed — sleeping {CYCLE_INTERVAL_SECONDS // 60}m...")
                time.sleep(CYCLE_INTERVAL_SECONDS)
                continue

            run_cycle(markets)
            time.sleep(CYCLE_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"\n[ERROR] Cycle failed: {e}")
            print(f"Retrying in {CYCLE_INTERVAL_SECONDS // 60} minutes...")
            time.sleep(CYCLE_INTERVAL_SECONDS)


def cmd_run():
    """Run a single cycle regardless of market hours (for testing)."""
    markets = open_markets()
    if not markets:
        print("Warning: all markets are currently closed. Running with full ticker list.")
        markets = ["NYSE", "TSX"]
    run_cycle(markets)


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


def cmd_portfolio():
    """Show current portfolio and open positions with live prices."""
    print(f"\nPortfolio snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    portfolio = Portfolio()

    if not portfolio.positions:
        portfolio.summary()
        return

    print("Fetching current prices for open positions...")
    held = list(portfolio.positions.keys())
    raw = fetch_history(held, period="2d")
    df = calculate_metrics(held, raw)
    current_prices = dict(zip(df["ticker"], df["price"])) if not df.empty else {}

    portfolio.summary(current_prices)


def cmd_report():
    """Generate tax report from trade log."""
    generate_report()


def cmd_help():
    print("""
Usage: python main.py <command>

Commands:
  start       Run automatically every 15 min during market hours (Ctrl+C to stop)
  run         Run a single cycle manually (ignores market hours — useful for testing)
  scan        Scan the market and display top candidates
  portfolio   Show current positions and P&L
  report      Tax report (coming soon)
  help        Show this message
""")


COMMANDS = {
    "start": cmd_start,
    "run": cmd_run,
    "scan": cmd_scan,
    "portfolio": cmd_portfolio,
    "report": cmd_report,
    "help": cmd_help,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)
    COMMANDS[cmd]()
