# STATUS.md — debt-payoff-planner

Built: October 30, 2026 by opencode, for Asdale Dynamics, LLC.

## What was built

`debt_payoff.py` — a single-file, fully offline Python CLI (stdlib only,
Python 3.10+) that turns debt dread into a month-by-month plan:

- **CSV import** of debts (`name`, `balance`, `apr`, optional `min_payment`),
  with friendly column aliases, `$`/`,`/`%` tolerant values, duplicate/negative
  rejection, and fallback minimum payments (`max($25, 1% of balance)`) when the
  column is omitted.
- **Snowball** (smallest balance first — morale wins front-loaded) and
  **avalanche** (highest APR first — least interest) strategies.
- **Month-by-month projection** with monthly interest accrual, minimums to all
  debts, and every spare dollar cascaded onto the focus debt (freed-up minimums
  roll forward automatically).
- **`compare`** — extra-payment scenarios side by side: debt-free date, months,
  interest in round numbers, and interest saved vs the no-extra baseline
  (extras are sorted ascending so the baseline is always first).
- **Interest saved in round numbers** (nearest $10) alongside exact cents.
- **Morale wins** — each debt paid off is labeled `WIN #n` in all outputs;
  first-win date is called out as the smallest-first morale boost.
- **Exports** — `calendar` (one row per month, per-debt payments, totals,
  win notes in the NOTES column) and `tracker` (friendly Markdown with a
  debts-in-payoff-order table, monthly checkboxes, and win callouts).
- **Honest failure modes** — if minimums cannot outrun interest it warns
  plainly instead of inventing a payoff date; runaway exponential growth
  (balance past $1,000,000,000) stops the projection with guidance.
- `sample-csv` command generates an example CSV with fictional debts
  (mirrored in `examples/example_debts.csv`). No real or personal data anywhere.

Supporting files: `README.md` (usage, CSV format, assumptions, companion-guide
note), MIT `LICENSE` ("Copyright (c) 2026 Asdale Dynamics, LLC"),
`tests/test_debt_payoff.py` (pytest suite).

## How to run it

```bash
python debt_payoff.py sample-csv                                  # example CSV
python debt_payoff.py plan example_debts.csv -s snowball -e 100   # projection
python debt_payoff.py compare example_debts.csv --extras 0,50,100 # scenarios
python debt_payoff.py calendar example_debts.csv -e 100 -o calendar.csv
python debt_payoff.py tracker example_debts.csv -e 100 -o tracker.md
```

Options: `-s/--strategy snowball|avalanche`, `-e/--extra` (extra per month),
`--start YYYY-MM` (first payment month; default next month),
`--max-months N` (default 600). Requires Python 3.10+; nothing else to install.

## Verification results (all run here)

- `python -m pytest tests -q` → **69 passed** (0 failures) in <1s.
- CLI smoke tests (subprocess + pointer checks): `--version`, `sample-csv`,
  `plan` (both strategies), `compare` (stdout and error paths), `calendar -o`
  (file rows validated), `tracker -o` (file written), friendly exit code 2 on
  bad CSV/flag input.
- Simulation spot checks: 0% APR 12-month exact payoff, hand-computed monthly
  interest/balances, snowball vs avalanche ordering, cascade of freed-up
  minimums after a debt dies, never-pays-off cap, runaway-growth stop.
- `import debt_payoff` in a fresh interpreter: clean (no warnings).

## Known gaps / deliberate limits

- Monthly approximation only — real cards accrue daily, so real interest will
  differ from projections by pennies over short plans and more over long ones.
- The CSV has no due-date handling; the calendar assumes every payment goes
  out once per month starting at the `--start` month.
- No interest-rate negotiation workflows and no prepayment-penalty modeling.
- No pip-installability/`pyproject.toml` — intentionally a run-where-you-got-it
  script; copy it anywhere and run `python debt_payoff.py`.
- Education tool only, not financial advice (stated in README and tracker).
