#!/usr/bin/env python3
import sys
import time
from datetime import datetime

from fetcher import get_universe, fetch_history, calculate_metrics
from market import open_markets, filter_tickers_to_open_markets, market_status_line
from simulator import Portfolio, STARTING_CASH
from strategy import buy_signals, sell_signals
from tax_report import generate_report

CYCLE_INTERVAL_SECONDS = 15 * 60  # 15 minutes
_cycle_count = 0


def run_cycle(markets: list[str]):
    """One full scan → strategy → execute cycle, filtered to open markets."""
    global _cycle_count
    _cycle_count += 1
    ts = datetime.now().strftime('%H:%M:%S')

    # Suppress yfinance/urllib noise by redirecting during download
    import warnings
    warnings.filterwarnings("ignore")

    # 1. Scan
    all_tickers = get_universe()
    active_tickers = filter_tickers_to_open_markets(all_tickers, markets)
    raw = fetch_history(active_tickers)
    df = calculate_metrics(active_tickers, raw)

    if df.empty:
        print(f"[{ts}] Cycle #{_cycle_count} — {market_status_line(markets)} — no data, skipping.")
        return

    current_prices = dict(zip(df["ticker"], df["price"]))

    # 2. Strategy
    portfolio = Portfolio()
    open_positions = {t: p for t, p in portfolio.positions.items() if t in current_prices}
    sells = sell_signals(open_positions, current_prices)
    buys = buy_signals(df, portfolio.positions)

    # 3. Execute sells first, then buys
    trade_lines = []

    for sig in sells:
        trade = portfolio.sell(sig.ticker, sig.price, sig.snapshot)
        if trade:
            trade_lines.append(
                f"  SELL {sig.ticker:<10} @ ${sig.price:.2f}  P&L: ${trade['realized_pnl']:+.4f}  ({sig.reason})"
            )

    for sig in buys:
        snapshot = sig.snapshot if isinstance(sig.snapshot, dict) else sig.snapshot.to_dict()
        trade = portfolio.buy(sig.ticker, sig.price, snapshot.get("atr_14", 1.0), snapshot)
        if trade:
            trade_lines.append(
                f"  BUY  {sig.ticker:<10} {trade['shares']:.4f} shares @ ${sig.price:.2f} = ${trade['value']:.2f}  ({sig.reason})"
            )

    # 4. Build compact summary line
    pv = portfolio.portfolio_value(current_prices)
    pos_summary = "  ".join(
        f"{t} {((current_prices.get(t, p['avg_cost']) - p['avg_cost']) / p['avg_cost'] * 100):+.2f}%"
        for t, p in sorted(portfolio.positions.items())
    )

    # 5. Print cycle output
    print(f"\n[{ts}] Cycle #{_cycle_count} — {market_status_line(markets)}")
    if trade_lines:
        for line in trade_lines:
            print(line)
    else:
        print(f"  No trades executed.")
    print(f"  Portfolio: ${pv:.2f} ({pv - STARTING_CASH:+.2f})  Cash: ${portfolio.cash:.2f}  Positions: {len(portfolio.positions)}")
    if pos_summary:
        print(f"  {pos_summary}")


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


def cmd_walkforward():
    """Run walk-forward analysis (train Year 1, test Year 2 out-of-sample)."""
    from backtester.walkforward import cli_main
    cli_main(sys.argv[2:])


def cmd_rolling_walkforward():
    """Run rolling walk-forward analysis (quarterly windows, ~2015 to present, compounding $5k)."""
    from backtester.rolling_walkforward import cli_main
    cli_main(sys.argv[2:])


def cmd_rwf_optimize():
    """Run full 45-combination optimizer matrix (reopt period × training window × drawdown threshold)."""
    from backtester.rwf_optimizer import cli_main
    cli_main(sys.argv[2:])


def cmd_help():
    print("""
Usage: python main.py <command>

Commands:
  start                Run automatically every 15 min during market hours (Ctrl+C to stop)
  run                  Run a single cycle manually (ignores market hours — useful for testing)
  scan                 Scan the market and display top candidates
  portfolio            Show current positions and P&L
  report               Tax report
  walkforward          Walk-forward analysis (--train-years, --test-years, --slippage, --spread, --workers)
  rolling-walkforward  Rolling walk-forward, 2015→present, compounding $5k vs 7% benchmark
                         (--slippage, --spread, --drawdown, --train-days, --test-days, --workers, --name, --notes)
  rwf-optimize         Full 45-run optimizer matrix (reopt period × train window × drawdown threshold)
                         (--slippage, --spread, --workers, --name, --notes)
  help                 Show this message
""")


COMMANDS = {
    "start":               cmd_start,
    "run":                 cmd_run,
    "scan":                cmd_scan,
    "portfolio":           cmd_portfolio,
    "report":              cmd_report,
    "walkforward":         cmd_walkforward,
    "rolling-walkforward": cmd_rolling_walkforward,
    "rwf-optimize":        cmd_rwf_optimize,
    "help":                cmd_help,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)
    COMMANDS[cmd]()
