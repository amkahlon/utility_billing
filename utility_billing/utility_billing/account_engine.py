# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Customer account rules (FS-REC-01, 10-12; FS-PAY-07; FS-CFG-17). Pure functions, no database.

An account keeps separate balances (FS-REC-11): current bill, arrears, surcharge, fees, and credit
(money paid in advance). What the consumer owes = current + arrears + surcharge + fees - credit.

- A new bill moves the unpaid current bill into arrears, sets the new current bill, then uses any
  credit on the account, in the posting order.
- A payment pays the balances in the society's posting order (default current bill, then arrears,
  then surcharge; fees last); anything left over becomes credit.
- A reversal undoes an entry's changes. If that would leave a balance below zero (for example a fee
  that was paid, then reversed), the money moves to credit; a negative credit moves to arrears.
"""

from decimal import ROUND_HALF_UP, Decimal

BUCKETS = ("current", "arrears", "surcharge", "fees")
ALL = BUCKETS + ("credit",)
ORDERS = {
	"Current bill, Arrears, Surcharge": ("current", "arrears", "surcharge", "fees"),
	"Arrears, Current bill, Surcharge": ("arrears", "current", "surcharge", "fees"),
	"Oldest first": ("arrears", "surcharge", "current", "fees"),
}
DEFAULT_ORDER = ORDERS["Current bill, Arrears, Surcharge"]
CENT = Decimal("0.01")


def D(x) -> Decimal:
	return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def order_of(setting) -> tuple:
	return ORDERS.get(setting or "", DEFAULT_ORDER)


def empty() -> dict:
	return {b: D(0) for b in ALL}


def total(bal: dict) -> Decimal:
	"""What the consumer owes (negative = in credit)."""
	return sum((D(bal[b]) for b in BUCKETS), D(0)) - D(bal["credit"])


def apply(bal: dict, deltas: dict) -> dict:
	return {b: D(bal.get(b)) + D(deltas.get(b)) for b in ALL}


def diff(before: dict, after: dict) -> dict:
	return {b: D(after[b]) - D(before[b]) for b in ALL}


def allocate(bal: dict, amount, order=DEFAULT_ORDER) -> tuple[dict, Decimal]:
	"""Split `amount` over the owed balances in `order`. Returns (paid per balance, amount left over)."""
	left = D(amount)
	paid = {b: D(0) for b in BUCKETS}
	for b in order:
		take = min(max(D(bal[b]), D(0)), left)
		paid[b] = take
		left -= take
	return paid, left


def normalise(bal: dict) -> dict:
	"""No balance below zero: an overpaid balance becomes credit; a negative credit becomes arrears."""
	bal = dict(bal)
	for b in BUCKETS:
		if bal[b] < 0:
			bal["credit"] += -bal[b]
			bal[b] = D(0)
	if bal["credit"] < 0:
		bal["arrears"] += -bal["credit"]
		bal["credit"] = D(0)
	return bal


def use_credit(bal: dict, order) -> dict:
	"""Pay owed balances from the account's credit."""
	if bal["credit"] <= 0:
		return bal
	paid, left = allocate(bal, bal["credit"], order)
	after = {b: bal[b] - paid[b] for b in BUCKETS}
	after["credit"] = left
	return after


def bill(bal: dict, current_amount, order=DEFAULT_ORDER) -> dict:
	"""Deltas for posting a new bill."""
	after = dict(bal)
	after["arrears"] = bal["arrears"] + bal["current"]
	after["current"] = D(current_amount)
	after = use_credit(normalise(after), order)
	return diff(bal, after)


def payment(bal: dict, amount, order=DEFAULT_ORDER) -> dict:
	"""Deltas for a payment: pays balances in order, the rest becomes credit."""
	paid, left = allocate(bal, amount, order)
	deltas = {b: -paid[b] for b in BUCKETS}
	deltas["credit"] = left
	return deltas


def charge(bal: dict, bucket: str, amount, order=DEFAULT_ORDER) -> dict:
	"""Deltas for adding a charge (surcharge, fee, debit note) to one balance, using credit if any."""
	after = dict(bal)
	after[bucket] = bal[bucket] + D(amount)
	return diff(bal, use_credit(after, order))


def credit_note(bal: dict, bucket: str, amount) -> dict:
	"""Deltas for a credit note on one balance. It can't lower a balance below zero."""
	amount = D(amount)
	if amount > D(bal[bucket]):
		raise ValueError(f"The {bucket} balance is only {bal[bucket]}.")
	return {**{b: D(0) for b in ALL}, bucket: -amount}


def reverse(bal: dict, deltas: dict) -> dict:
	"""Deltas that undo an earlier entry's deltas, kept within the rules above."""
	after = normalise(apply(bal, {b: -D(deltas.get(b)) for b in ALL}))
	return diff(bal, after)
