import yfinance as yf
import pandas as pd
import requests
from io import StringIO
from datetime import datetime


# --- Universe fetchers ---

def get_sp500_tickers() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
    tables = pd.read_html(StringIO(html))
    return tables[0]["Symbol"].tolist()


def get_tsx60_tickers() -> list[str]:
    url = "https://en.wikipedia.org/wiki/S%26P/TSX_60"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text
    tables = pd.read_html(StringIO(html))
    # TSX tickers need .TO suffix for yfinance
    tickers = tables[1]["Symbol"].dropna().tolist()
    return [t + ".TO" for t in tickers]


def get_universe() -> list[str]:
    print("Fetching S&P 500 tickers...")
    sp500 = get_sp500_tickers()
    print(f"  {len(sp500)} tickers")

    print("Fetching TSX 60 tickers...")
    tsx60 = get_tsx60_tickers()
    print(f"  {len(tsx60)} tickers")

    return sp500 + tsx60


# --- Data fetching ---

def fetch_history(tickers: list[str], period: str = "32d", interval: str = "1d") -> dict:
    """Batch-download historical data for all tickers."""
    print(f"\nDownloading historical data for {len(tickers)} tickers...")
    raw = yf.download(
        tickers,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    return raw


# --- Metrics calculation ---

def calculate_metrics(tickers: list[str], raw: dict) -> pd.DataFrame:
    rows = []

    for symbol in tickers:
        try:
            if len(tickers) == 1:
                df = raw
            else:
                df = raw[symbol]

            df = df.dropna()
            if len(df) < 2:
                continue

            today = df.iloc[-1]
            prev = df.iloc[-2]

            price = round(today["Close"], 2)
            prev_close = round(prev["Close"], 2)

            # Filter: minimum price
            if price < 5:
                continue

            volume_today = today["Volume"]
            avg_volume = df["Volume"].iloc[-31:-1].mean()  # 30-day avg excluding today

            # Filter: minimum average volume
            if avg_volume < 500_000:
                continue

            rel_volume = round(volume_today / avg_volume, 2) if avg_volume > 0 else 0

            change = round(price - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2)

            # ATR (14-day)
            highs = df["High"].iloc[-15:]
            lows = df["Low"].iloc[-15:]
            closes = df["Close"].iloc[-15:]
            tr = pd.concat([
                highs - lows,
                (highs - closes.shift()).abs(),
                (lows - closes.shift()).abs(),
            ], axis=1).max(axis=1)
            atr = round(tr.iloc[-14:].mean(), 2)

            rows.append({
                "ticker": symbol,
                "price": price,
                "change": change,
                "change_%": change_pct,
                "volume": int(volume_today),
                "avg_volume": int(avg_volume),
                "rel_volume": rel_volume,
                "atr_14": atr,
            })

        except Exception:
            continue

    df_out = pd.DataFrame(rows)
    if df_out.empty:
        return df_out

    # Sort by relative volume descending
    df_out = df_out.sort_values("rel_volume", ascending=False).reset_index(drop=True)
    return df_out


# --- Main ---

if __name__ == "__main__":
    print(f"Scan started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    tickers = get_universe()
    raw = fetch_history(tickers)
    df = calculate_metrics(tickers, raw)

    if df.empty:
        print("No results after filtering.")
    else:
        print(f"\nTop candidates by relative volume ({len(df)} passed filters):\n")
        print(df.head(30).to_string(index=False))
        print(f"\nTop 10 gainers:")
        print(df.nlargest(10, "change_%")[["ticker", "price", "change_%", "rel_volume", "atr_14"]].to_string(index=False))
        print(f"\nTop 10 losers:")
        print(df.nsmallest(10, "change_%")[["ticker", "price", "change_%", "rel_volume", "atr_14"]].to_string(index=False))
