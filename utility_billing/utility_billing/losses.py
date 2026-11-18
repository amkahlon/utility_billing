# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Line losses, AT&C and DISCO reconciliation (FS-RPT-08, 30-34, 38).

Input units come from the DISCO Bill (the bulk meter) and its feeder and transformer input rows; billed units come from
the posted bills. Loss = input - billed. Alert levels (Normal, Watch, High loss) are the society's own settings.

AT&C = 1 - (billing efficiency x collection efficiency), where
- billing efficiency = units billed / units that entered the society, and
- collection efficiency = money collected in the month / amount payable within the due date on the bills issued that
  month, capped at 100% (collecting old arrears doesn't make up for losing this month's revenue).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_last_day

from utility_billing.utility_billing import mis

NORMAL, WATCH, HIGH, NO_INPUT, CHECK = "Normal", "Watch", "High loss", "No input data", "Check data"
TONE = {NORMAL: "green", WATCH: "amber", HIGH: "red", NO_INPUT: "slate", CHECK: "amber"}
DEFAULT_LEVELS = (8.0, 12.0)
KEYS = {"Feeder": "feeder", "Transformer": "transformer"}


# ---------------------------------------------------------------- pure rules (unit tested)
def loss_pct(input_kwh, billed_kwh):
	"""Loss as a percentage of the units that came in; None when nothing is known to have come in."""
	return round((flt(input_kwh) - flt(billed_kwh)) / flt(input_kwh) * 100, 1) if flt(input_kwh) > 0 else None


def status_of(input_kwh, billed_kwh, watch=DEFAULT_LEVELS[0], high=DEFAULT_LEVELS[1]) -> str:
	if flt(input_kwh) <= 0:
		return NO_INPUT
	if flt(billed_kwh) > flt(input_kwh):
		return CHECK  # more billed than came in: a wrong input figure or a missing feeder
	pct = loss_pct(input_kwh, billed_kwh)
	return HIGH if pct >= flt(high) else WATCH if pct >= flt(watch) else NORMAL


def at_c(input_kwh, billed_kwh, payable, collected) -> dict:
	"""Billing efficiency, collection efficiency and AT&C loss, in percent (None where they can't be worked out)."""
	be = flt(billed_kwh) / flt(input_kwh) if flt(input_kwh) > 0 else None
	ce = min(flt(collected) / flt(payable), 1.0) if flt(payable) > 0 else None
	atc = (1 - be * ce) * 100 if be is not None and ce is not None else None
	as_percent = lambda v: None if v is None else round(v * 100, 1)  # noqa: E731
	return {"billing_efficiency": as_percent(be), "collection_efficiency": as_percent(ce), "atc": None if atc is None else round(atc, 1)}


def loading_status(percent) -> str:
	return _("Overloaded") if flt(percent) > 100 else _("Heavy") if flt(percent) >= 80 else _("OK")


def transformer_loading(sanctioned_kw, kva_rating, power_factor=0.9):
	"""Sanctioned load of the connections on a transformer as a percentage of what it can carry (kVA x power factor)."""
	capacity = flt(kva_rating) * power_factor
	return round(flt(sanctioned_kw) / capacity * 100, 1) if capacity > 0 else None


# ---------------------------------------------------------------- data
def levels(society):
	watch, high = frappe.db.get_value("Society", society, ["loss_watch_percent", "loss_high_percent"]) or (0, 0)
	return (flt(watch) or DEFAULT_LEVELS[0], flt(high) or DEFAULT_LEVELS[1])


def disco_bill(society, month):
	return frappe.db.get_value("DISCO Bill", {"society": society, "billing_month": mis.month_start(month)},
	                           ["name", "units_kwh", "export_units_kwh", "energy_amount", "fpa_amount", "qta_amount", "other_amount", "total_amount",
	                            "bill_no"], as_dict=True)


# Net metering (increment 28): a solar bill's `units` are the units billed after the solar credit, not what the consumer took from the
# network. The energy balance is: energy in = DISCO import − the society's export to the DISCO + residents' solar exports (fed into the
# society's network); energy delivered = residents' metered imports (spec test case 2: 787,200 − 213,600 + 408,159 − 913,289 = 68,470 lost).
DELIVERED = "sum(case when ifnull(b.nm_method, '') != '' then b.import_units else b.units end)"
EXPORTED = "sum(case when ifnull(b.nm_method, '') != '' then b.export_units else 0 end)"


def billed_units(society, month):
	"""Units delivered to residents in the month's posted bills (imports, for solar accounts)."""
	return flt(frappe.db.sql(f"""select {DELIVERED} from `tabUtility Bill` b where b.society = %s and b.docstatus = 1 and b.billing_month = %s""",
	                         (society, mis.month_start(month)))[0][0])


def solar_exports(society, month):
	return flt(frappe.db.sql(f"""select {EXPORTED} from `tabUtility Bill` b where b.society = %s and b.docstatus = 1 and b.billing_month = %s""",
	                         (society, mis.month_start(month)))[0][0])


def society_loss(society, month) -> dict | None:
	"""Whole-society figures for a month (bulk meter on the DISCO bill against all billed units), or None if no DISCO bill is recorded."""
	bill = disco_bill(society, month)
	if not bill or flt(bill.units_kwh) <= 0:
		return None
	solar = solar_exports(society, month)
	input_kwh = flt(bill.units_kwh) - flt(bill.get("export_units_kwh")) + solar
	source = _("DISCO bill") if not solar and not flt(bill.get("export_units_kwh")) else _("DISCO bill and solar exports")
	billed = billed_units(society, month)
	watch, high = levels(society)
	return {"input_kwh": round(input_kwh, 2), "billed_kwh": billed, "loss_kwh": round(input_kwh - billed, 2), "loss_pct": loss_pct(input_kwh, billed),
	        "status": status_of(input_kwh, billed, watch, high), "source": source, "disco_bill": bill.name if bill else None,
	        "disco_import_kwh": flt(bill.units_kwh), "disco_export_kwh": flt(bill.get("export_units_kwh")), "solar_export_kwh": solar}


