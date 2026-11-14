# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bills and bill runs (FS-BIL-01, 03-06, 09-10, 14-15; FS-RUN-01-05). Calculations are in
bill_engine.py; this module gathers the inputs from the database and stores the results."""

import json

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, now_datetime

from utility_billing.utility_billing import bill_engine as engine
from utility_billing.utility_billing import bill_print
from utility_billing.utility_billing import readings
from utility_billing.utility_billing.doctype.tariff.tariff import get_applicable_tariff

BACKGROUND_THRESHOLD = 200  # runs with more connections calculate in a background job (FS-RUN-01)


class BillProblem(Exception):
	"""A connection that can't be billed; recorded on the run, not raised to the user."""


def month_label(d):
	return getdate(d).strftime("%B %Y")


def charge_of(charge_type, rate=0, basis=None):
	ct = frappe.get_cached_doc("Charge Type", charge_type)
	return engine.Charge(
		charge_type=ct.name, label=ct.bill_label or ct.charge_name, group=ct.charge_group or "Other",
		order=ct.bill_order or 10, rounding=ct.rounding_rule or engine.NEAREST,
		basis=basis or ct.calculation_basis, rate=engine.D(rate),
	)


def approved_adjustment(society, utility, month, adjustment_type):
	name = frappe.db.get_value(
		"Tariff Adjustment",
		{"society": society, "utility": utility, "billing_month": readings.month_start(month),
		 "adjustment_type": adjustment_type, "docstatus": 1},
		"name",
	)
	return frappe.get_cached_doc("Tariff Adjustment", name) if name else None


def arrears_of(connection, month):
	"""Outstanding of the connection's earlier posted bills (reduced by payments in increment 7)."""
	rows = frappe.get_all(
		"Utility Bill",
		filters={"connection": connection, "docstatus": 1, "billing_month": ["<", readings.month_start(month)]},
		pluck="outstanding_amount",
	)
	return sum(flt(x) for x in rows)


