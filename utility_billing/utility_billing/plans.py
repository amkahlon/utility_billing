# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Installment plans (increment 15; FS-WF-05, FS-BIL-14, FS-CFG-18).

A plan splits what an account owes in arrears, surcharge and fees into a down payment and N monthly installments. Each bill from
the first month on **adds one installment** to what is payable within the due date (the rest of the arrears is not yet due),
and the bill shows it as its own row (FS-BIL-15). An installment not paid by the due date (and grace) **cancels the plan**: the
remaining installments are cancelled, the full arrears fall due again and the bills ask for them. While a plan is active the
account is left out of recovery runs."""

from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from utility_billing.utility_billing import account
from utility_billing.utility_billing.readings import month_start


# ---------------------------------------------------------------- pure
def schedule(amount, down_payment, n, first_month) -> list[tuple[int, object, Decimal]]:
	"""[(number, billing month, amount)]: what is left after the down payment in n equal installments, the last one taking the
	rounding (whole rupees)."""
	left = Decimal(str(flt(amount) - flt(down_payment)))
	if n < 1 or left <= 0:
		return []
	each = (left / n).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
	rows = []
	for i in range(n):
		part = each if i < n - 1 else left - each * (n - 1)
		rows.append((i + 1, add_months(month_start(first_month), i), part))
	return rows


# ---------------------------------------------------------------- state
def owed_arrears(connection) -> float:
	"""What the account owes in arrears, surcharge and fees (not the current bill)."""
	b = account.balances(connection)
	return flt(b["arrears"]) + flt(b["surcharge"]) + flt(b["fees"])


def active_plan(connection):
	return frappe.db.get_value("Installment Plan", {"connection": connection, "docstatus": 1, "plan_status": "Active"}, "name")


def installment_for(connection, month):
	"""(plan name, row) of the installment the bill of this month carries: the earliest scheduled one due by then, or None."""
	plan = active_plan(connection)
	if not plan:
		return None
	rows = frappe.get_all("Installment Row", filters={"parent": plan, "parenttype": "Installment Plan", "status": "Scheduled",
	                                                   "billing_month": ["<=", month_start(month)]},
	                      fields=["name", "number", "amount", "billing_month"], order_by="billing_month, number", limit=1)
	return (plan, rows[0]) if rows else None


def mark_billed(bill):
	"""After a bill carrying an installment is posted (or replaced by a revised bill): link the row to it."""
	if not (bill.get("installment_plan") and bill.get("installment_no")):
		return
	name = frappe.db.get_value("Installment Row", {"parent": bill.installment_plan, "number": bill.installment_no}, "name")
	if name:
		frappe.db.set_value("Installment Row", name, {"status": "Billed", "bill": bill.name}, update_modified=False)


def paid_since(connection, from_date, to_date=None) -> float:
	"""Payments received on the account from a date (not reversed)."""
	row = frappe.db.sql("""select -sum(amount) v from `tabCustomer Ledger Entry` where connection = %s and entry_type = 'Payment'
	                       and ifnull(reversed_by, '') = '' and posting_date >= %s and posting_date <= %s""",
	                    (connection, getdate(from_date), getdate(to_date or nowdate())))
	return flt(row[0][0]) if row and row[0][0] else 0.0


def down_payment_received(plan) -> bool:
	return flt(plan.down_payment) <= 0 or paid_since(plan.connection, plan.created_on) >= flt(plan.down_payment) - 0.005


# ---------------------------------------------------------------- the daily check
def daily(as_of=None, society=None):
	"""An installment paid is marked Paid; one missed cancels the plan (FS-WF-05). Returns (marked paid, plans defaulted)."""
	as_of = getdate(as_of or nowdate())
	paid = defaulted = 0
	filters = {"docstatus": 1, "plan_status": "Active"}
	if society:
		filters["society"] = society
	for name in frappe.get_all("Installment Plan", filters=filters, pluck="name"):
		plan = frappe.get_doc("Installment Plan", name)
		missed = False
		for row in plan.schedule:
			if row.status != "Billed" or not row.bill:
				continue
			bill = frappe.db.get_value("Utility Bill", row.bill, ["issue_date", "due_date", "extended_due_date", "payable_within_due", "society"], as_dict=True)
			deadline = getdate(account.surcharge_deadline(bill))
			covered = paid_since(plan.connection, bill.issue_date, deadline) >= flt(bill.payable_within_due) - 0.005
			if covered:
				frappe.db.set_value("Installment Row", row.name, "status", "Paid", update_modified=False)
				paid += 1
			elif as_of > deadline:
				frappe.db.set_value("Installment Row", row.name, "status", "Missed", update_modified=False)
				missed = True
		if missed:
			default_plan(plan)
			defaulted += 1
		elif all(r.status in ("Paid", "Cancelled") for r in frappe.get_all("Installment Row", filters={"parent": name}, fields=["status"])):
			plan.db_set("plan_status", "Completed")
	return paid, defaulted


def default_plan(plan):
	"""A missed installment: the remaining ones are cancelled and the full arrears fall due again."""
	for row in frappe.get_all("Installment Row", filters={"parent": plan.name, "status": "Scheduled"}, pluck="name"):
		frappe.db.set_value("Installment Row", row, "status", "Cancelled", update_modified=False)
	plan.db_set("plan_status", "Defaulted")
	plan.add_comment("Info", _("An installment was not paid in time, so the plan is cancelled and the full arrears are due again."))
