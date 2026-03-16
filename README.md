# trading-dev

A Python paper trading and market simulation tool. Pulls near-real-time market data for US and TSX stocks, simulates trades against live prices, evaluates strategy performance, calculates tax implications under Canadian tax law, and backtests + optimizes strategy parameters — no real order execution.

---

## Overview

### Live Bot

| Component | File | Description |
|-----------|------|-------------|
| Data layer | `fetcher.py` | Scans S&P 500 + TSX 60, ranks by relative volume, ATR, momentum |
| Trade simulator | `simulator.py` | Executes paper trades, tracks positions, logs to JSON |
| Strategy engine | `strategy.py` | Momentum signals, take profit / stop loss |
| Tax reporter | `tax_report.py` | Capital gains vs business income comparison (Ontario) |
| Scheduler | `market.py` | Market hours + holiday detection (NYSE + TSX) |
| CLI | `main.py` | Entry point for all commands |

### Backtester

| Component | File | Description |
|-----------|------|-------------|
| Grid search | `backtester/backtest.py` | Replays history across 300 parameter combinations |
| Database | `backtester/db.py` | SQLite storage for all runs, results, and equity curves |
| Results DB | `backtester/results/trading.db` | All backtest runs (gitignored) |

---

## Setup

**Requirements:** Python 3.11+

```bash
cd /trading/v1.0.0
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

---

## Usage

### Live Bot

```bash
# Run automatically every 15 minutes during market hours (Ctrl+C to stop)
python main.py start

# Run a single cycle manually (ignores market hours — useful for testing)
python main.py run

# Scan the market and display top candidates without trading
python main.py scan

# Show current positions and live P&L
python main.py portfolio

# Generate tax report
python main.py report
```

### Backtester

```bash
# Standard 1-year grid search (252 trading days)
python -m backtester.backtest

# Custom period and simulation window
python -m backtester.backtest --period 65d --days 30

# With slippage and spread costs
python -m backtester.backtest --slippage 0.0005 --spread 0.0003

