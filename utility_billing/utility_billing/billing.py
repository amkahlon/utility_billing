# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bills and bill runs (FS-BIL-01, 03-06, 09-10, 14-15; FS-RUN-01-05). Calculations are in
bill_engine.py; this module gathers the inputs from the database and stores the results."""

import json
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, now_datetime

from utility_billing.utility_billing import bill_engine as engine
from utility_billing.utility_billing import flags, holders, periods, plans, prorata, snapshots, split_engine, tax_engine
from utility_billing.utility_billing import account, bill_print
from utility_billing.utility_billing import readings, runcache
from utility_billing.utility_billing.doctype.tariff.tariff import get_applicable_tariff as _applicable_tariff


def get_applicable_tariff(society, utility, category, on_date):
	"""The tariff in force (tariff.get_applicable_tariff), looked up once per category and date during a run."""
	return runcache.tariff(("applicable", society, utility, category, str(on_date)),
	                       lambda: _applicable_tariff(society, utility, category, on_date))

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


def is_metered(utility) -> bool:
	return bool(frappe.utils.cint(frappe.get_cached_value("Utility Type", utility, "is_metered")))


def tariff_charges(tariff, plot_class=None) -> list:
	"""The tariff's own charges; a charge 'By Plot Size' becomes the monthly amount set for the property's plot size class
	(maintenance or sewerage by plot size; increment 22)."""
	out = []
	for c in tariff.charges:
		basis = frappe.get_cached_value("Charge Type", c.charge_type, "calculation_basis")
		if basis == engine.BY_PLOT_SIZE:
			rate = next((r.rate for r in tariff.get("plot_rates") or [] if r.charge_type == c.charge_type
			             and (r.plot_size_class or "").strip().lower() == (plot_class or "").strip().lower()), None)
			if rate is None:
				raise BillProblem(_("Tariff {0} has no {1} amount for plot size {2}.").format(
					tariff.name, frappe.get_cached_value("Charge Type", c.charge_type, "charge_name"), plot_class or _("(not set on the property)")))
			out.append(charge_of(c.charge_type, rate=rate, basis=engine.FIXED))
		else:
			out.append(charge_of(c.charge_type, rate=c.rate))
	return out


def contract_tariff(connection, month):
	"""The service agreement in force on the account in `month` that carries a contract tariff, or None (FS-MST-19, FS-CFG-32)."""
	if runcache.none_for("agreements", connection):
		return None
	start, end = readings.month_start(month), frappe.utils.get_last_day(month)
	rows = frappe.get_all("Service Agreement", filters={"connection": connection, "docstatus": 1, "status": "Active",
	                                                    "start_date": ["<=", end], "tariff": ["is", "set"]},
	                      or_filters=[["end_date", "is", "not set"], ["end_date", ">=", start]], fields=["name", "tariff"],
	                      order_by="start_date desc", limit=1)
	return rows[0] if rows else None


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


def tax_spec(t, month, category, consumer, utility=None):
	"""The TaxSpec of a Tax document for a bill, or None when it doesn't apply (another utility's tax, not yet or no longer in force,
	other category, exempt category). Whether the tax is switched on is the caller's business."""
	month = readings.month_start(month)
	if utility and utility not in {r.utility for r in t.get("utilities") or []}:
		return None
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


def taxes_for(society, month, category, consumer, extra=None, without=None, utility=None) -> list:
	"""The taxes in force for a bill: enabled, chosen for the bill's utility, effective in the billing month, for this category and
	consumer (FS-CFG-14). The utility defaults to the category's.
	`extra` adds a tax document that isn't switched on yet and `without` leaves one out (the impact preview uses both)."""
	if consumer and frappe.utils.cint(consumer.get("tax_exempt")):
		return []
	utility = utility or (category and frappe.db.get_value("Consumer Category", category, "utility"))
	specs = []
	for name in frappe.get_all("Tax", filters={"society": society, "enabled": 1}, pluck="name", order_by="calculation_order, name"):
		if name == without:
			continue
		spec = tax_spec(frappe.get_cached_doc("Tax", name), month, category, consumer, utility)
		if spec:
			specs.append(spec)
	if extra:
		spec = tax_spec(extra, month, category, consumer, utility)
		if spec:
			specs.append(spec)
	return specs


