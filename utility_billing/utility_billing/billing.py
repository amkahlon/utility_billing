# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bills and bill runs (FS-BIL-01, 03-06, 09-10, 14-15; FS-RUN-01-05). Calculations are in
bill_engine.py; this module gathers the inputs from the database and stores the results."""

import json
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, now_datetime

from utility_billing.utility_billing import bill_engine as engine
from utility_billing.utility_billing import flags, periods, plans, prorata, snapshots, split_engine, tax_engine
from utility_billing.utility_billing import account, bill_print
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
	"""Everything the account owes before this bill (arrears, unpaid current bill, surcharge, fees,
	less credit), from the customer account (increment 7)."""
	return account.arrears_for_new_bill(connection)


def tax_spec(t, month, category, consumer):
	"""The TaxSpec of a Tax document for a bill, or None when it doesn't apply (not yet or no longer in force, other category,
	exempt category). Whether the tax is switched on is the caller's business."""
	month = readings.month_start(month)
	if (t.effective_from and getdate(t.effective_from) > getdate(month)) or (t.effective_to and getdate(t.effective_to) < getdate(month)):
		return None
	if t.categories and category not in {c.consumer_category for c in t.categories}:
		return None
	if category in {c.consumer_category for c in t.exempt_categories}:
		return None
	rate = flt(t.rate)
	status = (consumer or {}).get("tax_status") or "Filer"
	for r in t.status_rates:
		if r.tax_status == status:
			rate = flt(r.rate)
	return tax_engine.TaxSpec(
		code=t.name, name=t.tax_name, rate=engine.D(rate), applies_to=t.applies_to, groups=tuple(r.charge_group for r in t.charge_rows if r.charge_group),
		charge_types=tuple(r.charge_type for r in t.charge_rows if r.charge_type), inclusive=bool(frappe.utils.cint(t.inclusive)),
		order=frappe.utils.cint(t.calculation_order), on_taxes=tuple(r.tax for r in t.on_taxes), rounding=t.rounding_rule or engine.NEAREST)


def taxes_for(society, month, category, consumer, extra=None, without=None) -> list:
	"""The taxes in force for a bill: enabled, effective in the billing month, for this category and consumer (FS-CFG-14).
	`extra` adds a tax document that isn't switched on yet and `without` leaves one out (the impact preview uses both)."""
	if consumer and frappe.utils.cint(consumer.get("tax_exempt")):
		return []
	specs = []
	for name in frappe.get_all("Tax", filters={"society": society, "enabled": 1}, pluck="name", order_by="calculation_order, name"):
		if name == without:
			continue
		spec = tax_spec(frappe.get_cached_doc("Tax", name), month, category, consumer)
		if spec:
			specs.append(spec)
	if extra:
		spec = tax_spec(extra, month, category, consumer)
		if spec:
			specs.append(spec)
	return specs


def tariff_segments(run, category, reading, units, fraction, fraction_note):
	"""One segment per tariff in force during the reading period, when a tariff changed inside it (FS-CFG-05, FS-BIL-02); None when
	one tariff covers the whole period, the dates are missing or the tariffs leave a gap."""
	start, end = reading.get("previous_reading_date"), reading.get("reading_date")
	if not (start and end):
		return None
	start, end = getdate(start), getdate(end)
	rows = frappe.get_all(
		"Tariff", filters={"docstatus": 1, "society": run.society, "utility": run.utility, "consumer_category": category,
		                   "effective_from": ["<=", end]},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", start]],
		fields=["name", "effective_from", "effective_to"])
	parts = split_engine.tariff_days(start, end, [(r.name, getdate(r.effective_from), getdate(r.effective_to) if r.effective_to else None) for r in rows])
	total = (end - start).days
	if len(parts) < 2 or sum(p[3] for p in parts) != total:
		return None
	shares = split_engine.split_units(units, [p[3] for p in parts])
	whole = engine.D(fraction) if fraction is not None else engine.D(1)
	segments = []
	for (name, lo, hi, days), share in zip(parts, shares, strict=True):
		t = frappe.get_cached_doc("Tariff", name)
		segments.append({
			"units": share, "fraction": whole * engine.D(days) / engine.D(total),
			"tariff": {"method": t.pricing_method, "flat_rate": t.flat_rate, "slabs": t.slabs, "minimum_charge": t.minimum_charge},
			"charges": [charge_of(c.charge_type, rate=c.rate) for c in t.charges], "tariff_name": name,
			"note": _("{0} of {1} days at {2}").format(days, total, t.tariff_title) + (f"; {fraction_note}" if fraction_note else ""),
		})
	return segments