# Named run with notes
python -m backtester.backtest --name "1y_with_costs" --notes "Slippage model added"
```

**Backtester flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `2y` | yfinance download period |
| `--days` | `252` | Number of simulation days |
| `--name` | auto | Run name (stored in DB) |
| `--slippage` | `0.0` | Slippage % per side |
| `--spread` | `0.0` | Spread % per side |
| `--notes` | `""` | Notes stored with this run |

---

## How It Works

### Data Layer
- Fetches the S&P 500 constituent list from Wikipedia
- Fetches the TSX 60 constituent list from Wikipedia (appends `.TO` suffix)
- Downloads 32 days of daily OHLCV data via `yfinance` (~15 min delayed)
- Filters tickers: price > $5, average volume > 500,000
- Ranks by **relative volume** (today vs 30-day average)
- Also calculates **ATR-14** and **% change on the day**

### Strategy — Momentum + Volume
- **BUY signal:** relative volume ≥ 1.5x AND day change > 0% (positive momentum with unusual volume)
- **SELL signal (take profit):** position up +3% from entry price
- **SELL signal (stop loss):** position down -2% from entry price
- Thresholds are configurable constants at the top of `strategy.py`

### Position Sizing — Kelly-Style
- Position size is inversely proportional to ATR% (higher volatility = smaller position)
- Capped between **5% and 20%** of portfolio value per position
- Fractional shares are supported (paper trading — no real execution constraints)
- Sizes recalculate dynamically based on current portfolio value

### Scheduler
- `python main.py start` loops every **15 minutes** (aligned with yfinance data refresh rate)
- Checks NYSE and TSX calendars before each cycle using `pandas_market_calendars`
- Skips cycles when all markets are closed (nights, weekends, holidays)
- On days where only one market is open, filters tickers to that market only
- Handles errors gracefully — logs and retries next cycle

### Trade Log (Audit Trail)
Every trade is written to `data/trades.json` with:
- Unique trade ID, timestamp, ticker, action (BUY/SELL), shares, price, value
- Cash balance after trade
- Realized P&L (SELL trades only)
- Full price snapshot from yfinance at time of trade (for verification)

This file is the single source of truth for all P&L and tax calculations.

### Backtester
- Downloads up to 2 years of daily OHLCV data for the full universe
- Pre-computes scan metrics for each simulation day (done once, reused across all combinations)
- Grid searches 300 parameter combinations: `min_rel_volume` × `min_change_pct` × `take_profit_pct` × `stop_loss_pct`
- Captures daily equity curve per combination (for charting)
- Optionally applies slippage and spread costs to simulate real-world execution
- Saves all results to SQLite DB and exports a CSV per run

### Database
All backtest runs persist to `backtester/results/trading.db` (SQLite):
- `test_runs` — metadata per run (name, type, date range, costs, notes)
- `test_results` — one row per parameter combination per run
- `equity_curves` — daily portfolio value per combination (used for charting)

---

## Tax Report

Run `python main.py report` to see:

- All closed trades with holding periods and realized P&L
- Open positions (excluded from tax calculation until sold)
- Side-by-side tax comparison:
  - **Capital gains** — 50% inclusion rate, added to base salary
  - **Business income** — 100% inclusion rate, added to base salary
- After-tax profit under each treatment
- CRA classification indicators:
  - Monthly trade frequency
  - Average holding period
  - Win/loss ratio

### Tax Assumptions
- Base salary: **$0**
- Province: **Ontario**
- Brackets: **2025 Federal + Ontario** (hardcoded)
- Ontario surtax applied where applicable
- Federal and Ontario basic personal amounts applied

> **Disclaimer:** This tool provides estimates for informational purposes only. Consult a tax professional before filing. CRA determines capital gains vs business income treatment based on intent, frequency, holding period, and other factors.

---

## Portfolio

- Starting balance: **$5,000**
- State persisted in `data/portfolio.json` between runs
- `data/` directory is excluded from git (contains live trading state)

---

## Project Structure

```
/trading/v1.0.0/
├── main.py                        # CLI entry point
├── fetcher.py                     # Market data and scanner
├── simulator.py                   # Portfolio and trade execution
├── strategy.py                    # Buy/sell signal logic
├── tax_report.py                  # Canadian tax analysis
├── market.py                      # Market hours and holiday calendar
├── requirements.txt               # Python dependencies
├── backtester/
│   ├── backtest.py                # Grid search backtester
│   ├── db.py                      # SQLite database layer
│   └── results/                   # Gitignored
│       ├── trading.db             # All backtest runs (SQLite)
│       └── *.csv                  # Per-run CSV exports
├── data/                          # Gitignored
│   ├── portfolio.json             # Current portfolio state
│   └── trades.json                # Full trade audit log
└── venv/                          # Gitignored
```

---

## Configuration

| Parameter | Location | Default | Description |
|-----------|----------|---------|-------------|
| `MIN_REL_VOLUME` | `strategy.py` | `1.5` | Minimum relative volume for buy signal |
| `MIN_CHANGE_PCT` | `strategy.py` | `0.0` | Minimum day % change for buy signal |
| `TAKE_PROFIT_PCT` | `strategy.py` | `3.0` | Take profit threshold (%) |
| `STOP_LOSS_PCT` | `strategy.py` | `-2.0` | Stop loss threshold (%) |
| `MAX_POSITION_PCT` | `simulator.py` | `0.20` | Max portfolio % per position |
| `MIN_POSITION_PCT` | `simulator.py` | `0.05` | Min portfolio % per position |
| `CYCLE_INTERVAL_SECONDS` | `main.py` | `900` | Seconds between cycles (15 min) |
| `STARTING_CASH` | `simulator.py` | `5000.0` | Starting portfolio balance |
| `BASE_SALARY` | `tax_report.py` | `0` | Annual non-trading income for tax calc |

---

## Backtest Results

### 30-day grid search (no transaction costs)
- **Best:** +19.2% — `min_rel_vol=1.0`, `take_profit=1%`, `stop_loss=-1%`
- **Worst:** -4.5% — `take_profit=5%`, `stop_loss=-3%`
- 279/300 combinations profitable

### 1-year grid search (no transaction costs)
- **Best:** +89.5% — `min_rel_vol=1.75`, `min_change=0%`, `take_profit=1%`, `stop_loss=-5%`
- **Average across all 300:** +37.9%
- 298/300 combinations profitable
- Key finding: take_profit=1% dominates (avg +64.2%) vs take_profit=7% (avg +16.3%)

> Transaction cost modeling (slippage + spread) not yet applied — results represent frictionless simulation.

---

## Notes

- Data is ~15 minutes delayed (Yahoo Finance free tier) — suitable for paper trading simulation, not HFT
- Dual-class TSX tickers (e.g. `TECK.B.TO`, `RCI.B.TO`) are not supported by yfinance and are silently skipped
- `BRK.B` (Berkshire Hathaway B) is not available via yfinance and is silently skipped
- The bot does not short sell — only long positions
- Backtest results reflect survivorship bias — only current index constituents are tested
