# debt-payoff-planner

A Python command-line tool that turns debt dread into a plan.

Import your balances and rates from a plain CSV, choose **snowball** (smallest
balance first, for quick wins) or **avalanche** (highest rate first, for the
lowest interest), and get a month-by-month projection of exactly when you are
debt-free. Compare extra-payment scenarios side by side, see interest saved in
round numbers, export a payment calendar CSV, and print a friendly Markdown
progress tracker with morale wins marked as you knock debts out.

Written in explain-it-like-a-friend style: plain English, step by step, never
condescending. Fully offline, standard library only - no installs, no accounts,
no internet, and none of your data ever leaves your machine.

Credit: **Asdale Dynamics, LLC** - part of the ASDALE DYNAMICS beginner-friendly
toolset (books, guides, and practical tools at dylanasdale.github.io).

## Why this exists

Debt is scary because it is vague. "I need to pay this off someday" is dread.
"$380 a month and you are debt-free in 19 months, first card gone by January"
is a plan. This tool does that math for you, month by month, and celebrates
each time a debt dies.

It complements the **credit-and-debt-calm-guide** companion (the daily habits
side) - this tool deliberately does only the numbers so the two do not repeat
each other.

## Quick start

Requires Python 3.10+ (no third-party packages).

```bash
# 1. Generate an example CSV with four fictional debts
python debt_payoff.py sample-csv

# 2. Look at a plan
python debt_payoff.py plan example_debts.csv --strategy snowball --extra 100

# 3. Compare what bigger extra payments buy you
python debt_payoff.py compare example_debts.csv --extras 0,50,100,200

# 4. Export a payment calendar and a printable progress tracker
python debt_payoff.py calendar example_debts.csv --extra 100 -o calendar.csv
python debt_payoff.py tracker example_debts.csv --extra 100 -o tracker.md
```

## Your debts CSV

A simple spreadsheet with a header row:

```csv
name,balance,apr,min_payment
Store Card,340.00,24.99,25.00
Medical Bills,600.00,0,30.00
Credit Card,1450.00,19.99,45.00
Car Loan,3250.00,7.49,180.00
```

| Column | Required | Meaning |
|--------|----------|---------|
| `name` | yes | Any unique label you like |
| `balance` | yes | What you owe today (numbers like `340` or `$1,450.00` both work) |
| `apr` | yes | Annual rate as a percent: `19.99` means 19.99% (`19.99%` and `0.1999` also work) |
| `min_payment` | no | Your usual minimum per month. If blank, the tool assumes `max($25, 1% of balance)` and says so |

Friendly column headers such as `account`, `amount owed`, and `interest rate`
are recognized too. Fictional sample data only ships inside this repo - never
put real account numbers or personal details in a CSV you plan to share.

## What it computes

- **Month-by-month projection** - interest is accrued monthly, the monthly
  budget pays every minimum, and every spare dollar (your extra payment plus
  freed-up minimums) is cascaded onto the focus debt until nothing is left.
- **Payoff dates and totals** - debt-free month, total interest, total paid.
- **Extra-payment comparison** - run several scenarios in one command; shows
  payoff dates and interest saved vs the do-nothing-extra baseline, rounded
  into round numbers you can feel.
- **Morale wins** - every time a debt is paid off it is marked with its win
  number (`WIN #1: Store Card paid off`). Snowball ordering front-loads those
  smallest-first wins on purpose.
- **Honest warnings** - if the interest outgrows the payments (minimums below
  what interest costs), it tells you plainly instead of promising a fake date.

Fine print, kept small: this tool assumes monthly payment cycles, first payment
in the month after you run it (or the `--start` month you give), and cents
rounded half-up each step. It is an education tool, not financial advice -
your real card statement is the tiebreaker.

## Commands

| Command | What it does |
|---------|--------------|
| `sample-csv --out FILE` | Write an example debts CSV |
| `plan CSV` | Month-by-month projection, wins marked |
| `compare CSV --extras 0,50,100` | Side-by-side extra-payment scenarios |
| `calendar CSV -o calendar.csv` | Payment calendar as CSV (one row per month) |
| `tracker CSV -o tracker.md` | Markdown progress tracker with checkboxes |

Options shared by most commands:

- `-s` / `--strategy` `snowball` (default) or `avalanche`
- `-e` / `--extra` extra payment per month, on top of minimums (default `0`)
- `--start YYYY-MM` first payment month (default: next month)
- `--max-months N` stop projecting after this many months (default `600`)

## Pair it with the habits

The plan on paper only pays off if the payments keep going out. The
**credit-and-debt-calm-guide** companion covers that side - the rhythms,
buffers, and low-stress routines. This tool sticks to the math and hands you
the calendar.

## Development

```bash
python -m pytest tests
```

The test suite (69 tests) covers CSV parsing, the simulation engine,
snowball/avalanche ordering, cascade behavior, reports, exports, and the CLI
end to end. Everything runs offline with the standard library and pytest.

## License

MIT - Copyright (c) 2026 Asdale Dynamics, LLC. See `LICENSE`. This project is
an education tool only and is not financial advice.