def tariff_segments(run, category, reading, units, fraction, fraction_note, plot_class=None):
	"""One segment per tariff in force during the reading period, when a tariff changed inside it (FS-CFG-05, FS-BIL-02); None when
	one tariff covers the whole period, the dates are missing or the tariffs leave a gap."""
	start, end = reading.get("previous_reading_date"), reading.get("reading_date")
	if not (start and end):
		return None
	start, end = getdate(start), getdate(end)
	rows = runcache.tariff(("periods", run.society, run.utility, category, start, end), lambda: frappe.get_all(
		"Tariff", filters={"docstatus": 1, "society": run.society, "utility": run.utility, "consumer_category": category,
		                   "effective_from": ["<=", end]},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", start]],
		fields=["name", "effective_from", "effective_to"]))
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
			"charges": tariff_charges(t, plot_class), "tariff_name": name,
			"note": _("{0} of {1} days at {2}").format(days, total, t.tariff_title) + (f"; {fraction_note}" if fraction_note else ""),
		})
	return segments


def category_segments(run, conn, reading, units, fraction, fraction_note, method, plot_class=None):
	"""Two parts of one bill when the category changed inside the reading period (increment 22; FS-BIL-02): each part at its category's
	tariff and charges, the units by the change-day reading when the society says so and one was taken, else by days. None otherwise."""
	start, end = reading.get("previous_reading_date"), reading.get("reading_date")
	changes = holders.changes_in(conn.name, start, end, holders.CATEGORY)
	if not changes:
		return None
	change = changes[-1]  # the latest change in the period decides the split
	parts = holders.split(units, start, end, change, method, reading.get("previous_reading"), reading.get("multiplier"))
	total = (getdate(end) - getdate(start)).days
	whole = engine.D(fraction) if fraction is not None else engine.D(1)
	segments = []
	for side, category in (("before", change.old_value), ("after", change.new_value)):
		share, days = parts[side]
		tariff_name = get_applicable_tariff(run.society, run.utility, category, readings.month_start(run.billing_month))
		if not tariff_name:
			raise BillProblem(_("No approved tariff for {0} in {1}.").format(category, month_label(run.billing_month)))
		t = frappe.get_cached_doc("Tariff", tariff_name)
		how = _("{0} units by the reading on {1}").format(f"{flt(share):g}", frappe.utils.formatdate(change.change_date)) if parts["by"] == "reading" \
			else _("{0} of {1} days").format(days, total)
		segments.append({
			"units": share, "fraction": whole * engine.D(days) / engine.D(total) if total else whole,
			"tariff": {"method": t.pricing_method, "flat_rate": t.flat_rate, "slabs": t.slabs, "minimum_charge": t.minimum_charge},
			"charges": tariff_charges(t, plot_class), "tariff_name": tariff_name,
			"note": _("{0}: {1}").format(frappe.db.get_value("Consumer Category", category, "category_name") or category, how)
			        + (f"; {fraction_note}" if fraction_note else ""),
		})
	return segments


