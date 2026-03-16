import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
TRADES_FILE = DATA_DIR / "trades.json"

# --- Constants ---
BASE_SALARY = 0

# Federal brackets 2025
FEDERAL_BRACKETS = [
    (57_375,       0.1500),
    (114_750,      0.2050),
    (158_519,      0.2600),
    (220_000,      0.2900),
    (float("inf"), 0.3300),
]

# Ontario provincial brackets 2025
ONTARIO_BRACKETS = [
    (51_446,       0.0505),
    (102_894,      0.0915),
    (150_000,      0.1116),
    (220_000,      0.1216),
    (float("inf"), 0.1316),
]

FEDERAL_BASIC_PERSONAL  = 15_705
ONTARIO_BASIC_PERSONAL  = 11_141
ONTARIO_SURTAX_1_THRESHOLD = 5_315   # 20% surtax on Ontario tax above this
ONTARIO_SURTAX_2_THRESHOLD = 6_802   # additional 36% on Ontario tax above this


# --- Tax math ---

def _bracket_tax(income: float, brackets: list[tuple], basic_personal: float) -> float:
    taxable = max(0.0, income - basic_personal)
    tax = 0.0
    prev = 0.0
    for limit, rate in brackets:
        if taxable <= prev:
            break
        tax += (min(taxable, limit) - prev) * rate
        prev = limit
    return tax


def _ontario_surtax(ontario_tax: float) -> float:
    surtax = 0.0
    if ontario_tax > ONTARIO_SURTAX_1_THRESHOLD:
        surtax += 0.20 * (ontario_tax - ONTARIO_SURTAX_1_THRESHOLD)
    if ontario_tax > ONTARIO_SURTAX_2_THRESHOLD:
        surtax += 0.36 * (ontario_tax - ONTARIO_SURTAX_2_THRESHOLD)
    return surtax


def total_tax(income: float) -> float:
    """Combined federal + Ontario tax on a given total income."""
    fed = _bracket_tax(income, FEDERAL_BRACKETS, FEDERAL_BASIC_PERSONAL)
    ont = _bracket_tax(income, ONTARIO_BRACKETS, ONTARIO_BASIC_PERSONAL)
    ont += _ontario_surtax(ont)
    return round(fed + ont, 2)


def effective_rate(income: float) -> float:
    return round((total_tax(income) / income) * 100, 2) if income > 0 else 0.0


# --- Trade parsing ---

def _parse_trades() -> tuple[list[dict], list[dict]]:
    """
    Returns (closed_trades, open_buys) by FIFO matching of BUY/SELL pairs.
    """
    if not TRADES_FILE.exists():
        return [], []

    raw = json.loads(TRADES_FILE.read_text())
    queues = defaultdict(list)
    closed = []

    for t in raw:
        if t["action"] == "BUY":
            queues[t["ticker"]].append(t)
        elif t["action"] == "SELL" and queues[t["ticker"]]:
            buy = queues[t["ticker"]].pop(0)
            buy_dt  = datetime.fromisoformat(buy["timestamp"])
            sell_dt = datetime.fromisoformat(t["timestamp"])
            closed.append({
                "ticker":       t["ticker"],
                "buy_date":     buy["timestamp"][:10],
                "sell_date":    t["timestamp"][:10],
                "shares":       t["shares"],
                "buy_price":    buy["price"],
                "sell_price":   t["price"],
                "realized_pnl": t["realized_pnl"],
                "holding_days": max(0, (sell_dt - buy_dt).days),
            })

    open_buys = [t for queue in queues.values() for t in queue]
    return closed, open_buys


# --- Report ---

