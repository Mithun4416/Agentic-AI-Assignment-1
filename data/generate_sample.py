"""Generate a synthetic three-month UPI-style bank statement with planted patterns.

Planted facts (these double as golden numbers for the tests):
- Salary credit of 85,000 on the 1st of each month
- Food & Dining: 6,000 in Aug, 6,000 in Sep, 8,400 in Oct (a 40% rise in month three)
- Three recurring subscriptions on fixed dates: Netflix 649 (7th), Spotify 119 (12th),
  Cult.fit 1,499 (15th)  -> 2,267 per month
- One unusually large Shopping transaction: Croma 34,999 on 22 Sep

Run:  python data/generate_sample.py
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent / "sample_statement.csv"
YEAR = 2026
MONTHS = [8, 9, 10]
MONTH_ABBR = {8: "AUG", 9: "SEP", 10: "OCT"}
OPENING_BALANCE = 120_000.00

# Planted golden values
SALARY = 85_000.00
FOOD_TOTALS = {8: 6_000.00, 9: 6_000.00, 10: 8_400.00}
SUBSCRIPTIONS = {"NETFLIX": (7, 649.00), "SPOTIFY": (12, 119.00), "CULTFIT": (15, 1_499.00)}
BIG_PURCHASE = (9, 22, "CROMA ELECTRONICS", 34_999.00)
RENT = 22_000.00
SIP = 5_000.00


@dataclass
class Row:
    day: date
    narration: str
    ref: str
    debit: float | None
    credit: float | None
    true_category: str
    merchant: str


def _ref(rng: random.Random) -> str:
    return str(rng.randint(10**11, 10**12 - 1))


def _split(rng: random.Random, total: float, n: int) -> list[float]:
    """n whole-rupee amounts that sum exactly to total."""
    weights = [rng.uniform(0.6, 1.4) for _ in range(n)]
    s = sum(weights)
    parts = [round(total * w / s) for w in weights]
    parts[-1] += int(total) - sum(parts)
    return [float(p) for p in parts]


def _upi(rng, merchant: str, bank: str, handle: str, note: str, direction: str = "DR") -> tuple[str, str]:
    ref = _ref(rng)
    return f"UPI/{direction}/{ref}/{merchant}/{bank}/{handle}/{note}", ref


def build_rows(seed: int = 42) -> list[Row]:
    rng = random.Random(seed)
    rows: list[Row] = []

    def add(d, narration, ref, debit=None, credit=None, cat="Other", merchant=""):
        rows.append(Row(d, narration, ref, debit, credit, cat, merchant))

    for m in MONTHS:
        mon = MONTH_ABBR[m]

        # Salary on the 1st
        ref = _ref(rng)
        add(date(YEAR, m, 1), f"NEFT CR-HDFC0001234-ACME TECHNOLOGIES PVT LTD-SALARY {mon} {YEAR}",
            ref, credit=SALARY, cat="Income", merchant="ACME TECHNOLOGIES")

        # Rent on the 5th
        n, ref = _upi(rng, "RAMESH KUMAR RENT", "SBIN", "ramesh.k@oksbi", f"Rent {mon}")
        add(date(YEAR, m, 5), n, ref, debit=RENT, cat="Rent", merchant="RAMESH KUMAR RENT")

        # Subscriptions on fixed dates
        for merch, (day, amt) in SUBSCRIPTIONS.items():
            cat = "Health" if merch == "CULTFIT" else "Entertainment & Subscriptions"
            n, ref = _upi(rng, merch, "YESB", f"{merch.lower()}@ybl", "Subscription")
            add(date(YEAR, m, day), n, ref, debit=amt, cat=cat, merchant=merch)

        # SIP on the 10th
        ref = _ref(rng)
        add(date(YEAR, m, 10), f"ACH D- ZERODHA BROKING LTD-SIP{ref[:6]}", ref, debit=SIP,
            cat="Investments", merchant="ZERODHA")

        # ATM withdrawal
        ref = _ref(rng)
        add(date(YEAR, m, 9), f"ATM WDL-MG ROAD BANGALORE-S1AN{ref[:4]}", ref, debit=3_000.00,
            cat="Cash Withdrawal", merchant="ATM")

        # Bills
        n, ref = _upi(rng, "AIRTEL", "ICIC", "airtel@icici", "Postpaid bill")
        add(date(YEAR, m, 18), n, ref, debit=799.00, cat="Bills & Utilities", merchant="AIRTEL")
        n, ref = _upi(rng, "BESCOM", "HDFC", "bescom@hdfcbank", "Electricity")
        add(date(YEAR, m, 20), n, ref, debit={8: 1_650.0, 9: 1_820.0, 10: 1_910.0}[m],
            cat="Bills & Utilities", merchant="BESCOM")

        # Food & Dining (planted totals; one café per month that the rules do not know)
        n_orders = {8: 12, 9: 12, 10: 15}[m]
        amounts = _split(rng, FOOD_TOTALS[m], n_orders)
        cafe = {8: "CHAAYOS", 9: "SWIGGY", 10: "THIRD WAVE COFFEE"}[m]
        for i, amt in enumerate(amounts):
            merch = cafe if i == 0 else ("SWIGGY" if i % 2 else "ZOMATO")
            n, ref = _upi(rng, merch, "YESB", f"{merch.replace(' ', '').lower()}@ybl", "Order")
            add(date(YEAR, m, rng.randint(2, 28)), n, ref, debit=amt, cat="Food & Dining", merchant=merch)

        # Groceries
        for amt in _split(rng, {8: 4_200.0, 9: 4_500.0, 10: 4_350.0}[m], 6):
            merch = rng.choice(["BLINKIT", "BIGBASKET", "DMART", "ZEPTO"])
            n, ref = _upi(rng, merch, "ICIC", f"{merch.lower()}@icici", "Groceries")
            add(date(YEAR, m, rng.randint(2, 28)), n, ref, debit=amt, cat="Groceries", merchant=merch)

        # Transport
        for amt in _split(rng, {8: 2_400.0, 9: 2_600.0, 10: 2_500.0}[m], 10):
            merch = rng.choice(["UBER", "OLA", "RAPIDO", "BMRCL METRO"])
            n, ref = _upi(rng, merch, "AXIS", f"{merch.split()[0].lower()}@axisbank", "Ride")
            add(date(YEAR, m, rng.randint(2, 28)), n, ref, debit=amt, cat="Transport", merchant=merch)

        # Shopping (small)
        for amt in _split(rng, {8: 2_800.0, 9: 3_100.0, 10: 2_600.0}[m], 3):
            merch = rng.choice(["AMAZON", "MYNTRA", "FLIPKART"])
            n, ref = _upi(rng, merch, "HDFC", f"{merch.lower()}@hdfcbank", "Order")
            add(date(YEAR, m, rng.randint(2, 28)), n, ref, debit=amt, cat="Shopping", merchant=merch)

        # Person-to-person transfers (rules cannot know these names)
        friends = {8: ["PRIYA SHARMA", "ROHAN DAS"], 9: ["ROHAN DAS", "ANKIT VERMA"],
                   10: ["PRIYA SHARMA", "ANKIT VERMA"]}[m]
        for friend in friends:
            handle = friend.split()[0].lower() + "@okhdfcbank"
            n, ref = _upi(rng, friend, "HDFC", handle, "Split")
            add(date(YEAR, m, rng.randint(2, 28)), n, ref, debit=float(rng.choice([500, 800, 1200])),
                cat="Transfers", merchant=friend)

    # One-off items
    d_m, d_d, merch, amt = BIG_PURCHASE
    ref = _ref(rng)
    add(date(YEAR, d_m, d_d), f"POS 4591XXXXXXXX3321 {merch} BANGALORE", ref, debit=amt,
        cat="Shopping", merchant="CROMA ELECTRONICS")
    n, ref = _upi(rng, "DECATHLON", "ICIC", "decathlon@icici", "Sports gear")
    add(date(YEAR, 8, 24), n, ref, debit=2_199.00, cat="Shopping", merchant="DECATHLON")
    n, ref = _upi(rng, "BOOKMYSHOW", "YESB", "bookmyshow@ybl", "Movie")
    add(date(YEAR, 9, 13), n, ref, debit=760.00, cat="Entertainment & Subscriptions", merchant="BOOKMYSHOW")
    n, ref = _upi(rng, "UDEMY", "HDFC", "udemy@hdfcbank", "Course")
    add(date(YEAR, 9, 26), n, ref, debit=499.00, cat="Education", merchant="UDEMY")
    n, ref = _upi(rng, "PHARMEASY", "AXIS", "pharmeasy@axisbank", "Medicines")
    add(date(YEAR, 10, 17), n, ref, debit=640.00, cat="Health", merchant="PHARMEASY")
    n, ref = _upi(rng, "ARJUN MEHTA", "ICIC", "arjun@okicici", "Trip share", direction="CR")
    add(date(YEAR, 9, 28), n, ref, credit=1_500.00, cat="Transfers", merchant="ARJUN MEHTA")

    rows.sort(key=lambda r: r.day)  # stable: keeps insertion order within a day
    return rows


def inr(x: float) -> str:
    """Format with Indian digit grouping: 123456.5 -> '1,23,456.50'."""
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + f"{whole}.{frac}"


def write_csv(path: Path = OUT, seed: int = 42) -> Path:
    rows = build_rows(seed)
    balance = OPENING_BALANCE
    with path.open("w", newline="", encoding="utf-8") as f:
        f.write("ACME BANK LTD - Statement of Account\n")
        f.write("Account Holder: DEMO USER,,,,,,\n")
        f.write("Account No: 50100123456789,,,,,,\n")
        f.write(f"Statement Period: 01/08/{YEAR} to 31/10/{YEAR},,,,,,\n")
        f.write("\n")
        w = csv.writer(f)
        w.writerow(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.",
                    "Deposit Amt.", "Closing Balance"])
        for r in rows:
            balance += (r.credit or 0) - (r.debit or 0)
            d = r.day.strftime("%d/%m/%y")
            w.writerow([d, r.narration, r.ref, d,
                        inr(r.debit) if r.debit else "",
                        inr(r.credit) if r.credit else "",
                        inr(balance)])
    return path


if __name__ == "__main__":
    p = write_csv()
    print(f"Wrote {len(build_rows())} transactions to {p}")