def build_bill(run, connection) -> dict:
	"""Utility Bill fields for one connection in a run. Raises BillProblem with a plain reason."""
	month = readings.month_start(run.billing_month)
	conn = frappe.get_cached_doc("Connection", connection)
	society = frappe.get_cached_doc("Society", run.society)

	reading = frappe.db.get_value(
		"Meter Reading", {"reading_cycle": run.reading_cycle, "connection": connection},
		["name", "status", "units", "consumption_type"], as_dict=True,
	)
	if not reading:
		raise BillProblem(_("No meter reading in cycle {0}.").format(run.reading_cycle))
	if reading.status not in readings.DONE:
		raise BillProblem(_("Meter reading {0} is {1}.").format(reading.name, reading.status))

	tariff_name = get_applicable_tariff(run.society, run.utility, conn.consumer_category, month)
	if not tariff_name:
		raise BillProblem(_("No approved tariff for {0} in {1}.").format(conn.consumer_category, month_label(month)))
	tariff = frappe.get_cached_doc("Tariff", tariff_name)

	if not society.energy_charge_type:
		raise BillProblem(_("Set the Energy Charge Type on society {0}.").format(society.name))
	energy = charge_of(society.energy_charge_type, basis=engine.PER_UNIT)
	charges = [charge_of(c.charge_type, rate=c.rate) for c in tariff.charges]

	adjustments = []
	for adj_type, used in (("FPA", society.charges_fpa), ("QTA", society.charges_qta)):
		if not used:
			continue
		adj = approved_adjustment(run.society, run.utility, month, adj_type)
		if not adj:
			raise BillProblem(_("No approved {0} for {1}.").format(adj_type, month_label(month)))
		ref_units = readings.get_billable_units(connection, adj.reference_month)
		exempt = None
		if conn.consumer_category in {e.consumer_category for e in adj.exempt_categories}:
			exempt = _("{0} category").format(frappe.db.get_value("Consumer Category", conn.consumer_category, "category_name"))
		elif flt(adj.exempt_below_units) and ref_units is not None and ref_units < flt(adj.exempt_below_units):
			exempt = _("below {0} units").format(adj.exempt_below_units)
		adjustments.append(engine.Adjustment(
			charge_of(adj.charge_type), units=None if ref_units is None else engine.D(ref_units),
			rate=engine.D(adj.rate_per_unit), reference=month_label(adj.reference_month), exempt_reason=exempt,
		))

	area = flt(frappe.db.get_value("Property", conn.property, "area"))
	result = engine.calculate(
		reading.units,
		{"method": tariff.pricing_method, "flat_rate": tariff.flat_rate, "slabs": tariff.slabs,
		 "minimum_charge": tariff.minimum_charge},
		energy, charges=charges, adjustments=adjustments, area=area,
		arrears=arrears_of(connection, month), minimum_bill=society.minimum_bill,
		surcharge={"basis": society.surcharge_basis, "rate": society.surcharge_rate,
		           "fixed_amount": society.surcharge_fixed_amount, "cap": society.surcharge_cap},
		bill_rounding=society.rounding_rule or engine.NEAREST,
	)
	bill = {
		"doctype": "Utility Bill", "connection": connection, "consumer": conn.consumer,
		"consumer_name": conn.consumer_name, "property": conn.property, "consumer_category": conn.consumer_category,
		"society": run.society, "utility": run.utility, "billing_month": month, "issue_date": run.issue_date,
		"due_date": run.due_date, "bill_run": run.name, "meter_reading": reading.name, "units": flt(reading.units),
		"consumption_type": reading.consumption_type, "tariff": tariff.name, "status": "Draft",
		"lines": [
			{"label": l.label, "charge_type": l.charge_type or None, "charge_group": l.group, "basis": l.basis,
			 "quantity": flt(l.quantity), "rate": flt(l.rate), "amount": flt(l.amount), "note": l.note,
			 "line_order": l.order}
			for l in result.lines
		],
		"current_amount": flt(result.current), "arrears": flt(result.arrears),
		"payable_within_due": flt(result.payable_within_due), "surcharge_amount": flt(result.surcharge),
		"payable_after_due": flt(result.payable_after_due), "outstanding_amount": 0,
	}
	bill["print_snapshot"] = json.dumps(bill_print.build_snapshot(bill))
	return bill


# ----- bill run -----
def check_run_prerequisites(run):
	cycle = frappe.db.get_value(
		"Reading Cycle", {"society": run.society, "utility": run.utility, "billing_month": readings.month_start(run.billing_month)},
		["name", "status"], as_dict=True,
	)
	if not cycle:
		frappe.throw(_("There's no reading cycle for {0}.").format(month_label(run.billing_month)))
	if cycle.status != "Closed":
		frappe.throw(_("Close reading cycle {0} before calculating bills.").format(cycle.name))
	return cycle.name


@frappe.whitelist()
def calculate_run(run: str):
	doc = frappe.get_doc("Bill Run", run)
	doc.check_permission("write")
	if doc.docstatus != 0 or doc.workflow_state not in ("Draft", "Rejected", None, ""):
		frappe.throw(_("Only a draft or rejected bill run can be calculated."))
	doc.reading_cycle = check_run_prerequisites(doc)
	count = frappe.db.count("Connection", {"society": doc.society, "utility": doc.utility, "status": "Active"})
	doc.db_set({"reading_cycle": doc.reading_cycle, "calculation_status": "Calculating"})
	if count > BACKGROUND_THRESHOLD:
		frappe.enqueue(_calculate, queue="long", timeout=3600, run=run, enqueue_after_commit=True)
		return {"background": True}
	_calculate(run)
	return {"background": False}


