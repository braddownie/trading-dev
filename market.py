from datetime import datetime
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ET = ZoneInfo("America/New_York")


def _is_market_open(exchange: str, now: datetime) -> bool:
    """Check if a given exchange is open right now."""
    cal = mcal.get_calendar(exchange)
    date_str = now.strftime("%Y-%m-%d")
    schedule = cal.schedule(start_date=date_str, end_date=date_str)
    if schedule.empty:
        return False
    market_open = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close = schedule.iloc[0]["market_close"].to_pydatetime()
    # schedule times are UTC-aware; convert now to UTC for comparison
    now_utc = now.astimezone(ZoneInfo("UTC"))
    return market_open <= now_utc <= market_close


def open_markets() -> list[str]:
    """
    Return list of currently open markets.
    Possible values: "NYSE", "TSX"
    """
    now = datetime.now(tz=ET)
    open_ = []
    if _is_market_open("NYSE", now):
        open_.append("NYSE")
    if _is_market_open("TSX", now):
        open_.append("TSX")
    return open_


def filter_tickers_to_open_markets(tickers: list[str], markets: list[str]) -> list[str]:
    """Keep only tickers that belong to currently open markets."""
    result = []
    for t in tickers:
        if t.endswith(".TO"):
            if "TSX" in markets:
                result.append(t)
        else:
            if "NYSE" in markets:
                result.append(t)
    return result


def market_status_line(markets: list[str]) -> str:
    if not markets:
        return "All markets closed"
    return "Open: " + ", ".join(markets)
