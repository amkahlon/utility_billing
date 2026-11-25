# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Impact preview before approving a tariff, a tax or an FPA/QTA adjustment (FS-CFG-07).

It applies the proposal to **last cycle's bills** (the society's latest billing month with posted bills) and shows what each
bill would have been: the difference per consumer (the biggest rises and falls), by category, and in total. Nothing is saved:
the proposal is worked out with the same engine that bills use, with the same readings, charges and adjustments."""

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from utility_billing.utility_billing import bill_engine as engine
from utility_billing.utility_billing import billing, mis, readings, tax_engine
from utility_billing.utility_billing import rights

ALLOWED = set(mis.EXEC + ["Billing Officer", "Society Admin", "Accounts Officer"])
LIMIT = 5000
TOP = 10


def require():
	if not rights.can("preview.view"):
		frappe.throw(_("You can't see impact previews."), frappe.PermissionError)


def last_cycle(society, category=None):
	"""(month, posted bills of that month) for a society, optionally one category."""
	# the latest month with posted bills of that category (another utility's later run, maintenance say, isn't this tariff's last cycle)
	month = frappe.db.get_value("Utility Bill", {"society": society, "docstatus": 1, "status": "Posted", **({"consumer_category": category} if category else {})},
	                            "billing_month", order_by="billing_month desc")
	if not month:
		return None, []
	filters = {"society": society, "docstatus": 1, "status": "Posted", "billing_month": month}
	if category:
		filters["consumer_category"] = category
	bills = frappe.get_all("Utility Bill", filters=filters, limit=LIMIT, order_by="name",
	                       fields=["name", "connection", "consumer", "consumer_name", "consumer_category", "current_amount", "bill_run", "billing_month"])
	return month, bills


def summarise(month, rows, unavailable=0, what="") -> dict:
	"""Totals, by category, and the biggest changes, from rows of {connection, consumer_name, category, old, new}."""
	cats = OrderedDict()
	for r in rows:
		c = cats.setdefault(r["category"], {"category": r["category"], "bills": 0, "old": 0.0, "new": 0.0})
		c["bills"] += 1
		c["old"] += flt(r["old"])
		c["new"] += flt(r["new"])
	by_cat = []
	for c in cats.values():
		diff = round(c["new"] - c["old"], 2)
		by_cat.append({**c, "old": round(c["old"], 2), "new": round(c["new"], 2), "difference": diff,
		               "percent": round(diff / c["old"] * 100, 1) if c["old"] else None})
	old, new = round(sum(flt(r["old"]) for r in rows), 2), round(sum(flt(r["new"]) for r in rows), 2)
	moved = sorted(({**r, "difference": round(flt(r["new"]) - flt(r["old"]), 2)} for r in rows), key=lambda r: r["difference"])
	changed = [r for r in moved if r["difference"]]
	return {"what": what, "month": getdate(month).strftime("%B %Y") if month else None, "bills": len(rows), "unavailable": unavailable,
	        "old_total": old, "new_total": new, "difference": round(new - old, 2), "percent": round((new - old) / old * 100, 1) if old else None,
	        "changed_bills": len(changed), "by_category": by_cat,
	        "biggest_rises": list(reversed(changed[-TOP:])) if changed and changed[-1]["difference"] > 0 else [],
	        "biggest_falls": changed[:TOP] if changed and changed[0]["difference"] < 0 else [],
	        "message": None if rows else _("There are no posted bills from last cycle to apply this to.")}


@frappe.whitelist()
def tariff_impact(name: str, with_rows: int = 0) -> dict:
	"""What last cycle's bills of the tariff's category would have been under this tariff (with_rows: each bill too, for the editor)."""
	require()
	t = frappe.get_doc("Tariff", name)
	month, bills = last_cycle(t.society, t.consumer_category)
	runs, rows, unavailable = {}, [], 0
	for b in bills:
		run = runs.get(b.bill_run) or runs.setdefault(b.bill_run, frappe.get_doc("Bill Run", b.bill_run))
		try:
			new = billing.build_bill(run, b.connection, override={"tariff": t.name, "arrears": 0})
		except billing.BillProblem:
			unavailable += 1
			continue
		rows.append({"connection": b.connection, "consumer_name": b.consumer_name, "category": b.consumer_category,
		             "old": flt(b.current_amount), "new": flt(new["current_amount"])})
	out = summarise(month, rows, unavailable, _("Tariff {0}").format(t.name))
	if frappe.utils.cint(with_rows):
		out["_rows"] = rows
	return out


@frappe.whitelist()
def tax_impact(name: str) -> dict:
	"""What this tax would have added to (or, if already on, changed on) last cycle's bills."""
	require()
	t = frappe.get_doc("Tax", name)
	t.check_permission("read")
	month, bills = last_cycle(t.society)
	rows = []
	for b in bills:
		consumer = frappe.db.get_value("Consumer", b.consumer, ["tax_status", "tax_exempt"], as_dict=True) or {}
		lines = [(l.charge_type, l.charge_group, flt(l.amount)) for l in frappe.get_all(
			"Bill Line", filters={"parent": b.name, "parenttype": "Utility Bill"}, fields=["charge_type", "charge_group", "amount", "tax"]) if not l.tax]
		without = billing.taxes_for(t.society, b.billing_month, b.consumer_category, consumer, without=t.name)
		with_it = billing.taxes_for(t.society, b.billing_month, b.consumer_category, consumer, without=t.name, extra=t)
		old = tax_engine.added(tax_engine.calculate_taxes(lines, without))
		new = tax_engine.added(tax_engine.calculate_taxes(lines, with_it))
		rows.append({"connection": b.connection, "consumer_name": b.consumer_name, "category": b.consumer_category,
		             "old": flt(b.current_amount), "new": flt(b.current_amount) - flt(old) + flt(new)})
	return summarise(month, rows, 0, _("Tax {0}").format(t.name))


@frappe.whitelist()
def adjustment_impact(name: str) -> dict:
	"""What this FPA or QTA would have charged on last cycle's bills, against what they carried for the same charge."""
	require()
	a = frappe.get_doc("Tariff Adjustment", name)
	a.check_permission("read")
	month, bills = last_cycle(a.society)
	from utility_billing.utility_billing import billing  # noqa: PLC0415

	rows, unavailable = [], 0
	for b in bills:
		units = readings.get_billable_units(b.connection, a.reference_month)
		if units is None:  # a month billed by the previous system: its history (increment 57, as the bill does)
			units = readings.legacy_units(b.connection, a.reference_month)
		if units is None:
			unavailable += 1
			continue
		exempt = billing.exemption_of(a, b.consumer_category, units)  # the bill's own rule (increment 57)
		new = 0.0 if exempt else float(engine.rnd(engine.D(units) * engine.D(a.rate_per_unit)))
		old = sum(flt(l.amount) for l in frappe.get_all("Bill Line", filters={"parent": b.name, "parenttype": "Utility Bill", "charge_type": a.charge_type},
		                                                fields=["amount"]))
		rows.append({"connection": b.connection, "consumer_name": b.consumer_name, "category": b.consumer_category,
		             "old": flt(b.current_amount), "new": flt(b.current_amount) - old + new})
	return summarise(month, rows, unavailable, _("{0} {1}").format(a.adjustment_type, a.name))
