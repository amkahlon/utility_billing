# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Year end (FS-RUN-12): balances carried forward and the year's receivables and revenue summaries.

The customer ledger is continuous, so a balance never needs moving into the next year; closing the year fixes what was owed
at the end of it (by bucket, by account) and the revenue billed by charge, and requires every month of the year to be closed.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, now_datetime

from utility_billing.utility_billing import periods
from utility_billing.utility_billing import rights

CLOSERS = {"System Manager", "Billing Manager", "General Manager", "Accounts Officer"}
BUCKETS = ("arrears", "current", "surcharge", "fees", "credit")


def months_of(start, end):
	d, out = periods.bounds(start)[0], []
	while d <= getdate(end):
		out.append(d)
		d = add_months(d, 1)
	return out


def compute(society, start, end) -> dict:
	"""The year's figures straight from the ledger and the posted bills."""
	start, end = getdate(start), getdate(end)
	sums = frappe.db.sql(
		"""select entry_type, sum(amount) from `tabCustomer Ledger Entry` where society = %s and posting_date between %s and %s group by entry_type""",
		(society, start, end))
	by_type = {t: flt(a) for t, a in sums}
	opening = flt(frappe.db.sql("select sum(amount) from `tabCustomer Ledger Entry` where society = %s and posting_date < %s", (society, start))[0][0])
	closing = flt(frappe.db.sql("select sum(amount) from `tabCustomer Ledger Entry` where society = %s and posting_date <= %s", (society, end))[0][0])
	billed = by_type.get("Bill", 0.0)
	charges = by_type.get("Surcharge", 0.0) + by_type.get("Fee", 0.0)
	collected = -by_type.get("Payment", 0.0)
	adjusted = sum(a for t, a in by_type.items() if t not in ("Bill", "Surcharge", "Fee", "Payment"))

	rows = frappe.db.sql(
		"""select connection, sum(d_arrears), sum(d_current), sum(d_surcharge), sum(d_fees), sum(d_credit)
		from `tabCustomer Ledger Entry` where society = %s and posting_date <= %s group by connection order by connection""", (society, end))
	names = dict(frappe.get_all("Connection", filters={"society": society}, fields=["name", "consumer_name"], as_list=True))
	balances, totals = [], {b: 0.0 for b in BUCKETS}
	for conn, arrears, current, surcharge, fees, credit in rows:
		vals = dict(zip(BUCKETS, (flt(arrears), flt(current), flt(surcharge), flt(fees), flt(credit)), strict=True))
		if not any(abs(v) > 0.004 for v in vals.values()):
			continue
		for b in BUCKETS:
			totals[b] += vals[b]
		balances.append({"connection": conn, "consumer_name": names.get(conn), **vals,
		                 "total": flt(vals["arrears"] + vals["current"] + vals["surcharge"] + vals["fees"] - vals["credit"], 2)})

	revenue = frappe.db.sql(
		"""select ifnull(l.charge_group, ''), l.label, count(distinct b.name), sum(l.amount)
		from `tabBill Line` l join `tabUtility Bill` b on b.name = l.parent and l.parenttype = 'Utility Bill'
		where b.society = %s and b.docstatus = 1 and b.issue_date between %s and %s group by 1, 2 order by 1, 2""", (society, start, end))
	return {
		"accounts": len(balances), "opening_balance": opening, "total_billed": billed, "total_surcharge": charges,
		"total_collected": collected, "total_adjusted": adjusted, "closing_balance": closing,
		"closing_arrears": totals["arrears"], "closing_current": totals["current"], "closing_surcharge": totals["surcharge"],
		"closing_fees": totals["fees"], "closing_credit": totals["credit"],
		"revenue": [{"charge_group": g, "label": lab, "bills": n, "amount": flt(a)} for g, lab, n, a in revenue],
		"balances": balances,
	}


def _check_months(doc):
	open_months = [m for m in months_of(doc.year_start, doc.year_end)
	               if not frappe.db.exists("Billing Period", {"society": doc.society, "period_start": m, "status": "Closed"})]
	if open_months:
		shown = ", ".join(m.strftime("%b %Y") for m in open_months[:6]) + (f" (+{len(open_months) - 6} more)" if len(open_months) > 6 else "")
		frappe.throw(_("Close every month of the year first. Not closed: {0}.").format(shown))


def _fill(doc):
	data = compute(doc.society, doc.year_start, doc.year_end)
	for k, v in data.items():
		if k not in ("revenue", "balances"):
			doc.set(k, v)
	doc.set("revenue", data["revenue"])
	doc.set("balances", data["balances"])


def _doc(name):
	if not rights.can("period.close"):
		raise frappe.PermissionError(_("Only a Billing Manager, Accounts Officer or General Manager can work on the year end."))
	doc = frappe.get_doc("Year End Close", name)
	if doc.status == "Closed":
		frappe.throw(_("{0} is already closed.").format(doc.close_title))
	return doc


@frappe.whitelist(methods=["POST"])
def prepare(name: str):
	"""Work out the year's figures from the ledger (can be repeated until the year is closed)."""
	doc = _doc(name)
	_fill(doc)
	doc.flags.ignore_permissions = True
	doc.save()
	return {"accounts": doc.accounts, "closing_balance": doc.closing_balance}


@frappe.whitelist(methods=["POST"])
def close(name: str):
	"""Fix the year: every month closed, figures recomputed, the balances carried forward."""
	doc = _doc(name)
	_check_months(doc)
	_fill(doc)
	check = flt(doc.opening_balance) + flt(doc.total_billed) + flt(doc.total_surcharge) - flt(doc.total_collected) + flt(doc.total_adjusted)
	if abs(check - flt(doc.closing_balance)) > 0.01:
		frappe.throw(_("The year's movements don't add up to the closing balance (off by Rs {0}); investigate before closing.").format(f"{check - flt(doc.closing_balance):,.2f}"))
	doc.status = "Closed"
	doc.closed_by, doc.closed_on = frappe.session.user, now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	return {"status": doc.status, "closing_balance": doc.closing_balance}
