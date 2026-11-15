# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading review page (ub-review), designed after the mock-up "Reading review" (2026-11-16): the cycle's flagged readings as one queue,
filtered by route and exception, and the chosen reading beside it with its photo, the reader, when it was taken, what the checks found and
what approving it will do. The decisions are the existing ones (readings.approve_reading, correct_reading, request_reread), so their rules
and the review log stay as they are (FS-RDG-06 to 09)."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from utility_billing.utility_billing import mis

WAITING = ("Flagged", "Re-read Requested")


def require():
	if not (set(frappe.get_roles()) & set(mis.READINGS + mis.EXEC)) and frappe.session.user != "Administrator":
		raise frappe.PermissionError(_("You can't review readings."))


def consequence(r, treatment) -> str:
	"""What approving the reading as it stands will do, in a sentence."""
	if treatment == "Defective meter":
		return _("If approved: the meter is marked defective, a meter change request is raised, and this month is billed by the society's "
		         "defective-meter method, marked on the bill.")
	if treatment == "Estimate" or not r.present_reading:
		return _("If approved: this month is estimated from the account's history, marked on the bill, and trued up at the next actual reading.")
	return _("If approved: {0} units are billed as read.").format(f"{flt(r.units):,.0f}")


@frappe.whitelist()
def queue(cycle: str | None = None, society: str | None = None, route: str | None = None, code: str | None = None):
	require()
	if not cycle:
		filters = {"society": society} if society else {}
		cycle = frappe.db.get_value("Reading Cycle", {**filters, "status": ["!=", "Closed"]}, "name", order_by="billing_month desc") or \
			frappe.db.get_value("Reading Cycle", filters, "name", order_by="billing_month desc")
	if not cycle:
		return {"cycle": None, "cycles": []}
	c = frappe.get_doc("Reading Cycle", cycle)
	cond = {"reading_cycle": cycle, "status": ["in", WAITING]}
	if route:
		cond["route"] = route
	if code:
		cond["exception_code"] = code
	rows = frappe.get_all("Meter Reading", filters=cond, order_by="route, route_sequence, name", limit=300,
	                      fields=["name", "connection", "consumer_name", "property", "meter", "route", "status", "exception_code", "checks",
	                              "previous_reading", "present_reading", "units", "expected_low", "expected_high", "expected_average",
	                              "reading_date", "photo", "assigned_reader", "source", "multiplier", "review_note"])
	houses = {p.name: p.property_no for p in frappe.get_all("Property", filters={"name": ["in", [r.property for r in rows] or [""]]},
	                                                     fields=["name", "property_no"])}
	treatments = {x.name: x.billing_treatment for x in frappe.get_all("Reading Exception Code", fields=["name", "billing_treatment"])}
	readers = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list({r.assigned_reader for r in rows if r.assigned_reader}) or [""]]},
	                                                     fields=["name", "full_name"])}
	for r in rows:
		r["house"] = houses.get(r.property) or r.property
		r["flag"] = r.exception_code or (r.checks or "").split("\n")[0].split(";")[0][:60] or _(r.status)
		r["reader"] = readers.get(r.assigned_reader) or r.assigned_reader
		r["consequence"] = consequence(r, treatments.get(r.exception_code))
	counts = frappe.db.sql("""select count(*) total, sum(status in ('Valid', 'Approved', 'Estimated')) done,
	                          sum(status in ('Flagged', 'Re-read Requested')) waiting from `tabMeter Reading` where reading_cycle = %s""", cycle, as_dict=True)[0]
	return {
		"cycle": cycle, "society": c.society, "utility": c.utility, "status": c.status,
		"month_label": frappe.utils.getdate(c.billing_month).strftime("%B"),
		"cycles": frappe.get_all("Reading Cycle", filters={"society": c.society}, fields=["name", "billing_month", "utility", "status"],
		                         order_by="billing_month desc", limit=12),
		"counts": {"total": cint(counts.total), "done": cint(counts.done), "waiting": cint(counts.waiting)},
		"routes": sorted({r for r in frappe.get_all("Meter Reading", filters={"reading_cycle": cycle, "route": ["is", "set"]}, pluck="route", distinct=True)}),
		"codes": frappe.get_all("Reading Exception Code", filters={"enabled": 1}, pluck="name", order_by="name"),
		"rows": rows, "route": route or "", "code": code or "",
		"can_decide": bool(set(frappe.get_roles()) & {"Billing Officer", "Billing Manager", "System Manager"}),
	}