def generate_report():
    closed, open_buys = _parse_trades()

    W = 66
    print(f"\n{'='*W}")
    print(f"  TAX REPORT  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Base salary: ${BASE_SALARY:,}  |  Province: Ontario  |  Tax year: {datetime.now().year}")
    print(f"{'='*W}")

    # ── Closed trades ──────────────────────────────────────────────
    print(f"\n── CLOSED TRADES ({'none yet' if not closed else f'{len(closed)} trade(s)'})")

    total_pnl    = 0.0
    wins         = 0
    losses       = 0
    holding_days = []

    if closed:
        print(f"\n  {'Ticker':<10} {'Buy':<12} {'Sell':<12} {'Days':>5}  {'Shares':>9}  {'P&L':>10}")
        print(f"  {'-'*(W-2)}")
        for t in closed:
            pnl = t["realized_pnl"]
            total_pnl += pnl
            holding_days.append(t["holding_days"])
            wins += pnl >= 0
            losses += pnl < 0
            print(f"  {t['ticker']:<10} {t['buy_date']:<12} {t['sell_date']:<12} "
                  f"{t['holding_days']:>5}  {t['shares']:>9.4f}  ${pnl:>+9.4f}")

        avg_hold = sum(holding_days) / len(holding_days)
        print(f"\n  Gross realized P&L : ${total_pnl:>+.4f}")
        print(f"  Wins / Losses      : {wins} / {losses}  "
              f"({'%.0f' % (wins/len(closed)*100)}% win rate)")
        print(f"  Avg holding period : {avg_hold:.1f} days")

    # ── Open positions ─────────────────────────────────────────────
    if open_buys:
        print(f"\n── OPEN POSITIONS ({len(open_buys)}) — unrealized P&L excluded from tax calculation")
        print(f"\n  {'Ticker':<10} {'Entry Date':<12} {'Entry Price':>12}  {'Shares':>9}")
        print(f"  {'-'*(W-2)}")
        for t in open_buys:
            print(f"  {t['ticker']:<10} {t['timestamp'][:10]:<12} ${t['price']:>11.2f}  {t['shares']:>9.4f}")

    # ── Tax analysis ───────────────────────────────────────────────
    print(f"\n── TAX ANALYSIS  (realized gains only, 2025 brackets)")

    if total_pnl == 0.0:
        print("\n  No realized gains or losses yet — nothing to calculate.\n")
        return

    tax_salary_only = total_tax(BASE_SALARY)

    # Capital gains (50% inclusion)
    cg_inclusion     = total_pnl * 0.50
    cg_total_income  = BASE_SALARY + cg_inclusion
    cg_tax           = total_tax(cg_total_income)
    cg_incremental   = round(cg_tax - tax_salary_only, 2)
    cg_after_tax     = round(total_pnl - cg_incremental, 4)

    # Business income (100% inclusion)
    bi_total_income  = BASE_SALARY + total_pnl
    bi_tax           = total_tax(bi_total_income)
    bi_incremental   = round(bi_tax - tax_salary_only, 2)
    bi_after_tax     = round(total_pnl - bi_incremental, 4)

    preferred = "Capital Gains" if cg_after_tax >= bi_after_tax else "Business Income"
    advantage  = round(abs(cg_after_tax - bi_after_tax), 4)

    print(f"""
  Tax on salary alone ($0)       : ${tax_salary_only:>10.2f}

  {"":30}  {"Capital Gains":>14}  {"Business Income":>15}
  {"─"*W}
  Inclusion rate                       {"50%":>14}  {"100%":>15}
  Taxable trading income               ${cg_inclusion:>+13.4f}  ${total_pnl:>+14.4f}
  Total taxable income                 ${cg_total_income:>13.2f}  ${bi_total_income:>14.2f}
  Total tax owing                      ${cg_tax:>13.2f}  ${bi_tax:>14.2f}
  Incremental tax on trading           ${cg_incremental:>+13.4f}  ${bi_incremental:>+14.4f}
  After-tax trading profit             ${cg_after_tax:>+13.4f}  ${bi_after_tax:>+14.4f}
  {"─"*W}
  Preferred treatment: {preferred}  (${advantage:.4f} more after tax)""")

    # ── CRA indicators ─────────────────────────────────────────────
    print(f"\n── CRA CLASSIFICATION INDICATORS")

    if not closed:
        print("\n  No closed trades yet — indicators will appear after first sell.\n")
        return

    first_dt     = datetime.fromisoformat(min(t["buy_date"] for t in closed) + "T00:00:00")
    days_active  = max(1, (datetime.now() - first_dt).days)
    monthly_freq = (len(closed) / days_active) * 30
    avg_hold     = sum(holding_days) / len(holding_days)

    def indicator(bad: bool, warn_msg: str, ok_msg: str) -> str:
        return f"  {'[!]' if bad else '[ ]'}  {warn_msg if bad else ok_msg}"

    print()
    print(indicator(monthly_freq > 10,
        f"High frequency: {monthly_freq:.1f} trades/month — points toward business income",
        f"Moderate frequency: {monthly_freq:.1f} trades/month"))
    print(indicator(avg_hold < 30,
        f"Short avg holding period: {avg_hold:.1f} days — points toward business income",
        f"Avg holding period: {avg_hold:.1f} days — consistent with capital gains"))
    print(indicator(losses == 0 and len(closed) >= 5,
        "No losing trades — CRA may view systematic approach as business activity",
        f"Win/loss ratio looks normal ({wins}W / {losses}L)"))

    print(f"""
  Note: CRA determines capital gains vs business income based on
  intent, frequency, holding period, and other factors. These
  indicators are informational only — consult a tax professional
  before filing.
""")
