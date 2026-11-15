# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Defective meters (FS-WF-08, SC-29).

A reading with an exception code whose treatment is "Defective meter" is billed, at the estimate step, by the society's
Defective-Meter Billing method instead of the ordinary estimate, and marked "Average billed: meter defective". The first such
reading marks the meter Defective and raises a Meter change service request (unless one is open). After the society's maximum
months averaging stops: zero units, so the bill carries only fixed and minimum charges, and the meter stays on the Defective Meters
report until it is replaced. When it is replaced, the next actual reading trues up the averaged months from the old meter's final
reading (readings.reading_context); if the old meter can't be read, the averaged months stand."""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from utility_billing.utility_billing import reading_engine as engine
from utility_billing.utility_billing import readings

AVERAGE, SAME_MONTH, LOAD, MINIMUM = "Average of last N months", "Same month last year", "Load × load factor × hours", "Minimum charge only"
TREATMENT = "Defective meter"


def is_defective_code(code) -> bool:
	return bool(code) and frappe.db.get_value("Reading Exception Code", code, "billing_treatment") == TREATMENT


def months_billed(meter, before_month) -> int:
	"""Months this meter has already been billed as defective before `before_month` (its current defective spell)."""
	since = frappe.db.get_value("Meter", meter, "defective_since")
	filters = {"meter": meter, "defective": 1, "billing_month": ["<", readings.month_start(before_month)]}
	if since:
		filters["billing_month"] = ["between", [readings.month_start(since), add_months(readings.month_start(before_month), -1)]]
	return frappe.db.count("Meter Reading", filters)


def units_for(reading) -> tuple[float, str]:
	"""(units, note) for a reading of a defective meter."""
	s = frappe.get_cached_doc("Society", reading.society)
	method = s.get("defective_billing_method") or AVERAGE
	limit = cint(s.get("defective_max_months")) or 3
	done = months_billed(reading.meter, reading.billing_month)
	if done >= limit:
		return 0, _("Meter defective for more than {0} months: averaging stopped, fixed charges only until the meter is replaced.").format(limit)
	n = cint(s.get("defective_average_months")) or 3
	history = readings.monthly_history(reading.connection, reading.billing_month, n)
	if method == MINIMUM:
		units, how = 0, _("minimum charge only")
	elif method == LOAD:
		conn = frappe.db.get_value("Connection", reading.connection, ["sanctioned_load_kw", "last_mdi_kw"], as_dict=True)
		load = flt(conn.sanctioned_load_kw) or flt(conn.last_mdi_kw)
		units = round(load * flt(s.get("defective_load_factor") or 30) / 100 * (cint(s.get("defective_hours")) or 720), 2)
		how = _("{0} kW × {1}% × {2} hours").format(f"{load:g}", f"{flt(s.get('defective_load_factor') or 30):g}", cint(s.get("defective_hours")) or 720)
	else:
		last_year = readings.same_month_last_year(reading.connection, reading.billing_month) if method == SAME_MONTH else None
		if last_year is not None:
			units, how = flt(last_year), _("same month last year")
		elif history:
			units, how = flt(engine.q(sum(engine.D(u) for u in history) / len(history))), _("average of {0} months").format(len(history))
		else:
			units, how = 0, _("no history, billed zero")
	return units, _("Average billed: meter defective ({0}: {1} units). Month {2} of at most {3}.").format(how, f"{units:g}", done + 1, limit)


def mark_meter(reading):
	"""The meter is Defective from this reading's month, with a Meter change request raised (once)."""
	m = frappe.db.get_value("Meter", reading.meter, ["status", "defective_since", "meter_change_request"], as_dict=True)
	values = {}
	if m.status != "Defective":
		values.update(status="Defective", defective_since=readings.month_start(reading.billing_month))
	open_request = m.meter_change_request and frappe.db.get_value("Service Request", m.meter_change_request, "status") not in (
		"Completed", "Rejected", "Cancelled")
	if not open_request:
		values["meter_change_request"] = raise_meter_change(reading)
	if values:
		frappe.db.set_value("Meter", reading.meter, values)


def raise_meter_change(reading):
	from utility_billing.utility_billing import servicereq  # noqa: PLC0415

	servicereq.ensure_types(reading.society)
	rtype = frappe.db.get_value("Service Request Type", {"society": reading.society, "kind": servicereq.METER, "enabled": 1})
	if not rtype:
		return None
	sr = frappe.get_doc({"doctype": "Service Request", "request_type": rtype, "connection": reading.connection, "requested_on": nowdate(),
	                     "meter_reason": _("Meter {0} defective ({1}), reading {2}").format(reading.meter, reading.exception_code, reading.name)})
	sr.flags.ignore_permissions = True
	sr.insert()
	return sr.name


def still_defective(society) -> list[dict]:
	"""Meters of the society marked Defective and still installed: for the exception report."""
	s = frappe.get_cached_doc("Society", society)
	limit = cint(s.get("defective_max_months")) or 3
	out = []
	for m in frappe.get_all("Meter", filters={"society": society, "status": "Defective", "current_connection": ["is", "set"]},
	                        fields=["name", "current_connection", "defective_since", "meter_change_request"], order_by="defective_since"):
		billed = frappe.db.count("Meter Reading", {"meter": m.name, "defective": 1,
		                                           "billing_month": [">=", readings.month_start(m.defective_since or nowdate())]})
		req = frappe.db.get_value("Service Request", m.meter_change_request, ["status", "current_step"], as_dict=True) if m.meter_change_request else None
		out.append({"meter": m.name, "connection": m.current_connection, "defective_since": m.defective_since, "months_averaged": billed,
		            "limit": limit, "over_limit": billed >= limit, "request": m.meter_change_request, "request_status": req.status if req else None,
		            "days": (getdate(nowdate()) - getdate(m.defective_since)).days if m.defective_since else None})
	return out
