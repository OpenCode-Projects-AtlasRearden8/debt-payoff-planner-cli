#!/usr/bin/env python3
"""debt-payoff-planner: turn debt dread into a month-by-month plan.

Fully offline, standard-library only. Import balances and rates from a CSV,
pick a snowball or avalanche strategy, project payoff dates month by month,
compare extra-payment scenarios side by side, and export a payment calendar
CSV plus a friendly Markdown progress tracker.

This tool does the math only; it is not financial advice.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"
DEFAULT_MAX_MONTHS = 600
DEFAULT_MIN_PAYMENT_FLOOR = Decimal("25.00")
MIN_PAYMENT_PCT = Decimal("0.01")
ZERO = Decimal("0.00")
ONE_CENT = Decimal("0.01")
ROUNDED_DISPLAY = Decimal("10")

NAME_ALIASES = {"name", "debt", "account", "account name", "description"}
BALANCE_ALIASES = {
    "balance", "balance amount", "amount", "amount owed",
    "starting balance", "current balance",
}
APR_ALIASES = {"apr", "rate", "interest rate", "interest apr"}
MIN_PAYMENT_ALIASES = {
    "min_payment", "minimum payment", "min payment",
    "minimum payment amount", "min-payment",
}
MONTH_ABBREVIATIONS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
STRATEGIES = ("snowball", "avalanche")
STRATEGY_DESCRIPTIONS = {
    "snowball": "smallest balance first (fast wins for morale)",
    "avalanche": "highest interest rate first (least interest overall)",
}


class DebtInputError(ValueError):
    """Raised when a debt CSV or a CLI value cannot be read or understood."""


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------

def parse_number(text: str, field_name: str, where: str) -> Decimal:
    raw = (text or "").strip()
    if not raw:
        raise DebtInputError(f"{where}: {field_name} is blank.")
    cleaned = raw.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise DebtInputError(
            f"{where}: {field_name} is not a number I understand: {raw!r}"
        ) from None


def parse_apr(text: str, where: str) -> Decimal:
    raw = (text or "").strip().rstrip("%").strip()
    value = parse_number(raw, "apr", where)
    if value < 0:
        raise DebtInputError(f"{where}: apr cannot be negative ({raw!r}).")
    if value >= Decimal("0.5"):
        value = value / Decimal(100)
    return value


def parse_start_month(text: str | None) -> tuple[int, int]:
    raw = (text or "").strip()
    if not raw:
        return next_month(*today_year_month())
    iso_text = raw if len(raw.split("-")) == 3 else raw + "-01"
    try:
        parsed = date.fromisoformat(iso_text)
    except ValueError:
        raise DebtInputError(
            f"--start must look like 2026-11 or 2026-11-01, not {raw!r}"
        ) from None
    return parsed.year, parsed.month


def today_year_month() -> tuple[int, int]:
    now = date.today()
    return now.year, now.month


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def add_months(year: int, month: int, count: int) -> tuple[int, int]:
    offset = (month - 1) + count
    return year + offset // 12, offset % 12 + 1


# ---------------------------------------------------------------------------
# money helpers
# ---------------------------------------------------------------------------

def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_to(value: Decimal, nearest: Decimal) -> Decimal:
    if nearest <= 0:
        return q2(value)
    bucket = (value / nearest).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (bucket * nearest).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal) -> str:
    return f"${q2(value):,.2f}"


def fmt_month(year: int, month: int) -> str:
    return f"{MONTH_ABBREVIATIONS[month - 1]} {year}"


def month_iso(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def ordinal_number(value: int) -> str:
    if value % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


# ---------------------------------------------------------------------------
# debts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Debt:
    name: str
    balance: Decimal
    apr: Decimal
    min_payment: Decimal

    @property
    def monthly_rate(self) -> Decimal:
        return self.apr / Decimal(12)


def default_min_payment(balance: Decimal) -> Decimal:
    guess = q2(balance * MIN_PAYMENT_PCT)
    if guess < DEFAULT_MIN_PAYMENT_FLOOR:
        guess = DEFAULT_MIN_PAYMENT_FLOOR
    return guess


def resolve_columns(header: list[str]) -> tuple[int, int, int, int | None]:
    lowered = [cell.strip().lower() for cell in header]

    def find(aliases: set[str]) -> int | None:
        for alias in aliases:
            if alias in lowered:
                return lowered.index(alias)
        return None

    name_idx = find(NAME_ALIASES)
    balance_idx = find(BALANCE_ALIASES)
    apr_idx = find(APR_ALIASES)
    min_idx = find(MIN_PAYMENT_ALIASES)
    missing = []
    if name_idx is None:
        missing.append("name")
    if balance_idx is None:
        missing.append("balance")
    if apr_idx is None:
        missing.append("apr")
    if missing:
        raise DebtInputError(
            "Missing required column(s): " + ", ".join(missing)
            + f". I found these headers: {', '.join(header)}."
        )
    return name_idx, balance_idx, apr_idx, min_idx


def parse_debts_csv_text(text: str) -> list[Debt]:
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise DebtInputError(
            "The CSV needs a header row plus at least one debt row."
        )
    name_idx, balance_idx, apr_idx, min_idx = resolve_columns(rows[0])
    debts: list[Debt] = []
    names_seen: set[str] = set()
    for data_row_number, row in enumerate(rows[1:], start=2):
        where = f"CSV row {data_row_number}"

        def cell(index: int | None) -> str:
            if index is None or index >= len(row):
                return ""
            return row[index]

        name = cell(name_idx).strip()
        if not name:
            raise DebtInputError(f"{where}: name is blank.")
        if name in names_seen:
            raise DebtInputError(
                f"{where}: duplicate debt name {name!r}; "
                "each debt needs a unique name."
            )
        balance = parse_number(cell(balance_idx), "balance", where)
        if balance < 0:
            raise DebtInputError(f"{where}: balance cannot be negative.")
        apr = parse_apr(cell(apr_idx), where)
        min_text = cell(min_idx).strip() if min_idx is not None else ""
        if min_text:
            min_payment = parse_number(min_text, "min_payment", where)
        else:
            min_payment = default_min_payment(balance)
        if min_payment < 0:
            raise DebtInputError(f"{where}: min_payment cannot be negative.")
        names_seen.add(name)
        debts.append(Debt(name, q2(balance), apr, q2(min_payment)))
    if not debts:
        raise DebtInputError("No debt rows found in the CSV.")
    return debts


def load_debts(path: str) -> list[Debt]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            text = handle.read()
    except OSError as error:
        raise DebtInputError(f"I could not open {path!r}: {error}") from None
    return parse_debts_csv_text(text)


def example_csv_text() -> str:
    return (
        "name,balance,apr,min_payment\n"
        "Store Card,340.00,24.99,25.00\n"
        "Medical Bills,600.00,0,30.00\n"
        "Credit Card,1450.00,19.99,45.00\n"
        "Car Loan,3250.00,7.49,180.00\n"
    )


# ---------------------------------------------------------------------------
# plan simulation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonthRow:
    number: int
    year: int
    month: int
    payments: dict[str, Decimal]
    interest: dict[str, Decimal]
    ending: dict[str, Decimal]
    wins: tuple[str, ...]
    still_owed: Decimal


@dataclass
class PlanResult:
    strategy: str
    extra: Decimal
    start_year: int
    start_month: int
    order: list[str]
    apr_by_name: dict[str, Decimal]
    start_balance_by_name: dict[str, Decimal]
    rows: list[MonthRow] = field(default_factory=list)
    wins: list[tuple[int, str]] = field(default_factory=list)
    months: int = 0
    total_interest: Decimal = ZERO
    total_paid: Decimal = ZERO
    initial_total: Decimal = ZERO
    min_budget: Decimal = ZERO
    skipped_zero: list[str] = field(default_factory=list)
    capped: bool = False
    runaway: bool = False
    max_months: int = DEFAULT_MAX_MONTHS

    CEILING = Decimal("1000000000")

    @property
    def budget(self) -> Decimal:
        return q2(self.min_budget + self.extra)

    @property
    def final_year(self) -> int:
        return add_months(self.start_year, self.start_month,
                          self.months - 1)[0]

    @property
    def final_month(self) -> int:
        return add_months(self.start_year, self.start_month,
                          self.months - 1)[1]

    def month_label_after(self, month_number: int) -> str:
        year, month = add_months(self.start_year, self.start_month,
                                 month_number - 1)
        return fmt_month(year, month)

    def win_month(self, debt_name: str) -> int | None:
        for month_number, name in self.wins:
            if name == debt_name:
                return month_number
        return None


def strategy_order(debts: list[Debt], strategy: str) -> list[Debt]:
    active = [debt for debt in debts if debt.balance > 0]
    if strategy == "snowball":
        return sorted(active, key=lambda d: (d.balance, -d.apr, d.name))
    return sorted(active, key=lambda d: (-d.apr, d.balance, d.name))


def simulate(
    debts: list[Debt],
    strategy: str = "snowball",
    extra: Decimal = ZERO,
    start: tuple[int, int] | None = None,
    max_months: int = DEFAULT_MAX_MONTHS,
) -> PlanResult:
    if strategy not in STRATEGIES:
        raise DebtInputError(
            f"Unknown strategy {strategy!r}; use snowball or avalanche."
        )
    extra = q2(max(ZERO, extra))
    if start is None:
        start = next_month(*today_year_month())
    start_year, start_month = start

    ordered = strategy_order(debts, strategy)
    result = PlanResult(
        strategy=strategy,
        extra=extra,
        start_year=start_year,
        start_month=start_month,
        order=[debt.name for debt in ordered],
        apr_by_name={debt.name: debt.apr for debt in ordered},
        start_balance_by_name={debt.name: q2(debt.balance) for debt in ordered},
        skipped_zero=[debt.name for debt in debts if debt.balance <= 0],
        max_months=max_months,
    )
    if not ordered:
        return result

    balances = {debt.name: q2(debt.balance) for debt in ordered}
    rates = {debt.name: debt.monthly_rate for debt in ordered}
    min_pay = {debt.name: max(ONE_CENT, q2(debt.min_payment))
               for debt in ordered}
    result.initial_total = q2(sum(balances.values()))
    result.min_budget = q2(sum(min_pay.values()))
    remaining = list(debt.name for debt in ordered)
    ordered_names = [debt.name for debt in ordered]

    for month_number in range(1, max_months + 1):
        if not remaining:
            break
        if any(balances[name] > result.CEILING for name in remaining):
            result.runaway = True
            result.capped = True
            break
        interest_paid: dict[str, Decimal] = {}
        payments: dict[str, Decimal] = {}
        for name in remaining:
            accrued = q2(balances[name] * rates[name])
            balances[name] = q2(balances[name] + accrued)
            interest_paid[name] = accrued
        cascade = extra
        for name in remaining:
            due = min(min_pay[name], balances[name])
            payments[name] = due
            balances[name] = q2(balances[name] - due)
            cascade += min_pay[name] - due
        for name in remaining:
            if q2(cascade) <= ZERO:
                break
            due = min(q2(cascade), balances[name])
            if due > ZERO:
                payments[name] = q2(payments[name] + due)
                balances[name] = q2(balances[name] - due)
                cascade -= due
        wins_this_month: list[str] = []
        for name in ordered_names:
            if name in remaining and balances[name] <= ZERO:
                balances[name] = ZERO
                remaining.remove(name)
                wins_this_month.append(name)
                result.wins.append((month_number, name))
        still_owed = q2(sum(balances.values()))
        year, month = add_months(start_year, start_month, month_number - 1)
        result.rows.append(MonthRow(
            number=month_number,
            year=year,
            month=month,
            payments=dict(payments),
            interest=dict(interest_paid),
            ending=dict(balances),
            wins=tuple(wins_this_month),
            still_owed=still_owed,
        ))
        result.total_interest = q2(
            result.total_interest + sum(interest_paid.values()))
        result.total_paid = q2(result.total_paid + sum(payments.values()))
        result.months = month_number
        result.capped = bool(remaining)
    return result


def load_plan(
    csv_path: str,
    strategy: str,
    extra: Decimal,
    start_text: str | None,
    max_months: int,
) -> tuple[list[Debt], PlanResult]:
    debts = load_debts(csv_path)
    plan = simulate(debts, strategy=strategy, extra=extra,
                    start=parse_start_month(start_text), max_months=max_months)
    return debts, plan


# ---------------------------------------------------------------------------
# text reports
# ---------------------------------------------------------------------------

def budget_line(plan: PlanResult) -> str:
    parts = [f"{fmt_money(plan.min_budget)} in minimums"]
    if plan.extra > 0:
        parts.append(f"{fmt_money(plan.extra)} extra")
    return " + ".join(parts) + f" = {fmt_money(plan.budget)}/month"


def month_total(row: MonthRow, amounts: dict[str, Decimal]) -> Decimal:
    return q2(sum(amounts.values()))


def render_plan_text(plan: PlanResult) -> str:
    strategy_title = plan.strategy.capitalize()
    lines: list[str] = []
    lines.append(f"{strategy_title} plan - {budget_line(plan)}")
    lines.append(f"({plan.strategy}: {STRATEGY_DESCRIPTIONS[plan.strategy]})")
    if plan.skipped_zero:
        lines.append(
            "Already at $0, skipped in the projection: "
            + ", ".join(plan.skipped_zero)
        )
    if not plan.rows:
        lines.append(
            "Nothing to plan here - these debts are already paid off. "
            "Congratulations."
        )
        return "\n".join(lines)
    if plan.runaway:
        lines.append(
            "Heads up: with this budget the balances grow faster than "
            "the payments can shrink them - the projection had to stop. "
            "You will need a bigger extra payment and likely lower rates; "
            "ask your lenders."
        )
    elif plan.capped:
        lines.append(
            f"Heads up: with this budget the interest outgrows the payments "
            f"and debt remains after {plan.max_months} months. Try a bigger "
            "extra payment (see the 'compare' command) or ask your lenders "
            "about lower rates."
        )
    else:
        lines.append(
            f"Debt-free in {plan.months} months "
            f"({fmt_month(plan.final_year, plan.final_month)})."
        )
    first = plan.wins[0] if plan.wins else None
    if first:
        rank = 1
        lines.append(
            f"First win (smallest-first morale boost): {first[1]} paid off "
            f"in Month {first[0]} ({plan.month_label_after(first[0])}) = "
            f"win #1."
        )
    lines.append(
        f"Total interest: {fmt_money(plan.total_interest)} "
        f"(call it about "
        f"{fmt_money(round_to(plan.total_interest, ROUNDED_DISPLAY))}) "
        f"on {fmt_money(plan.initial_total)} of balances."
    )
    lines.append(f"Total paid toward debt: {fmt_money(plan.total_paid)}")
    lines.append("")
    lines.append("MONTH-BY-MONTH PROJECTION")
    header = (f"{'Mth':>3}  {'Month':<10}{'Payment':>10}"
              f"{'Interest':>10}{'Still owed':>12}  Notes")
    lines.append(header)
    lines.append("-" * len(header))
    for row in plan.rows:
        notes: list[str] = []
        for rank, (win_number, win_name) in enumerate(plan.wins, start=1):
            if win_name in row.wins and win_number == row.number:
                notes.append(f"{win_name} PAID OFF (win #{rank})")
        if not notes and row.number % 12 == 0:
            notes.append(f"year {row.number // 12}")
        lines.append(
            f"{row.number:>3}  {fmt_month(row.year, row.month):<10}"
            f"{fmt_money(month_total(row, row.payments)):>10}"
            f"{fmt_money(month_total(row, row.interest)):>10}"
            f"{fmt_money(row.still_owed):>12}"
            + ("  " + "; ".join(notes) if notes else "")
        )
    if not plan.capped:
        lines.append("-" * len(header))
        lines.append(
            f"{plan.months:>3}  {'TOTAL':<10}"
            f"{fmt_money(plan.total_paid):>10}"
            f"{fmt_money(plan.total_interest):>10}"
            f"{'$0.00':>12}"
        )
    lines.append("")
    lines.append(
        "Want more motivation? Run 'compare' to see how a bigger "
        "extra payment moves your debt-free date."
    )
    return "\n".join(lines)


def render_compare_text(plans: list[PlanResult], extras: list[Decimal]) -> str:
    strategy = plans[0].strategy if plans else "snowball"
    lines: list[str] = []
    lines.append(f"SCENARIO COMPARISON - {strategy}")
    lines.append(f"({strategy}: {STRATEGY_DESCRIPTIONS[strategy]})")
    lines.append("")
    header = (f"{'Extra/mo':>10}{'Debt-free':>12}{'Months':>9}"
              f"{'Interest':>13}{'Saved vs base':>16}")
    lines.append(header)
    lines.append("-" * len(header))
    base = plans[0] if plans else None
    for plan in plans:
        payoff = (
            fmt_month(plan.final_year, plan.final_month)
            if not plan.capped else f"> {plan.max_months} mo"
        )
        months = str(plan.months) if not plan.capped else "--"
        if plan is base:
            saved_text = "-"
        else:
            saved = q2(base.total_interest - plan.total_interest)
            saved_text = fmt_money(round_to(saved, ROUNDED_DISPLAY))
        lines.append(
            f"{fmt_money(plan.extra):>10}{payoff:>12}{months:>9}"
            f"{fmt_money(round_to(plan.total_interest, ROUNDED_DISPLAY)):>13}"
            f"{saved_text:>16}"
        )
    lines.append("-" * len(header))
    if base is not None and not base.capped and plans[-1] is not base \
            and not plans[-1].capped:
        best = plans[-1]
        saved = q2(base.total_interest - best.total_interest)
        lines.append(
            f"Quick takeaway: an extra {fmt_money(best.extra)}/month "
            f"finishes {base.months - best.months} month(s) sooner and "
            f"saves about "
            f"{fmt_money(round_to(saved, ROUNDED_DISPLAY))} in interest "
            "vs the base plan."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------

def calendar_csv_text(plan: PlanResult) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["month_number", "month", "date"] + plan.order
        + ["TOTAL_PAYMENT", "INTEREST_PAID", "STILL_OWED", "NOTES"]
    )
    for row in plan.rows:
        notes: list[str] = []
        for rank, (win_number, win_name) in enumerate(plan.wins, start=1):
            if win_name in row.wins and win_number == row.number:
                notes.append(f"WIN #{rank}: {win_name} paid off")
        writer.writerow(
            [row.number, fmt_month(row.year, row.month),
             month_iso(row.year, row.month)]
            + [q2(row.payments.get(name, ZERO)) for name in plan.order]
            + [
                month_total(row, row.payments),
                month_total(row, row.interest),
                row.still_owed,
                "; ".join(notes),
            ]
        )
    return buffer.getvalue()


def tracker_markdown_text(plan: PlanResult) -> str:
    strategy_title = plan.strategy.capitalize()
    lines: list[str] = []
    lines.append(f"# Debt Payoff Tracker - {strategy_title} plan")
    lines.append("")
    lines.append(f"Budget: {budget_line(plan)}")
    if plan.skipped_zero:
        lines.append(
            f"Already at $0 (outside the projection): "
            f"{', '.join(plan.skipped_zero)}"
        )
    if not plan.rows:
        lines.append("")
        lines.append(
            "Nothing to plan - these debts are already paid off. Nice work."
        )
        return "\n".join(lines) + "\n"
    lines.append("")
    if plan.runaway:
        lines.append(
            "**Heads up:** the balances grow faster than the payments "
            "can shrink them - the projection had to stop. You will need "
            "a bigger extra payment and likely lower rates."
        )
    elif plan.capped:
        lines.append(
            f"**Heads up:** with this budget interest outgrows the "
            f"payments within {plan.max_months} months. Try a bigger extra "
            "payment (see the 'compare' command)."
        )
    else:
        lines.append(
            f"Debt-free date: **{fmt_month(plan.final_year, plan.final_month)}"
            f"** ({plan.months} months of payments)."
        )
    if plan.wins:
        first_month, first_name = plan.wins[0]
        lines.append(
            f"First win: **{first_name}** paid off in Month {first_month} "
            f"({plan.month_label_after(first_month)})."
        )
    lines.append(
        f"Total interest: **{fmt_money(plan.total_interest)}** "
        f"(about {fmt_money(round_to(plan.total_interest, ROUNDED_DISPLAY))})."
    )
    lines.append("")
    lines.append("## Debts, in payoff order")
    lines.append("")
    lines.append("| Debt | Start balance | APR | Payoff |")
    lines.append("|------|--------------:|----:|--------|")
    for rank, name in enumerate(plan.order, start=1):
        win_number = plan.win_month(name)
        if win_number:
            payoff_text = f"Month {win_number} " \
                          f"({plan.month_label_after(win_number)})"
        elif plan.capped:
            payoff_text = "not within the plan cap - needs a bigger budget"
        else:
            payoff_text = "?"
        apr = (plan.apr_by_name.get(name, ZERO) * Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        lines.append(
            f"| {rank}. {name} | "
            f"{fmt_money(plan.start_balance_by_name.get(name, ZERO))} | "
            f"{apr:.2f}% | {payoff_text} |"
        )
    lines.append("")
    lines.append("## Monthly checklist")
    lines.append("")
    lines.append("Check a box as each month's payments go out.")
    lines.append("")
    for row in plan.rows:
        detail = ", ".join(
            f"{name} {fmt_money(amount)}"
            for name, amount in row.payments.items()
            if amount > ZERO
        )
        lines.append(
            f"- [ ] Month {row.number} - {fmt_month(row.year, row.month)}: "
            f"pay **{fmt_money(month_total(row, row.payments))}** "
            f"total ({detail})"
        )
        for rank, (win_number, win_name) in enumerate(plan.wins, start=1):
            if win_name in row.wins and win_number == row.number:
                final = row.payments.get(win_name, ZERO)
                lines.append(
                    f"    - WIN #{rank}: **{win_name}** PAID OFF with its "
                    f"final payment of {fmt_money(final)}. "
                    f"That is {rank} of {len(plan.wins)} gone - the "
                    "smallest-first wins are stacking up."
                )
    lines.append("")
    lines.append("## The numbers, plainly")
    lines.append("")
    lines.append(f"- Monthly payments: {plan.months}")
    lines.append(f"- Total interest: {fmt_money(plan.total_interest)}")
    lines.append(f"- Total paid: {fmt_money(plan.total_paid)}")
    lines.append("")
    lines.append(
        "This tracker does the math only - it is not financial advice. "
        "Pair it with the daily habits in the credit-and-debt-calm-guide "
        "companion."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="debt_payoff.py",
        description="Turn debt dread into a plan: import a CSV of balances "
        "and rates, pick snowball or avalanche, and project your payoff "
        "month by month. Fully offline. Not financial advice.",
    )
    parser.add_argument("--version", action="version",
                        version=f"debt-payoff-planner {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser(
        "sample-csv", help="write an example debts CSV to get started")
    sample.add_argument("--out", default="example_debts.csv",
                        help="where to write it (default example_debts.csv)")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("csv_file", help="path to your debts CSV")
        sub.add_argument(
            "-s", "--strategy", choices=STRATEGIES, default="snowball",
            help="snowball = smallest balance first; "
            "avalanche = highest APR first (default: snowball)")
        sub.add_argument(
            "-e", "--extra", default="0",
            help="extra payment each month, on top of minimums (default 0)")
        sub.add_argument(
            "--start", default=None,
            help="first payment month as YYYY-MM (default: next month)")
        sub.add_argument(
            "--max-months", type=int, default=DEFAULT_MAX_MONTHS,
            help=f"stop projecting after this many months "
            f"(default {DEFAULT_MAX_MONTHS})")

    plan = subparsers.add_parser(
        "plan", help="show the month-by-month payoff projection")
    add_common(plan)

    compare = subparsers.add_parser(
        "compare", help="compare extra-payment scenarios side by side")
    add_common(compare)
    compare.add_argument(
        "--extras", default="0,50,100",
        help="comma-separated extra-payment scenarios to try "
        "(default: 0,50,100)")

    calendar = subparsers.add_parser(
        "calendar", help="export the payment calendar as a CSV")
    add_common(calendar)
    calendar.add_argument("-o", "--out", default=None,
                          help="write to this file instead of stdout")

    tracker = subparsers.add_parser(
        "tracker", help="export a friendly Markdown progress tracker")
    add_common(tracker)
    tracker.add_argument("-o", "--out", default=None,
                         help="write to this file instead of stdout")
    return parser


def parse_extras(text: str) -> list[Decimal]:
    extras: list[Decimal] = []
    for chunk in (text or "").split(","):
        chunk = chunk.strip().replace("$", "")
        if not chunk:
            continue
        value = parse_number(chunk, "extra", "--extras")
        if value < 0:
            raise DebtInputError("Extra payments cannot be negative.")
        extras.append(q2(value))
    if not extras:
        extras = [ZERO]
    return sorted(set(extras))


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def command_sample(args: argparse.Namespace, stdout) -> int:
    write_text(args.out, example_csv_text())
    print(f"Wrote {args.out} with four example debts (fictional numbers).")
    print("Try your first plan:")
    script = "debt_payoff.py"
    print(f"  python {script} plan {args.out} --strategy snowball --extra 100")
    return 0


def run_command(args: argparse.Namespace, stdout) -> int:
    extra = q2(parse_number(args.extra.replace("$", ""), "extra", "--extra"))
    debts, plan = load_plan(
        args.csv_file,
        args.strategy,
        extra,
        args.start,
        args.max_months,
    )
    if args.command == "plan":
        print(render_plan_text(plan), file=stdout)
    elif args.command == "compare":
        start = parse_start_month(args.start)
        extras = parse_extras(args.extras)
        plans = [
            simulate(debts, strategy=args.strategy, extra=scenario_extra,
                     start=start, max_months=args.max_months)
            for scenario_extra in extras
        ]
        print(render_compare_text(plans, extras), file=stdout)
    elif args.command == "calendar":
        text = calendar_csv_text(plan)
        if args.out:
            write_text(args.out, text)
            print(f"Wrote the payment calendar to {args.out}:")
            print(f"  {plan.months} month(s) of payments, "
                  f"{len(plan.wins)} win(s) marked.")
            if not plan.capped:
                print(f"  Debt-free about "
                      f"{fmt_month(plan.final_year, plan.final_month)}.")
        else:
            print(text, file=stdout, end="")
    elif args.command == "tracker":
        text = tracker_markdown_text(plan)
        if args.out:
            write_text(args.out, text)
            print(f"Wrote the progress tracker to {args.out}:")
            if plan.wins and not plan.capped:
                first_month, first_name = plan.wins[0]
                print(f"  First win on the list: {first_name} in "
                      f"Month {first_month}.")
        else:
            print(text, file=stdout, end="")
    else:
        raise DebtInputError(f"Unknown command {args.command!r}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "sample-csv":
            return command_sample(args, sys.stdout)
        return run_command(args, sys.stdout)
    except DebtInputError as error:
        print(f"Hmm - {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
