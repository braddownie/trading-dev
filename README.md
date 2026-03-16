# trading-dev

A Python paper trading and market simulation tool built from scratch as a learning project in quantitative strategy development. Pulls near-real-time market data for S&P 500 stocks, simulates trades against live prices, evaluates strategy performance out-of-sample, calculates tax implications under Canadian tax law, and backtests + optimizes strategy parameters across a decade of market history — no real order execution.

---

## Project Journey

This project was built iteratively, one validated component at a time. Here's the story of how it evolved:

### Stage 1 — Live Bot
Started with a basic momentum scanner: scan the S&P 500 for unusual relative volume and positive price movement, paper trade with a $5,000 portfolio, log everything. The live bot runs every 15 minutes during market hours and persists state between sessions.

### Stage 2 — Backtester
Added a grid search backtester to evaluate whether the strategy's parameters were actually optimal, or just guessed. Discovered that **take profit at 1% dramatically outperforms take profit at 7%** on a 1-year backtest — counterintuitive but consistent. Transaction costs (slippage + spread) roughly halved average returns, making realistic cost modeling a non-negotiable requirement.

Key finding: frictionless best was +89.5%, realistic best dropped to +79.3%, and average fell from +37.9% to +21.4%. The strategy survives costs but the margin is real.

### Stage 3 — Walk-Forward Validation
A grid search on historical data proves nothing by itself — it will always find parameters that fit the past. Added walk-forward validation: train on one period, test on the next unseen period. This is the standard methodology used in professional quantitative research.

Expanded to a **rolling walk-forward**: slide a training window across 10 years of data, test each quarter out-of-sample, compound the results into a single portfolio. This answers the real question: *is this edge consistent across a decade of varied market conditions, or did we just get lucky in one period?*

**Preliminary result (quarterly_1y config, no drawdown limit, 40 windows 2016→2026):**
- Strategy: $5,000 → $16,562 (+231.2%)
- Benchmark: $5,000 → $9,836 (+96.7%) [7% annual]
- Delta: +134.5 percentage points
- Profitable quarters: 31/40 (77.5%) | Beat benchmark: 26/40 (65.0%)
- Worst quarter: -26.1% (Q1 2020, COVID crash) | Max drawdown: -28.0% (2022 bear market)

### Stage 4 — Optimizer (in progress)
The rolling walk-forward has several free variables: how often to reoptimize (monthly, quarterly, 6-monthly), how long a training window to use (3 months to 18 months), and whether to suppress new buys during drawdowns (and at what threshold). Rather than guess, built a **45-run optimizer matrix** that tests every combination systematically:

- 9 reoptimization configs (3 reopt periods × 3–4 training windows)
- 5 drawdown thresholds (none, 10%, 15%, 16%, 20%)
- Each config runs the full rolling walk-forward with 8,640 parameter combinations per window
- Results ranked by final compounded balance

**This run is currently in progress.** Results and winning configuration will be added here when complete.

### What's Next
- Wire the winning optimizer config into the live bot
- Add monthly reoptimization command (re-runs backtester on trailing 12 months, suggests updated params — human reviews before applying)
- Survivorship bias correction (currently using present-day S&P 500 constituents; historical constituents would give more accurate results)
- Web UI for browsing results and triggering runs

---

## Key Design Decisions

**S&P 500 only (USD, via Alpaca)**
Initially scanned both S&P 500 and TSX 60, but TSX introduces per-trade FX conversion costs (~0.5–1.5%) that would dwarf any edge. Alpaca is USD-only, so limiting to S&P 500 eliminates FX risk entirely and simplifies live deployment.

**No perpetual auto-reoptimization**
Tempting to re-run the optimizer after every market session and auto-apply the latest "best" parameters. Rejected — this is a fast path to overfitting live. Instead: monthly periodic reoptimization with human review. Only switch parameters if improvement exceeds a meaningful threshold.

**Realistic transaction costs from the start**
Modeled slippage (0.05% per side) and spread (0.03% per side) from day one. Results without these are misleading — the strategy needed to prove itself with costs included before any further development.

**Walk-forward over in-sample optimization**
Any parameter set will look good in-sample. The only meaningful test is out-of-sample performance across varied market regimes. All reported results use parameters that were never seen during training.

**Crash-safe optimizer**
A full 45-run matrix takes days to run. Every window saves to SQLite immediately on completion. If the machine dies mid-run, `--resume <id>` picks up exactly where it left off.

