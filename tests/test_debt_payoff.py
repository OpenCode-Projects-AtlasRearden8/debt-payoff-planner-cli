"""Tests for debt-payoff-planner (debt_payoff.py)."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debt_payoff as dp  # noqa: E402


def D(text: str) -> Decimal:
    return Decimal(text)


def debt(
    name: str = "Card",
    balance: str = "1000",
    apr: str = "0",
    min_payment: str = "100",
) -> dp.Debt:
    return dp.Debt(name, D(balance), dp.parse_apr(apr, "test"), D(min_payment))


def make_csv_text(rows: str) -> str:
    return "name,balance,apr,min_payment\n" + rows


# ---------------------------------------------------------------------------
# parsing and money helpers
# ---------------------------------------------------------------------------

class TestMoneyHelpers:
    def test_q2_half_up(self):
        assert dp.q2(D("1.005")) == D("1.01")
        assert dp.q2(D("1.004")) == D("1.00")

    def test_round_to_nearest_ten(self):
        assert dp.round_to(D("86.40"), D("10")) == D("90.00")
        assert dp.round_to(D("84.10"), D("10")) == D("80.00")
        assert dp.round_to(D("5.00"), D("10")) == D("10.00")

    def test_fmt_money(self):
        assert dp.fmt_money(D("1234.5")) == "$1,234.50"

    def test_fmt_month(self):
        assert dp.fmt_month(2026, 11) == "Nov 2026"
        assert dp.fmt_month(2027, 1) == "Jan 2027"

    def test_month_iso(self):
        assert dp.month_iso(2026, 3) == "2026-03"

    def test_add_months_rollover(self):
        assert dp.add_months(2026, 11, 2) == (2027, 1)
        assert dp.add_months(2026, 12, 1) == (2027, 1)
        assert dp.add_months(2026, 1, 0) == (2026, 1)

    def test_ordinal_number(self):
        assert dp.ordinal_number(1) == "1st"
        assert dp.ordinal_number(2) == "2nd"
        assert dp.ordinal_number(3) == "3rd"
        assert dp.ordinal_number(11) == "11th"
        assert dp.ordinal_number(21) == "21st"


class TestParseNumbers:
    def test_parse_money_strips_symbols(self):
        parsed = dp.parse_number("$1,250.75", "balance", "row")
        assert parsed == D("1250.75")

    def test_parse_money_rejects_garbage(self):
        with pytest.raises(dp.DebtInputError):
            dp.parse_number("lots", "balance", "row")

    def test_parse_money_rejects_blank(self):
        with pytest.raises(dp.DebtInputError):
            dp.parse_number("  ", "balance", "row")

    def test_apr_percent_form(self):
        assert dp.parse_apr("19.99", "row") == D("0.1999")

    def test_apr_fraction_form(self):
        assert dp.parse_apr("0.1999", "row") == D("0.1999")

    def test_apr_percent_suffix(self):
        assert dp.parse_apr("24.99%", "row") == D("0.2499")

    def test_apr_zero(self):
        assert dp.parse_apr("0", "row") == D("0")

    def test_apr_negative_rejected(self):
        with pytest.raises(dp.DebtInputError):
            dp.parse_apr("-5", "row")


class TestCsvParsing:
    def test_basic_parse(self):
        debts = dp.parse_debts_csv_text(make_csv_text(
            "Store Card,340.00,24.99,25.00\n"))
        assert len(debts) == 1
        assert debts[0].name == "Store Card"
        assert debts[0].balance == D("340.00")
        assert debts[0].apr == D("0.2499")
        assert debts[0].min_payment == D("25.00")

    def test_missing_min_payment_gets_default(self):
        debts = dp.parse_debts_csv_text("name,balance,apr\nTiny,100.00,0\n")
        assert debts[0].min_payment == D("25.00")
        big = dp.parse_debts_csv_text(
            "name,balance,apr\nBig,10000.00,0\n")[0]
        assert big.min_payment == D("100.00")

    def test_alternate_column_names(self):
        debts = dp.parse_debts_csv_text(
            "account,amount owed,interest rate\nA,100,10\n")
        assert debts[0].name == "A"
        assert debts[0].balance == D("100")
        assert debts[0].apr == D("0.10")

    def test_missing_required_column(self):
        with pytest.raises(dp.DebtInputError, match="apr"):
            dp.parse_debts_csv_text("name,balance\nA,100\n")

    def test_blank_name_rejected(self):
        with pytest.raises(dp.DebtInputError, match="name is blank"):
            dp.parse_debts_csv_text(make_csv_text(",100,0,10\n"))

    def test_duplicate_names_rejected(self):
        text = make_csv_text("A,100,0,10\nA,200,0,10\n")
        with pytest.raises(dp.DebtInputError, match="duplicate"):
            dp.parse_debts_csv_text(text)

    def test_negative_balance_rejected(self):
        text = make_csv_text("A,-1,0,10\n")
        with pytest.raises(dp.DebtInputError, match="negative"):
            dp.parse_debts_csv_text(text)

    def test_negative_min_payment_rejected(self):
        text = make_csv_text("A,100,0,-1\n")
        with pytest.raises(dp.DebtInputError, match="negative"):
            dp.parse_debts_csv_text(text)

    def test_header_only_rejected(self):
        with pytest.raises(dp.DebtInputError):
            dp.parse_debts_csv_text("name,balance,apr\n")

    def test_row_number_in_error(self):
        text = make_csv_text("A,100,0,10\nB,abc,0,10\n")
        with pytest.raises(dp.DebtInputError, match="CSV row 3"):
            dp.parse_debts_csv_text(text)

    def test_example_csv_matches_sample_command(self):
        example = Path(__file__).resolve().parent.parent / "examples" / \
            "example_debts.csv"
        debts = dp.load_debts(str(example))
        assert [d.name for d in debts] == [
            "Store Card", "Medical Bills", "Credit Card", "Car Loan",
        ]
        assert example.read_text(encoding="utf-8") == dp.example_csv_text()


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------

class TestSimulation:
    def test_zero_apr_pays_off_exactly(self):
        plan = dp.simulate([debt(balance="1200", min_payment="100")],
                           start=(2026, 11))
        assert plan.months == 12
        assert plan.total_interest == D("0.00")
        assert plan.total_paid == D("1200.00")
        assert plan.wins[-1] == (12, "Card")
        assert plan.capped is False
        assert plan.final_year == 2027 and plan.final_month == 10

    def test_monthly_row_labels(self):
        plan = dp.simulate([debt(balance="200", min_payment="100")],
                           start=(2026, 11))
        assert [(row.year, row.month) for row in plan.rows] == [
            (2026, 11), (2026, 12),
        ]

    def test_interest_accrues_before_payment(self):
        plan = dp.simulate([debt(balance="1000", apr="12", min_payment="60")],
                           start=(2026, 11), max_months=3)
        assert [row.interest["Card"] for row in plan.rows] == [
            D("10.00"), D("9.50"), D("9.00"),
        ]
        assert [row.ending["Card"] for row in plan.rows] == [
            D("950.00"), D("899.50"), D("848.50"),
        ]

    def test_interest_consistency(self):
        plan = dp.simulate([debt(balance="1200", apr="12", min_payment="110")],
                           start=(2026, 11))
        assert not plan.capped
        assert plan.total_paid - plan.initial_total == plan.total_interest

    def test_snowball_order(self):
        debts = [debt("A", "500", "5", "25"), debt("B", "800", "20", "25"),
                 debt("C", "300", "10", "25")]
        plan = dp.simulate(debts, strategy="snowball", start=(2026, 11))
        assert plan.order == ["C", "A", "B"]

    def test_avalanche_order(self):
        debts = [debt("A", "500", "5", "25"), debt("B", "800", "20", "25"),
                 debt("C", "300", "10", "25")]
        plan = dp.simulate(debts, strategy="avalanche", start=(2026, 11))
        assert plan.order == ["B", "C", "A"]

    def test_extra_goes_to_focus_debt_first(self):
        plan = dp.simulate(
            [debt("First", "100", "0", "25"), debt("Second", "300", "0", "25")],
            strategy="snowball", extra=D("50"), start=(2026, 11),
        )
        month1 = plan.rows[0]
        assert month1.payments == {"First": D("75.00"),
                                   "Second": D("25.00")}

    def test_cascade_after_payoff(self):
        plan = dp.simulate(
            [debt("First", "100", "0", "25"), debt("Second", "300", "0", "25")],
            strategy="snowball", extra=D("50"), start=(2026, 11),
        )
        assert plan.win_month("First") == 2
        month2 = plan.rows[1]
        assert month2.payments == {"Second": D("75.00"), "First": D("25.00")}

    def test_leftover_minimums_cascade(self):
        plan = dp.simulate(
            [debt("Tiny", "10", "0", "25"), debt("Big", "100", "0", "25")],
            strategy="snowball", extra=D("0"), start=(2026, 11),
        )
        month1 = plan.rows[0]
        assert month1.payments["Tiny"] == D("10.00")
        assert month1.payments["Big"] == D("40.00")
        assert plan.win_month("Tiny") == 1

    def test_zero_balance_debt_skipped(self):
        plan = dp.simulate(
            [debt("Gone", "0", "0", "0"), debt("Live", "100", "0", "50")],
            start=(2026, 11),
        )
        assert plan.skipped_zero == ["Gone"]
        assert plan.order == ["Live"]
        assert plan.months == 2

    def test_runaway_growth_stops_friendly(self):
        plan = dp.simulate(
            [debt("Trap", "1000", "120", "5")], start=(2026, 11),
            max_months=600,
        )
        assert plan.runaway is True
        assert plan.capped is True
        assert plan.total_interest > plan.initial_total
        text = dp.render_plan_text(plan)
        assert "projection had to stop" in text

    def test_never_pays_off_is_capped(self):
        plan = dp.simulate(
            [debt("Trap", "1000", "12", "5")], start=(2026, 11),
            max_months=24,
        )
        assert plan.capped is True
        assert plan.months == 24
        assert plan.wins == []

    def test_empty_debt_list(self):
        plan = dp.simulate([], start=(2026, 11))
        assert plan.rows == []
        assert plan.months == 0

    def test_unknown_strategy_rejected(self):
        with pytest.raises(dp.DebtInputError):
            dp.simulate([debt()], strategy="tsunami", start=(2026, 11))

    def test_negative_extra_clamped(self):
        plan = dp.simulate([debt(balance="100", min_payment="100")],
                           extra=D("-5"), start=(2026, 11))
        assert plan.extra == D("0.00")

    def test_larger_extra_finishes_no_later(self):
        debts = [debt("A", "500", "19.99", "25"),
                 debt("B", "3000", "7.49", "150")]
        base = dp.simulate(debts, start=(2026, 11))
        boosted = dp.simulate(debts, extra=D("100"), start=(2026, 11))
        assert boosted.months < base.months
        assert boosted.total_interest < base.total_interest


class TestPlanResultGeometry:
    def test_final_month_evolution(self):
        plan = dp.simulate([debt(balance="1200", min_payment="100")],
                           start=(2026, 11))
        rows = plan.rows
        assert (rows[0].year, rows[0].month) == (2026, 11)
        assert (rows[-1].year, rows[-1].month) == (2027, 10)

    def test_still_owed_marches_down(self):
        plan = dp.simulate([debt(balance="600", min_payment="250")],
                           start=(2026, 11))
        owed = [row.still_owed for row in plan.rows]
        assert owed == [D("350.00"), D("100.00"), D("0.00")]


# ---------------------------------------------------------------------------
# reports and exports
# ---------------------------------------------------------------------------

def example_plan(extra: Decimal = D("100")) -> dp.PlanResult:
    debts = dp.parse_debts_csv_text(dp.example_csv_text())
    return dp.simulate(debts, strategy="snowball", extra=extra,
                       start=(2026, 11))


class TestPlanText:
    def test_contains_key_facts(self):
        text = dp.render_plan_text(example_plan())
        assert "Snowball plan" in text
        assert "MONTH-BY-MONTH PROJECTION" in text
        assert "PAID OFF" in text
        assert "win #1" in text
        assert "Debt-free in" in text
        assert "call it about" in text

    def test_first_win_is_smallest_balance(self):
        text = dp.render_plan_text(example_plan(D("0")))
        assert "Store Card paid off in Month" in text

    def test_capped_plan_warns(self):
        plan = dp.simulate([debt("Trap", "1000", "12", "5")],
                           start=(2026, 11), max_months=24)
        text = dp.render_plan_text(plan)
        assert "Heads up" in text

    def test_empty_plan(self):
        plan = dp.simulate([debt("Gone", "0", "0", "0")], start=(2026, 11))
        text = dp.render_plan_text(plan)
        assert "already paid off" in text


class TestCompareText:
    def test_savings_monotonic_and_nonnegative(self):
        debts = dp.parse_debts_csv_text(dp.example_csv_text())
        extras = [D("0"), D("50"), D("150")]
        plans = [
            dp.simulate(debts, strategy="avalanche", extra=e,
                        start=(2026, 11))
            for e in extras
        ]
        text = dp.render_compare_text(plans, extras)
        assert "$150.00" in text
        assert "Saved vs base" in text
        interest = Decimal("999999999")
        for plan in plans:
            assert plan.total_interest <= interest
            interest = plan.total_interest

    def test_parse_extras_sorts_and_dedupes(self):
        assert dp.parse_extras("100,0,100,50") == [
            D("0.00"), D("50.00"), D("100.00"),
        ]

    def test_parse_extras_dollar_signs(self):
        assert dp.parse_extras("$25") == [D("25.00")]

    def test_parse_extras_rejects_negative(self):
        with pytest.raises(dp.DebtInputError):
            dp.parse_extras("-10")


class TestCalendarCsv:
    def test_header_and_columns(self):
        plan = example_plan()
        rows = list(csv.reader(io.StringIO(dp.calendar_csv_text(plan))))
        assert rows[0][:4] == ["month_number", "month", "date", "Store Card"]
        assert "Car Loan" in rows[0]
        for name in ("TOTAL_PAYMENT", "INTEREST_PAID", "STILL_OWED", "NOTES"):
            assert name in rows[0]

    def test_one_row_per_month_with_win_note(self):
        plan = example_plan()
        rows = list(csv.reader(io.StringIO(dp.calendar_csv_text(plan))))
        assert len(rows) - 1 == plan.months
        win_row = rows[3]
        assert win_row[1] == "Jan 2027"
        assert "Store Card paid off" in win_row[-1]
        assert any(row[-1] for row in rows[1:])

    def test_totals_match_plan(self):
        plan = example_plan()
        text = dp.calendar_csv_text(plan)
        reader = csv.DictReader(io.StringIO(text))
        total = sum(D(row["TOTAL_PAYMENT"]) for row in reader)
        assert total == plan.total_paid


class TestTrackerMarkdown:
    def test_structure(self):
        plan = example_plan()
        text = dp.tracker_markdown_text(plan)
        assert text.startswith("# Debt Payoff Tracker - Snowball plan")
        assert "## Debts, in payoff order" in text
        assert "## Monthly checklist" in text
        assert "## The numbers, plainly" in text
        assert "24.99%" in text
        assert "not financial advice" in text

    def test_checklist_covers_every_month(self):
        plan = example_plan()
        text = dp.tracker_markdown_text(plan)
        boxes = [line for line in text.splitlines()
                 if line.startswith("- [ ] Month ")]
        assert len(boxes) == plan.months
        wins = [line for line in text.splitlines() if "WIN #" in line]
        assert len(wins) == len(plan.wins)

    def test_win_lines_and_payoff_order_table(self):
        plan = example_plan()
        text = dp.tracker_markdown_text(plan)
        assert "**Store Card** PAID OFF" in text
        assert "1. Store Card" in text
        assert "4. Car Loan" in text

    def test_capped_tracker(self):
        plan = dp.simulate([debt("Trap", "1000", "12", "5")],
                           start=(2026, 11), max_months=24)
        text = dp.tracker_markdown_text(plan)
        assert "bigger extra" in text


# ---------------------------------------------------------------------------
# CLI (subprocess)
# ---------------------------------------------------------------------------

def run_cli(*arguments, cwd=None):
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent
                             / "debt_payoff.py"), *arguments],
        capture_output=True, text=True, cwd=cwd or "/tmp/opencode",
    )


class TestCli:
    def test_version(self):
        result = run_cli("--version")
        assert result.returncode == 0
        assert "debt-payoff-planner 1.0.0" in result.stdout

    def test_sample_csv_then_plan(self, tmp_path):
        sample = tmp_path / "sample_debts.csv"
        result = run_cli("sample-csv", "--out", str(sample))
        assert result.returncode == 0
        assert sample.exists()
        debts = dp.parse_debts_csv_text(sample.read_text(encoding="utf-8"))
        assert len(debts) == 4
        plan_result = run_cli("plan", str(sample), "-s", "snowball",
                              "-e", "100")
        assert plan_result.returncode == 0
        assert "Snowball plan" in plan_result.stdout
        assert "MONTH-BY-MONTH PROJECTION" in plan_result.stdout

    def test_plan_avalanche(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("plan", example, "-s", "avalanche")
        assert result.returncode == 0
        assert "Avalanche plan" in result.stdout

    def test_compare(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("compare", example, "--extras", "0,100")
        assert result.returncode == 0
        assert "SCENARIO COMPARISON" in result.stdout
        assert "$100.00" in result.stdout

    def test_compare_bad_extras(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("compare", example, "--extras", "abc")
        assert result.returncode == 2
        assert "Hmm" in result.stderr

    def test_calendar_to_file(self, tmp_path):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        out = tmp_path / "calendar.csv"
        result = run_cli("calendar", example, "-o", str(out))
        assert result.returncode == 0
        assert "calendar" in result.stdout
        rows = list(csv.reader(out.open(encoding="utf-8")))
        assert rows[0][0] == "month_number"

    def test_tracker_to_stdout(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("tracker", example)
        assert result.returncode == 0
        assert "# Debt Payoff Tracker" in result.stdout

    def test_missing_file_friendly_error(self):
        result = run_cli("plan", "nope_not_here.csv")
        assert result.returncode == 2
        assert "Hmm" in result.stderr

    def test_bad_start_month(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("plan", example, "--start", "not-a-date")
        assert result.returncode == 2
        assert "--start" in result.stderr

    def test_start_month_honored(self):
        example = str(Path(__file__).resolve().parent.parent
                      / "examples" / "example_debts.csv")
        result = run_cli("plan", example, "--start", "2027-01")
        assert result.returncode == 0
        assert "Jan 2027" in result.stdout