def holder_parts(run, connection, reading, society):
	"""The two bills of a month in which the account's holder changed (user decision 2026-11-16): a final bill to the outgoing holder for
	their part, carrying the account's arrears, and the incoming holder's bill for theirs, with none. [] when there's no such change."""
	start, end = reading.get("previous_reading_date"), reading.get("reading_date")
	changes = holders.changes_in(connection, start, end, holders.HOLDER)
	if not changes:
		return []
	change = changes[-1]
	parts = holders.split(reading.units, start, end, change, society.tariff_split_method, reading.get("previous_reading"), reading.get("multiplier"))
	total = (getdate(end) - getdate(start)).days or 1
	day = frappe.utils.formatdate(change.change_date)
	how = lambda side: (_("{0} units by the reading on {1}").format(f"{flt(parts[side][0]):g}", day) if parts["by"] == "reading"  # noqa: E731
	                    else _("{0} of {1} days").format(parts[side][1], total))
	before = {"consumer": change.old_consumer, "final": True, "fraction": engine.D(parts["before"][1]) / engine.D(total),
	          "note": _("Final bill up to {0} ({1})").format(day, how("before"))}
	after = {"consumer": change.new_consumer, "no_adjustments": True, "fraction": engine.D(parts["after"][1]) / engine.D(total),
	         "note": _("From {0} ({1})").format(day, how("after"))}
	return [{"part": before, "units": parts["before"][0]},
	        {"part": after, "units": parts["after"][0], "arrears": 0, "installment": None}]


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
	reading_fields = ["name", "status", "units", "consumption_type", "previous_reading_date", "reading_date", "defective", "previous_reading",
	                  "multiplier"]
	cycle_rows = runcache.bulk(("readings", run.reading_cycle), lambda: {
		r.connection: r for r in frappe.get_all("Meter Reading", filters={"reading_cycle": run.reading_cycle}, fields=[*reading_fields, "connection"])})
	reading = cycle_rows.get(connection) if cycle_rows is not None else frappe.db.get_value(
		"Meter Reading", {"reading_cycle": run.reading_cycle, "connection": connection}, reading_fields, as_dict=True)
	# Fixed charges while disconnected (increment 22, gap 2): with the society's "Continue" policy a disconnected account is billed its
	# fixed and per-area charges every month, without a reading, energy or minimum charge.
	keep_fixed = conn.status == "Disconnected" and society.get("disconnected_fixed_charges") == "Continue"
	if not reading and keep_fixed:
		reading = frappe._dict(name=None, status="Valid", units=0, consumption_type=_("Disconnected"), defective=0)
	metered = is_metered(run.utility)
	if not metered:  # a service billed without a meter (maintenance, flat water, sewerage; increment 22): no reading, no energy
		reading = frappe._dict(name=None, status="Valid", units=0, consumption_type=_("Not metered"), defective=0)
	if not reading:
		raise BillProblem(_("No meter reading in cycle {0}.").format(run.reading_cycle))
	if reading.status not in readings.DONE:
		raise BillProblem(_("Meter reading {0} is {1}.").format(reading.name, reading.status))
	part = override.get("part") or {}  # one side of a holder change (increment 22): its consumer, share of the period, arrears

	category = override.get("category") or conn.consumer_category
	units = flt(override.get("units")) if override.get("units") is not None else reading.units
	agreement = None if override.get("tariff") else contract_tariff(connection, month)  # a service agreement's tariff (FS-CFG-32)
	tariff_name = override.get("tariff") or (agreement and agreement.tariff) or get_applicable_tariff(run.society, run.utility, category, month)
	if not tariff_name:
		raise BillProblem(_("No approved tariff for {0} in {1}.").format(category, month_label(month)))
	tariff = frappe.get_cached_doc("Tariff", tariff_name)

	energy = None
	if metered:
		if not society.energy_charge_type:
			raise BillProblem(_("Set the Energy Charge Type on society {0}.").format(society.name))
		energy = charge_of(society.energy_charge_type, basis=engine.PER_UNIT)
	props = runcache.bulk(("properties", run.society), lambda: {
		p.name: p for p in frappe.get_all("Property", filters={"society": run.society}, fields=["name", "plot_size_class", "area"])})
	prop = (props or {}).get(conn.property) or frappe.db.get_value("Property", conn.property, ["plot_size_class", "area"], as_dict=True) or {}
	plot_class = prop.get("plot_size_class")
	charges = tariff_charges(tariff, plot_class)

	adjustments = []
	for adj_type, used in (("FPA", society.charges_fpa), ("QTA", society.charges_qta)):
		if not used or not metered or part.get("no_adjustments") or keep_fixed and not reading.name:
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

	area = flt(prop.get("area"))
	consumer_id = part.get("consumer") or conn.consumer
	consumer = frappe.db.get_value("Consumer", consumer_id, ["tax_status", "tax_exempt"], as_dict=True) or {}
	plan_name = installment = installment_no = None
	if "installment" in override:  # a revised bill keeps the installment of the bill it replaces
		plan_name, installment, installment_no = override.get("installment_plan"), override["installment"], override.get("installment_no")
	else:
		found = plans.installment_for(connection, month)
		if found:
			plan_name, installment, installment_no = found[0], flt(found[1].amount), found[1].number
	fraction, fraction_note = None, ""
	disconnected_on = None if keep_fixed else conn.get("disconnected_on")  # "Continue": the disconnection doesn't cut the month short
	if frappe.utils.cint(society.prorate_part_months) and (conn.connection_date or disconnected_on):
		try:
			fraction, fraction_note = prorata.fraction(month, conn.connection_date, disconnected_on)
		except prorata.NotConnected as e:
			raise BillProblem(str(e)) from None
	if part.get("fraction") is not None:  # this holder's share of the period
		fraction = (engine.D(fraction) if fraction is not None else engine.D(1)) * engine.D(part["fraction"])
		fraction_note = "; ".join(x for x in (part.get("note"), fraction_note) if x)
	fixed_only = keep_fixed and not reading.name  # a disconnected account billed its fixed charges only
	segments = None
	splitting = society.tariff_split_method in ("By days", holders.BY_READING)
	if splitting and not override.get("tariff") and not override.get("category") and not part:
		segments = category_segments(run, conn, reading, units, fraction, fraction_note, society.tariff_split_method, plot_class)
		if segments:
			tariff = frappe.get_cached_doc("Tariff", segments[-1]["tariff_name"])
	if splitting and not segments and not override.get("tariff") and not part:
		segments = tariff_segments(run, category, reading, units, fraction, fraction_note, plot_class)
		if segments:
			tariff = frappe.get_cached_doc("Tariff", segments[-1]["tariff_name"])  # the bill carries the tariff in force now
	result = engine.calculate(
		units,
		{"method": tariff.pricing_method, "flat_rate": tariff.flat_rate, "slabs": tariff.slabs,
		 "minimum_charge": 0 if fixed_only or not metered else tariff.minimum_charge},
		energy, charges=charges, adjustments=adjustments, area=area,
		arrears=override["arrears"] if override.get("arrears") is not None else arrears_of(connection, month),
		surcharge={"basis": society.surcharge_basis, "rate": society.surcharge_rate,
		           "fixed_amount": society.surcharge_fixed_amount, "cap": society.surcharge_cap},
		bill_rounding=society.rounding_rule or engine.NEAREST,
		taxes=taxes_for(run.society, month, category, consumer, utility=run.utility), fraction=fraction, fraction_note=fraction_note,
		installment=engine.D(installment) if installment else None, segments=segments,
	)
	consumption_type = reading.consumption_type
	if frappe.utils.cint(reading.get("defective")):
		consumption_type = _("Average billed: meter defective")  # FS-WF-08
	bill = {
		"doctype": "Utility Bill", "connection": connection, "consumer": consumer_id,
		"consumer_name": frappe.db.get_value("Consumer", consumer_id, "consumer_name") if part.get("consumer") else conn.consumer_name,
		"property": conn.property, "consumer_category": category,
		"society": run.society, "utility": run.utility, "billing_month": month, "issue_date": run.issue_date,
		"due_date": run.due_date, "bill_run": run.name, "meter_reading": reading.name, "units": flt(units),
		"consumption_type": consumption_type, "tariff": tariff.name, "status": "Draft",
		"final_bill": 1 if part.get("final") else 0, "period_note": part.get("note"),
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
	if not is_metered(run.utility):
		return None  # a service billed without a meter has no reading cycle (increment 22)
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
	society, utility = frappe.db.get_value("Bill Run", run, ["society", "utility"])
	with runcache.for_run(society, utility):  # FS-RUN-07: lookups shared by every account, once per run
		_calculate_bills(run)


def _calculate_bills(run):
	doc = frappe.get_doc("Bill Run", run)
	try:
		for name in frappe.get_all("Utility Bill", filters={"bill_run": run, "docstatus": 0}, pluck="name"):
			frappe.delete_doc("Utility Bill", name, force=True, ignore_permissions=True)
		excluded = {e.connection for e in doc.exclusions}
		problems = []
		month = readings.month_start(doc.billing_month)
		society = frappe.get_cached_doc("Society", doc.society)
		statuses = ["Active", "Disconnected"] if society.get("disconnected_fixed_charges") == "Continue" else ["Active"]  # increment 22
		billed = dict(frappe.db.sql("""select connection, name from `tabUtility Bill` where society = %s and utility = %s and billing_month = %s
		                               and docstatus = 1""", (doc.society, doc.utility, month)))  # one query for the run (FS-RUN-07)
		for conn in frappe.get_all(
			"Connection", filters={"society": doc.society, "utility": doc.utility, "status": ["in", statuses]},
			pluck="name", order_by="name",
		):
			if conn in excluded:
				continue
			already = billed.get(conn)
			if already:
				if not frappe.utils.cint(doc.get("supplementary")):  # a supplementary run bills only the accounts still without a bill
					problems.append({"connection": conn, "problem": _("Already billed for this month in {0}.").format(already)})
				continue
			try:
				parts = []
				if not runcache.none_for("changed", conn):  # only an account with a recorded change can need two bills
					reading = frappe.db.get_value("Meter Reading", {"reading_cycle": doc.reading_cycle, "connection": conn},
					                              ["status", "units", "previous_reading_date", "reading_date", "previous_reading", "multiplier"],
					                              as_dict=True)
					parts = holder_parts(doc, conn, reading, society) if reading and reading.status in readings.DONE else []
				for override in parts or [None]:  # a holder change in the period: a final bill and the incoming holder's bill
					bill = frappe.get_doc(build_bill(doc, conn, override))
					bill.flags.from_bill_run = True
					bill.insert(ignore_permissions=True, ignore_links=True)  # its links come from the account, tariff and charge types
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
	with runcache.for_run(doc.society, doc.utility):  # FS-RUN-07
		_post_bills(doc)


def _post_bills(doc):
	periods.assert_open(doc.society, doc.issue_date, "Bill run")
	if not frappe.db.exists("Run Snapshot", {"bill_run": doc.name}):  # a posting carried on after a failure keeps the first snapshot
		snapshots.take(doc)  # kept before anything is posted, so the run can be restored (FS-RUN-13)
	# final bills first: what the account owes after them becomes the former holder's before the incoming holder's bill is posted
	batch = frappe.flags.post_batch  # background posting: commit every `batch` bills (FS-RUN-07: under the transaction write limit,
	# and payments to these accounts aren't held up for minutes); a failure leaves the rest as drafts for "Post Again"
	names = frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 0}, pluck="name", order_by="final_bill desc, name")
	for n, name in enumerate(names, start=1):
		if batch and n % batch == 0:
			frappe.db.commit()
		bill = frappe.get_doc("Utility Bill", name)
		# Payments may have arrived between calculation and approval: arrears are taken from the account
		# as it stands at posting, so the bill never asks for money already paid.
		account.lock(bill.connection)  # held to the commit, so no payment slips in between this read and the bill's entry
		last = account.last_entry(bill.connection)  # read once for the arrears and the posting (FS-RUN-07)
		arrears = flt(account.arrears_for_new_bill(bill.connection, account.balances_of(last)))
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
		bill.flags.ignore_links = True  # checked when the run calculated it; nothing it links to can have gone since
		bill.docstatus = 1  # submit: the run's approval and the ledger entry record the posting, so no version row per bill
		bill.save(ignore_version=True)
		account.post_bill(bill, last)
		plans.mark_billed(bill)
		if frappe.utils.cint(bill.final_bill):
			change = frappe.get_all("Connection Change", filters={"parent": bill.connection, "change_type": holders.HOLDER, "old_consumer": bill.consumer},
			                        fields=["change_date", "owner_liable"], order_by="change_date desc", limit=1)
			holders.after_final_bill(bill, change[0] if change else frappe._dict(change_date=bill.issue_date, owner_liable=0))
			runcache.active()["former"].add(bill.connection)  # the incoming holder's bill must see the balance just moved off
	from utility_billing.utility_billing import billaccounts  # noqa: PLC0415

	billaccounts.after_run_posted(doc)  # combined bills gather the services' new bills (increment 22)