def build_bill(run, connection, override=None) -> dict:
	"""Utility Bill fields for one connection in a run. Raises BillProblem with a plain reason.
	`override` (a bill revision) may set units, category and arrears."""
	override = override or {}
	month = readings.month_start(run.billing_month)
	conn = frappe.get_cached_doc("Connection", connection)
	society = frappe.get_cached_doc("Society", run.society)

	held = flags.effect(connection, flags.HOLD_BILLING, month, frappe.utils.get_last_day(month))
	if held:
		raise BillProblem(_("Bill on hold: flag {0}. Remove it to bill this account.").format(held))
	reading = frappe.db.get_value(
		"Meter Reading", {"reading_cycle": run.reading_cycle, "connection": connection},
		["name", "status", "units", "consumption_type", "previous_reading_date", "reading_date"], as_dict=True,
	)
	if not reading:
		raise BillProblem(_("No meter reading in cycle {0}.").format(run.reading_cycle))
	if reading.status not in readings.DONE:
		raise BillProblem(_("Meter reading {0} is {1}.").format(reading.name, reading.status))

	category = override.get("category") or conn.consumer_category
	units = flt(override.get("units")) if override.get("units") is not None else reading.units
	tariff_name = override.get("tariff") or get_applicable_tariff(run.society, run.utility, category, month)
	if not tariff_name:
		raise BillProblem(_("No approved tariff for {0} in {1}.").format(category, month_label(month)))
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
		if category in {e.consumer_category for e in adj.exempt_categories}:
			exempt = _("{0} category").format(frappe.db.get_value("Consumer Category", category, "category_name"))
		elif flt(adj.exempt_below_units) and ref_units is not None and ref_units < flt(adj.exempt_below_units):
			exempt = _("below {0} units").format(adj.exempt_below_units)
		adjustments.append(engine.Adjustment(
			charge_of(adj.charge_type), units=None if ref_units is None else engine.D(ref_units),
			rate=engine.D(adj.rate_per_unit), reference=month_label(adj.reference_month), exempt_reason=exempt,
		))

	area = flt(frappe.db.get_value("Property", conn.property, "area"))
	consumer = frappe.db.get_value("Consumer", conn.consumer, ["tax_status", "tax_exempt"], as_dict=True) or {}
	plan_name = installment = installment_no = None
	if "installment" in override:  # a revised bill keeps the installment of the bill it replaces
		plan_name, installment, installment_no = override.get("installment_plan"), override["installment"], override.get("installment_no")
	else:
		found = plans.installment_for(connection, month)
		if found:
			plan_name, installment, installment_no = found[0], flt(found[1].amount), found[1].number
	fraction, fraction_note = None, ""
	if frappe.utils.cint(society.prorate_part_months) and (conn.connection_date or conn.get("disconnected_on")):
		try:
			fraction, fraction_note = prorata.fraction(month, conn.connection_date, conn.get("disconnected_on"))
		except prorata.NotConnected as e:
			raise BillProblem(str(e)) from None
	segments = None
	if society.tariff_split_method == "By days" and not override.get("tariff"):
		segments = tariff_segments(run, category, reading, units, fraction, fraction_note)
		if segments:
			tariff = frappe.get_cached_doc("Tariff", segments[-1]["tariff_name"])  # the bill carries the tariff in force now
	result = engine.calculate(
		units,
		{"method": tariff.pricing_method, "flat_rate": tariff.flat_rate, "slabs": tariff.slabs,
		 "minimum_charge": tariff.minimum_charge},
		energy, charges=charges, adjustments=adjustments, area=area,
		arrears=override["arrears"] if override.get("arrears") is not None else arrears_of(connection, month), minimum_bill=society.minimum_bill,
		surcharge={"basis": society.surcharge_basis, "rate": society.surcharge_rate,
		           "fixed_amount": society.surcharge_fixed_amount, "cap": society.surcharge_cap},
		bill_rounding=society.rounding_rule or engine.NEAREST,
		taxes=taxes_for(run.society, month, category, consumer), fraction=fraction, fraction_note=fraction_note,
		installment=engine.D(installment) if installment else None, segments=segments,
	)
	bill = {
		"doctype": "Utility Bill", "connection": connection, "consumer": conn.consumer,
		"consumer_name": conn.consumer_name, "property": conn.property, "consumer_category": category,
		"society": run.society, "utility": run.utility, "billing_month": month, "issue_date": run.issue_date,
		"due_date": run.due_date, "bill_run": run.name, "meter_reading": reading.name, "units": flt(units),
		"consumption_type": reading.consumption_type, "tariff": tariff.name, "status": "Draft",
		"lines": [
			{"label": l.label, "charge_type": l.charge_type or None, "charge_group": l.group, "basis": l.basis,
			 "quantity": flt(l.quantity), "rate": flt(l.rate), "amount": flt(l.amount), "note": l.note,
			 "line_order": l.order, "tax": l.tax or None}
			for l in result.lines
		],
		"taxes": [{"tax": t.code, "tax_name": t.name, "base": flt(t.base), "rate": flt(t.rate), "amount": flt(t.amount), "inclusive": 1 if t.inclusive else 0}
		          for t in result.taxes],
		"tax_amount": flt(result.tax_added),
		"installment_due": flt(result.installment) if result.installment is not None else 0, "installment_no": installment_no or 0,
		"installment_plan": plan_name if result.installment is not None else None,
		"current_amount": flt(result.current), "arrears": flt(result.arrears),
		"payable_within_due": flt(result.payable_within_due), "surcharge_amount": flt(result.surcharge),
		"payable_after_due": flt(result.payable_after_due), "outstanding_amount": 0,
	}
	if override.get("revises"):
		bill["revises"], bill["revision_no"] = override["revises"], override.get("revision_no") or 2
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
	periods.assert_open(doc.society, doc.issue_date, "Bill run")
	snapshots.take(doc)  # kept before anything is posted, so the run can be restored (FS-RUN-13)
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 0}, pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		# Payments may have arrived between calculation and approval: arrears are taken from the account
		# as it stands at posting, so the bill never asks for money already paid.
		arrears = flt(account.arrears_for_new_bill(bill.connection))
		if arrears != flt(bill.arrears):
			bill.arrears = arrears
			bill.payable_within_due = flt(bill.current_amount) + (flt(bill.installment_due) if flt(bill.installment_due) else arrears)
			bill.payable_after_due = bill.payable_within_due + flt(bill.surcharge_amount)
		bill.status = "Posted"
		bill.outstanding_amount = bill.payable_within_due
		# Freeze what the bill prints as it is now, at posting (FS-RUN-09: reprints match the original).
		bill.print_snapshot = json.dumps(bill_print.build_snapshot(bill))
		bill.flags.from_bill_run = True
		bill.flags.ignore_permissions = True
		bill.submit()
		account.post_bill(bill)
		plans.mark_billed(bill)


def check_run_cancellable(doc):
	periods.assert_open(doc.society, doc.issue_date, "Bill run")  # a closed month's bills can't be undone (FS-ACC-10)
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 1}, pluck="name"):
		account.check_bill_reversible(frappe.get_doc("Utility Bill", name))


def cancel_run_bills(doc):
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 1}, pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		account.reverse_bill(bill)
		bill.flags.from_bill_run = True
		bill.flags.ignore_permissions = True
		bill.cancel()