**Tax treatment built in**
Canadian capital gains vs business income treatment materially affects after-tax returns. The tax reporter calculates both sides using current Ontario + federal brackets so the real return is always visible.

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
| Grid search | `backtester/backtest.py` | Replays history across 8,640 parameter combinations (S&P 500 only) |
| Walk-forward | `backtester/walkforward.py` | Train Year 1, test Year 2 out-of-sample (S&P 500 only) |
| Rolling walk-forward | `backtester/rolling_walkforward.py` | Sliding train/test windows 2015→present, compounds $5k vs benchmark |
| RWF Optimizer | `backtester/rwf_optimizer.py` | 45-run matrix across reopt periods, training windows, and drawdown thresholds |
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

# Generate tax report (prompts for base salary at runtime)
python main.py report
```

### Backtester

```bash
# Standard 1-year grid search (252 trading days)
python -m backtester.backtest

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
| `--slippage` | `0.0` | Slippage fraction per side (e.g. `0.0005` = 0.05%) |
| `--spread` | `0.0` | Spread fraction per side (e.g. `0.0003` = 0.03%) |
| `--notes` | `""` | Notes stored with this run |

### Walk-Forward Analysis

```bash
# Single walk-forward (train Year 1, test Year 2 out-of-sample)
python main.py walkforward
python main.py walkforward --slippage 0.0005 --spread 0.0003

# Rolling walk-forward (quarterly windows, 2015→present)
python main.py rolling-walkforward
python main.py rolling-walkforward --slippage 0.0005 --spread 0.0003 --drawdown 15.0

# Full 45-run optimizer matrix (run in a screen/tmux session — long-running)
python main.py rwf-optimize

# Resume an interrupted optimizer run
python main.py rwf-optimize --resume <optimizer_run_id>

# Quick smoke test (4 configs, 2-year history cap)
python main.py rwf-optimize --test
```

**Rolling walk-forward flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--slippage` | `0.0005` | Slippage fraction per side |
| `--spread` | `0.0003` | Spread fraction per side |
| `--drawdown` | `None` | Max drawdown % before suppressing new buys |
| `--train-days` | `252` | Training window in trading days |
| `--test-days` | `63` | Test window in trading days |
| `--workers` | cpu_count - 1 | Parallel worker processes |
| `--name` | auto | Run name (stored in DB) |
| `--notes` | `""` | Notes stored with this run |

**RWF Optimizer flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--slippage` | `0.0005` | Slippage fraction per side |
| `--spread` | `0.0003` | Spread fraction per side |
| `--workers` | cpu_count - 1 | Parallel worker processes |
| `--name` | auto | Run name (stored in DB) |
| `--notes` | `""` | Notes stored with this run |
| `--resume` | `None` | Resume an existing optimizer run by ID |
| `--test` | off | Smoke test mode: 4 configs, 2-year history cap |

---

## How It Works

### Data Layer
- Fetches the S&P 500 constituent list from Wikipedia
- Downloads 32 days of daily OHLCV data via `yfinance` (~15 min delayed)
- Filters tickers: price > $5, average volume > 500,000
- Ranks by **relative volume** (today vs N-day average, configurable lookback)
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
- Checks NYSE calendar before each cycle using `pandas_market_calendars`
- Skips cycles when markets are closed (nights, weekends, holidays)
- Handles errors gracefully — logs and retries next cycle

### Trade Log (Audit Trail)
Every trade is written to `data/trades.json` with:
- Unique trade ID, timestamp, ticker, action (BUY/SELL), shares, price, value
- Cash balance after trade
- Realized P&L (SELL trades only)
- Full price snapshot from yfinance at time of trade (for verification)

This file is the single source of truth for all P&L and tax calculations.

### Backtester
- Downloads daily OHLCV data for the S&P 500 universe
- Pre-computes scan metrics for each simulation day across all vol lookback periods (done once, reused across all combinations)
- Grid searches **8,640 parameter combinations** across 6 dimensions:

| Parameter | Values |
|-----------|--------|
| `min_rel_volume` | 1.0, 1.25, 1.5, 1.75, 2.0 |
| `min_change_pct` | 0.0, 0.5, 1.0 |
| `take_profit_pct` | 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0 |
| `stop_loss_pct` | -1.0, -2.0, -3.0, -5.0, -7.0, -10.0 |
| `max_position_pct` | 0.10, 0.15, 0.20 |
| `vol_lookback` | 10, 20, 30, 60 days |

- Parallelized with `ProcessPoolExecutor` — near-linear speedup with physical core count
- Optionally applies slippage and spread costs to simulate real-world execution
- Saves all results to SQLite DB

### Walk-Forward Analysis
Tests whether parameters learned from historical data hold up out-of-sample.

**Single walk-forward** (`walkforward.py`):
- Trains a full grid search on Year 1
- Tests the top 3 parameter combinations on Year 2
- Answers: did the best params from training actually hold up?