def check_run_cancellable(doc):
	periods.assert_open(doc.society, doc.issue_date, "Bill run")  # a closed month's bills can't be undone (FS-ACC-10)
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 1}, pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		if frappe.utils.cint(bill.final_bill):
			fob = frappe.db.get_value("Former Occupant Balance", {"final_bill": name})
			if fob and holders.paid_by(fob):
				frappe.throw(_("Final bill {0} has payments against former occupant balance {1}; reverse them first.").format(name, fob))
			continue  # checked after the incoming holder's bill is undone (cancel_run_bills does that one first)
		account.check_bill_reversible(bill)


def cancel_run_bills(doc):
	# the incoming holder's bill before the final bill: entries are undone newest first
	for name in frappe.get_all("Utility Bill", filters={"bill_run": doc.name, "docstatus": 1}, pluck="name", order_by="final_bill asc, name"):
		bill = frappe.get_doc("Utility Bill", name)
		account.reverse_bill(bill)
		bill.flags.from_bill_run = True
		bill.flags.ignore_permissions = True
		bill.cancel()
		if frappe.utils.cint(bill.final_bill):
			for fob in frappe.get_all("Former Occupant Balance", filters={"final_bill": name}, pluck="name"):
				frappe.delete_doc("Former Occupant Balance", fob, force=True, ignore_permissions=True)