def network_losses(society, month, by="Feeder") -> list[dict]:
	"""Loss per feeder or transformer: input from the DISCO bill's input rows, billed from the bills of the connections on it."""
	key = KEYS[by]
	month = mis.month_start(month)
	watch, high = levels(society)
	inputs = {r.item: flt(r.kwh) for r in mis.sql(
		f"""select i.{key} item, sum(i.input_kwh) kwh from `tabDISCO Input Row` i join `tabDISCO Bill` d on d.name = i.parent
		where d.society = %(s)s and d.billing_month = %(m)s and ifnull(i.{key}, '') != '' group by i.{key}""", {"s": society, "m": month})}
	# a feeder or transformer the DISCO bill gives no input for: its check meter's units read on the route (FS-RDG-12, increment 23)
	for r in mis.sql(f"""select {key} item, sum(units) kwh from `tabCheck Meter Reading` where society = %(s)s and billing_month = %(m)s
	                     and status = 'Read' and ifnull({key}, '') != '' and (%(tx)s or ifnull(transformer, '') = '') group by {key}""",
	                 {"s": society, "m": month, "tx": 1 if by == "Transformer" else 0}):
		inputs.setdefault(r.item, flt(r.kwh))
	billed = {(r.item or ""): r for r in mis.sql(
		f"""select c.{key} item, count(*) connections, {DELIVERED} kwh, {EXPORTED} solar from `tabUtility Bill` b
		join `tabConnection` c on c.name = b.connection
		where b.society = %(s)s and b.docstatus = 1 and b.billing_month = %(m)s group by c.{key}""", {"s": society, "m": month})}
	table, name_field = ("Feeder", "feeder_name") if by == "Feeder" else ("Transformer", "transformer_code")
	rows = []
	for item in list(dict.fromkeys(list(inputs) + [k for k in billed if k])) + ([""] if "" in billed else []):
		b = billed.get(item)
		# solar fed in by the consumers on this feeder or transformer comes into it too
		inp, kwh = inputs.get(item, 0.0) + (flt(b.solar) if b and item in inputs else 0.0), flt(b.kwh) if b else 0.0
		label = frappe.db.get_value(table, item, name_field) if item else _("(no {0})").format(_(by).lower())
		status = status_of(inp, kwh, watch, high)
		rows.append({"item": item, "name": label or item, "connections": cint(b.connections) if b else 0, "input_kwh": inp, "billed_kwh": kwh,
		             "loss_kwh": round(inp - kwh, 2) if inp else None, "loss_pct": loss_pct(inp, kwh), "status": status, "tone": TONE[status]})
	return sorted(rows, key=lambda r: (r["loss_pct"] is None, -(r["loss_pct"] or 0), r["name"]))


def at_c_for(society, month, series_row) -> dict | None:
	"""AT&C for a month from its whole-society loss and the month's dashboard series row (payable, collected)."""
	loss = society_loss(society, month)
	if not loss:
		return None
	return {**loss, **at_c(loss["input_kwh"], loss["billed_kwh"], series_row["payable"], series_row["collected"])}


def disco_comparison(society, month) -> dict | None:
	"""The DISCO's bill against what the society billed its residents for electricity (FS-RPT-08, 34, 38).
	Resident billing here is every bill line of the Electricity charge group (energy, FPA, QTA, per-unit add-ons); fixed society
	charges such as service and aquifer aren't DISCO cost and are left out. FPA and QTA billed are the lines of the approved
	adjustments of that month."""
	month = mis.month_start(month)
	bill = disco_bill(society, month)
	units = billed_units(society, month)
	electricity = flt(frappe.db.sql(
		"""select sum(l.amount) from `tabBill Line` l join `tabUtility Bill` b on b.name = l.parent join `tabCharge Type` c on c.name = l.charge_type
		where b.society = %s and b.docstatus = 1 and b.billing_month = %s and c.charge_group = 'Electricity'""", (society, month))[0][0])

	def billed_as(kind):
		return flt(frappe.db.sql(
			"""select sum(l.amount) from `tabBill Line` l join `tabUtility Bill` b on b.name = l.parent
			where b.society = %s and b.docstatus = 1 and b.billing_month = %s and l.charge_type in
			(select charge_type from `tabTariff Adjustment` where society = %s and billing_month = %s and adjustment_type = %s and docstatus = 1)""",
			(society, month, society, month, kind))[0][0])

	fpa, qta = billed_as("FPA"), billed_as("QTA")
	row = {"disco_bill": bill.name if bill else None, "disco_units": flt(bill.units_kwh) if bill else None, "billed_units": units,
	       "disco_total": flt(bill.total_amount) if bill else None, "billed_amount": electricity,
	       "disco_fpa": flt(bill.fpa_amount) if bill else None, "billed_fpa": fpa,
	       "disco_qta": flt(bill.qta_amount) if bill else None, "billed_qta": qta}
	if bill:
		row["units_difference"] = round(flt(bill.units_kwh) - units, 2)
		row["margin"] = round(electricity - flt(bill.total_amount), 2)
		row["margin_pct"] = mis.pct(row["margin"], electricity)
		row["fpa_recovery"] = mis.pct(fpa, bill.fpa_amount) if flt(bill.fpa_amount) else None
		row["qta_recovery"] = mis.pct(qta, bill.qta_amount) if flt(bill.qta_amount) else None
	return row