def _calculate(run):
	doc = frappe.get_doc("Bill Run", run)
	try:
		for name in frappe.get_all("Utility Bill", filters={"bill_run": run, "docstatus": 0}, pluck="name"):
			frappe.delete_doc("Utility Bill", name, force=True, ignore_permissions=True)
		excluded = {e.connection for e in doc.exclusions}
		problems = []
		month = readings.month_start(doc.billing_month)
		for conn in frappe.get_all(
			"Connection", filters={"society": doc.society, "utility": doc.utility, "status": "Active"},
			pluck="name", order_by="name",
		):
			if conn in excluded:
				continue
			already = frappe.db.get_value(
				"Utility Bill", {"connection": conn, "billing_month": month, "docstatus": 1}, "name"
			)
			if already:
				problems.append({"connection": conn, "problem": _("Already billed for this month in {0}.").format(already)})
				continue
			try:
				bill = frappe.get_doc(build_bill(doc, conn))
				bill.flags.from_bill_run = True
				bill.insert(ignore_permissions=True)
			except BillProblem as e:
				problems.append({"connection": conn, "problem": str(e)})
		doc.set("problems", problems)
		set_summary(doc)
		doc.calculation_status = "Calculated"
		doc.calculated_on = now_datetime()
		doc.flags.from_calculation = True
		doc.save(ignore_permissions=True)
	except Exception:
		frappe.db.rollback()
		frappe.db.set_value("Bill Run", run, "calculation_status", "Failed")
		frappe.db.commit()
		raise


def set_summary(doc):
	"""Run summary by charge and by category, with the previous month (FS-RUN-05)."""
	bills = frappe.get_all(
		"Utility Bill", filters={"bill_run": doc.name, "docstatus": 0},
		fields=["name", "consumer_category", "units", "current_amount", "arrears", "payable_within_due"],
	)
	doc.bill_count = len(bills)
	doc.total_units = sum(flt(b.units) for b in bills)
	doc.total_current = sum(flt(b.current_amount) for b in bills)
	doc.total_arrears = sum(flt(b.arrears) for b in bills)
	doc.total_payable = sum(flt(b.payable_within_due) for b in bills)
	prev_month = add_months(readings.month_start(doc.billing_month), -1)
	prev = frappe.get_all(
		"Utility Bill", filters={"society": doc.society, "utility": doc.utility, "billing_month": prev_month, "docstatus": 1},
		fields=["name", "consumer_category", "current_amount"],
	)
	doc.previous_month_current = sum(flt(b.current_amount) for b in prev)

	rows = []
	names = [b.name for b in bills]
	prev_names = [b.name for b in prev]

	def line_totals(parents):
		out = {}
		if not parents:
			return out
		for l in frappe.get_all("Bill Line", filters={"parent": ["in", parents], "parenttype": "Utility Bill"},
		                        fields=["label", "amount", "parent"]):
			t = out.setdefault(l.label, {"amount": 0.0, "bills": set()})
			t["amount"] += flt(l.amount)
			t["bills"].add(l.parent)
		return out

	now, before = line_totals(names), line_totals(prev_names)
	for label, t in now.items():
		rows.append({"dimension": _("Charge"), "item": label, "bills": len(t["bills"]), "amount": t["amount"],
		             "previous_amount": before.get(label, {}).get("amount", 0)})
	cats = {}
	for b in bills:
		c = cats.setdefault(b.consumer_category, {"bills": 0, "units": 0.0, "amount": 0.0})
		c["bills"] += 1
		c["units"] += flt(b.units)
		c["amount"] += flt(b.current_amount)
	prev_cats = {}
	for b in prev:
		prev_cats[b.consumer_category] = prev_cats.get(b.consumer_category, 0) + flt(b.current_amount)
	for cat, c in sorted(cats.items()):
		rows.append({"dimension": _("Category"), "item": cat, "bills": c["bills"], "units": c["units"],
		             "amount": c["amount"], "previous_amount": prev_cats.get(cat, 0)})
	doc.set("summary", rows)


def post_run(doc):
	"""On approval: post every draft bill of the run (FS-BIL-10: posted bills never change)."""
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 0}, pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		bill.status = "Posted"
		bill.outstanding_amount = bill.payable_within_due
		# Freeze what the bill prints as it is now, at posting (FS-RUN-09: reprints match the original).
		bill.print_snapshot = json.dumps(bill_print.build_snapshot(bill))
		bill.flags.from_bill_run = True
		bill.flags.ignore_permissions = True
		bill.submit()


def cancel_run_bills(doc):
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 1}, pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		bill.flags.from_bill_run = True
		bill.flags.ignore_permissions = True
		bill.cancel()