**Rolling walk-forward** (`rolling_walkforward.py`):
- Slides a configurable training window / test window across all data from 2015 to present
- Shifts by one test period each iteration
- For each window: grid search on train period → best params → out-of-sample test period
- Compounds a starting $5,000 through every test period sequentially
- Compares the final strategy balance against a 7% annual benchmark (compounded per period)
- Supports optional drawdown protection: suppresses new buys when portfolio drops X% below peak
- Answers: is this edge consistent across a decade of data, or was one period just lucky?

**RWF Optimizer** (`rwf_optimizer.py`):
- Runs a 45-combination matrix: 9 reoptimization configs × 5 drawdown thresholds
- Reopt configs cover monthly, quarterly, and 6-monthly test periods with 3m/6m/1y/18m training windows
- Drawdown thresholds tested: None, 10%, 15%, 16%, 20%
- History loaded once and shared across all 45 runs
- Results ranked by final compounded balance
- Crash-safe: every window saved to DB as it completes; interrupted runs resume with `--resume`

### Database
All runs persist to `backtester/results/trading.db` (SQLite):
- `test_runs` — metadata per grid search / walk-forward run
- `test_results` — one row per parameter combination per run
- `equity_curves` — daily portfolio value per combination (used for charting)
- `rolling_wf_runs` — metadata per rolling walk-forward run
- `rolling_wf_windows` — one row per window (best params, returns, compounded balances)
- `rwf_optimizer_runs` — metadata per optimizer matrix run

---

## Tax Report

Run `python main.py report` to see:

- Prompts for base salary at runtime (not stored anywhere)
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
│   ├── backtest.py                # Grid search backtester (8,640 combos)
│   ├── walkforward.py             # Single walk-forward analysis
│   ├── rolling_walkforward.py     # Rolling walk-forward (variable windows)
│   ├── rwf_optimizer.py           # 45-run optimizer matrix
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

---

## Backtest Results

### 1-year grid search (no transaction costs)
- **Best:** +89.5% — `min_rel_vol=1.75`, `min_change=0%`, `take_profit=1%`, `stop_loss=-5%`
- **Average across all combinations:** +37.9%
- 298/300 combinations profitable

### 1-year grid search (with transaction costs: slippage=0.05%, spread=0.03%)
- **Best:** +79.3% — `min_rel_vol=1.5`, `min_change=0%`, `take_profit=1%`, `stop_loss=-5%`
- **Average:** +21.4%, **Median:** +21.5%
- 283/300 profitable, 17 losing
- Transaction costs roughly halved average returns vs frictionless

### Rolling walk-forward (2015→2026, 40 quarterly windows, quarterly_1y config, no drawdown limit)
- **Strategy:** $5,000 → $16,562 **(+231.2%)**
- **Benchmark:** $5,000 → $9,836 **(+96.7%)** [7% annual, compounded quarterly]
- **Delta:** +134.5 percentage points vs benchmark
- Profitable quarters: 31/40 (77.5%)
- Beat benchmark: 26/40 (65.0%)
- Best quarter: +27.9% (Q1 2019)
- Worst quarter: -26.1% (Q1 2020, COVID crash)
- Max drawdown: -28.0% (2022 bear market)

### Full 45-run optimizer
*In progress — results to be added here on completion.*

> **Note:** All results reflect survivorship bias — only current S&P 500 constituents are tested against their own historical data. Real-world historical results would be lower. A survivorship-bias-corrected run using historical constituent data is planned.

---

## Known Limitations

- **Survivorship bias** — backtests use today's S&P 500 constituents. Companies that were delisted, went bankrupt, or were removed from the index are not included. This inflates historical returns.
- **Delayed data** — Yahoo Finance free tier is ~15 minutes delayed. Suitable for end-of-day momentum strategies, not intraday or HFT.
- **Sequential simulation** — walk-forward windows must run sequentially (each window's starting balance depends on the previous). Parallelism is applied only within the inner grid search.
- **No shorting** — long-only strategy. Performance during sustained bear markets (e.g. 2022) is weak.
- **Paper trading only** — no real order execution. Live slippage, liquidity constraints, and partial fills are not fully modelled.

---

## Notes

- The backtester uses S&P 500 only — matches the intended live strategy via Alpaca (USD account, no per-trade FX)
- The live bot scans S&P 500 + TSX 60 but will be narrowed to S&P 500 when connected to Alpaca
- `BRK.B` and `BF.B` are not available via yfinance and are silently skipped (~501 of 503 tickers)
- The optimizer is CPU-bound; parallelism scales linearly with physical core count (hyperthreading provides minimal benefit for this workload)
