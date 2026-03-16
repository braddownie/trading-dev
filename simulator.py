import json
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
TRADES_FILE = DATA_DIR / "trades.json"

STARTING_CASH = 5000.0
MAX_POSITION_PCT = 0.20  # no single position > 20% of portfolio
MIN_POSITION_PCT = 0.05  # no single position < 5% of portfolio


class Portfolio:
    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._load()

    def _load(self):
        if PORTFOLIO_FILE.exists():
            state = json.loads(PORTFOLIO_FILE.read_text())
            self.cash = state["cash"]
            self.positions = state["positions"]
        else:
            self.cash = STARTING_CASH
            self.positions = {}
            self._save()

    def _save(self):
        PORTFOLIO_FILE.write_text(json.dumps({
            "cash": self.cash,
            "positions": self.positions,
        }, indent=2))

    def _log_trade(self, entry: dict):
        trades = []
        if TRADES_FILE.exists():
            trades = json.loads(TRADES_FILE.read_text())
        trades.append(entry)
        TRADES_FILE.write_text(json.dumps(trades, indent=2))

    def portfolio_value(self, current_prices: dict | None = None) -> float:
        position_value = sum(
            pos["shares"] * (current_prices or {}).get(ticker, pos["avg_cost"])
            for ticker, pos in self.positions.items()
        )
        return round(self.cash + position_value, 4)

    def size_position(self, price: float, atr_14: float, portfolio_value: float) -> float:
        """
        Kelly-style sizing: allocate inversely proportional to ATR%.
        Higher volatility = smaller position. Capped between MIN and MAX.
        """
        atr_pct = atr_14 / price if price > 0 else 0.05
        fraction = min(MAX_POSITION_PCT, max(MIN_POSITION_PCT, 0.01 / atr_pct))
        dollar_amount = min(portfolio_value * fraction, self.cash)
        return round(dollar_amount / price, 6)  # fractional shares supported

    def buy(self, ticker: str, price: float, atr_14: float, price_snapshot: dict) -> dict | None:
        pv = self.portfolio_value({ticker: price})
        shares = self.size_position(price, atr_14, pv)
        cost = round(shares * price, 4)

        if cost < 0.01 or self.cash < cost:
            return None

        if ticker in self.positions:
            existing = self.positions[ticker]
            total_shares = existing["shares"] + shares
            avg_cost = ((existing["shares"] * existing["avg_cost"]) + cost) / total_shares
            self.positions[ticker] = {"shares": round(total_shares, 6), "avg_cost": round(avg_cost, 4)}
        else:
            self.positions[ticker] = {"shares": shares, "avg_cost": price}

        self.cash = round(self.cash - cost, 4)

        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "action": "BUY",
            "shares": shares,
            "price": price,
            "value": cost,
            "cash_after": self.cash,
            "realized_pnl": None,
            "price_snapshot": price_snapshot,
        }
        self._log_trade(entry)
        self._save()
        return entry

    def sell(self, ticker: str, price: float, price_snapshot: dict) -> dict | None:
        if ticker not in self.positions:
            return None

        pos = self.positions[ticker]
        shares = pos["shares"]
        proceeds = round(shares * price, 4)
        realized_pnl = round(proceeds - (shares * pos["avg_cost"]), 4)

        del self.positions[ticker]
        self.cash = round(self.cash + proceeds, 4)

        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "action": "SELL",
            "shares": shares,
            "price": price,
            "value": proceeds,
            "cash_after": self.cash,
            "realized_pnl": realized_pnl,
            "price_snapshot": price_snapshot,
        }
        self._log_trade(entry)
        self._save()
        return entry

    def summary(self, current_prices: dict | None = None):
        pv = self.portfolio_value(current_prices)
        total_pnl = pv - STARTING_CASH
        print(f"\n{'='*60}")
        print(f"  Portfolio Value : ${pv:.2f}  (started with ${STARTING_CASH:.2f})")
        print(f"  Total P&L       : ${total_pnl:+.2f}")
        print(f"  Cash            : ${self.cash:.2f}")
        print(f"  Invested        : ${pv - self.cash:.2f}")
        print(f"{'='*60}")

        if not self.positions:
            print("  No open positions.\n")
            return

        print(f"\n  {'Ticker':<10} {'Shares':>10} {'Avg Cost':>10} {'Cur Price':>10} {'Unreal P&L':>12} {'P&L %':>8}")
        print(f"  {'-'*56}")
        for ticker, pos in sorted(self.positions.items()):
            cur = (current_prices or {}).get(ticker, pos["avg_cost"])
            unreal = (cur - pos["avg_cost"]) * pos["shares"]
            unreal_pct = ((cur - pos["avg_cost"]) / pos["avg_cost"]) * 100
            print(f"  {ticker:<10} {pos['shares']:>10.4f} {pos['avg_cost']:>10.2f} {cur:>10.2f} {unreal:>+12.4f} {unreal_pct:>+7.2f}%")
        print()


if __name__ == "__main__":
    from fetcher import get_universe, fetch_history, calculate_metrics

    print(f"Simulator started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Fetch scan data
    tickers = get_universe()
    raw = fetch_history(tickers)
    df = calculate_metrics(tickers, raw)

    if df.empty:
        print("No candidates found.")
        exit()

    # Buy top 5 candidates by relative volume
    portfolio = Portfolio()
    candidates = df.head(5)
    current_prices = {}

    print(f"\nExecuting buys on top 5 candidates by relative volume:\n")
    for _, row in candidates.iterrows():
        snapshot = {
            "price": row["price"],
            "change": row["change"],
            "change_%": row["change_%"],
            "volume": row["volume"],
            "avg_volume": row["avg_volume"],
            "rel_volume": row["rel_volume"],
            "atr_14": row["atr_14"],
        }
        trade = portfolio.buy(row["ticker"], row["price"], row["atr_14"], snapshot)
        current_prices[row["ticker"]] = row["price"]

        if trade:
            print(f"  BUY  {trade['ticker']:<10}  {trade['shares']:.4f} shares @ ${trade['price']:.2f}  = ${trade['value']:.2f}")
        else:
            print(f"  SKIP {row['ticker']:<10}  insufficient cash")

    portfolio.summary(current_prices)
