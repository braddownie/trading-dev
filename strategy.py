from dataclasses import dataclass

import pandas as pd

# --- Tunable thresholds ---
MIN_REL_VOLUME = 1.5     # minimum relative volume for a buy signal
MIN_CHANGE_PCT = 0.0     # minimum % change on the day (positive momentum)
TAKE_PROFIT_PCT = 3.0    # sell when position is up this % from entry
STOP_LOSS_PCT = -2.0     # sell when position is down this % from entry


@dataclass
class Signal:
    ticker: str
    action: str          # "BUY" or "SELL"
    price: float
    reason: str
    snapshot: dict


def buy_signals(scan: pd.DataFrame, open_positions: dict) -> list[Signal]:
    """
    Return BUY signals for tickers not already held that meet momentum criteria.
    """
    signals = []
    for _, row in scan.iterrows():
        ticker = row["ticker"]
        if ticker in open_positions:
            continue
        if row["rel_volume"] >= MIN_REL_VOLUME and row["change_%"] > MIN_CHANGE_PCT:
            signals.append(Signal(
                ticker=ticker,
                action="BUY",
                price=row["price"],
                reason=f"rel_vol={row['rel_volume']:.2f}x  change={row['change_%']:+.2f}%",
                snapshot=row.to_dict(),
            ))
    return signals


def sell_signals(open_positions: dict, current_prices: dict) -> list[Signal]:
    """
    Return SELL signals for held positions that hit take-profit or stop-loss.
    """
    signals = []
    for ticker, pos in open_positions.items():
        price = current_prices.get(ticker)
        if price is None:
            continue
        pnl_pct = ((price - pos["avg_cost"]) / pos["avg_cost"]) * 100
        if pnl_pct >= TAKE_PROFIT_PCT:
            reason = f"take profit  {pnl_pct:+.2f}% (target {TAKE_PROFIT_PCT:+.1f}%)"
        elif pnl_pct <= STOP_LOSS_PCT:
            reason = f"stop loss    {pnl_pct:+.2f}% (limit {STOP_LOSS_PCT:+.1f}%)"
        else:
            continue
        signals.append(Signal(
            ticker=ticker,
            action="SELL",
            price=price,
            reason=reason,
            snapshot={"price": price, "avg_cost": pos["avg_cost"], "pnl_%": round(pnl_pct, 2)},
        ))
    return signals
