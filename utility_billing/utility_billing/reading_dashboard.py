# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading dashboard (FS-RDG-13, increment 23): a reading cycle's progress while the readers are out: read, estimated, waiting and
flagged readings, by route and by reader, the readings waiting for review and the route's check meters. The page refreshes itself."""

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import getdate

from utility_billing.utility_billing import access, dashboard, mis
from utility_billing.utility_billing import rights

READ, ESTIMATED, WAITING, FLAGGED = ("Valid", "Approved"), ("Estimated",), ("Pending", "Re-read Requested"), ("Flagged",)


def require():
	if not rights.can("readings.view"):
		frappe.throw(_("You don't have permission to see the reading dashboard."), frappe.PermissionError)


def counts(rows) -> dict:
	n = len(rows)
	read = sum(1 for r in rows if r.status in READ)
	est = sum(1 for r in rows if r.status in ESTIMATED)
	flagged = sum(1 for r in rows if r.status in FLAGGED)
	waiting = sum(1 for r in rows if r.status in WAITING)
	return {"total": n, "read": read, "estimated": est, "flagged": flagged, "waiting": waiting,
	        "percent": round((read + est) * 100 / n, 1) if n else None}


def grouped(rows, key, label_of=lambda k: k) -> list[dict]:
	groups = OrderedDict()
	for r in sorted(rows, key=lambda r: (r.get(key) is None, r.get(key) or "")):
		groups.setdefault(r.get(key) or "", []).append(r)
	return [{"key": k, "label": label_of(k) if k else _("Not on a route") if key == "route" else _("No reader"), **counts(v)}
	        for k, v in groups.items()]


@frappe.whitelist()
def get_reading_dashboard(society: str | None = None, cycle: str | None = None):
	"""Everything the reading dashboard shows, in one call."""
	require()
	societies = access.visible_societies()
	if not societies:
		return {"societies": [], "empty": True}
	access.check_society(society)
	society = society or dashboard.default_society(societies)
	cycles = frappe.get_all("Reading Cycle", filters={"society": society}, fields=["name", "utility", "billing_month", "status"],
	                        order_by="billing_month desc, utility", limit=24)
	if cycle and not any(c.name == cycle for c in cycles):
		cycle = None
	if not cycle and cycles:
		cycle = next((c.name for c in cycles if c.status != "Closed"), cycles[0].name)
	base = {"society": society, "societies": societies, "cycles": [{"name": c.name, "label": f"{c.utility} {getdate(c.billing_month):%B %Y}"
	                                                                  + (f" ({_('closed')})" if c.status == "Closed" else "")} for c in cycles],
	        "cycle": cycle, "data_label": dashboard.data_label(society)}
	if not cycle:
		return {**base, "no_cycle": True}
	c = frappe.db.get_value("Reading Cycle", cycle, ["utility", "billing_month", "status", "reading_from", "reading_to"], as_dict=True)
	rows = frappe.get_all("Meter Reading", filters={"reading_cycle": cycle},
	                      fields=["name", "connection", "consumer_name", "status", "route", "route_sequence", "assigned_reader", "units",
	                              "exception_code", "reading_date", "expected_average"])
	readers = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list({r.assigned_reader for r in rows if r.assigned_reader}) or [""]]},
	                                                     fields=["name", "full_name"])}
	review = [r for r in rows if r.status in FLAGGED]
	checks = frappe.get_all("Check Meter Reading", filters={"reading_cycle": cycle},
	                        fields=["name", "meter", "measures", "route", "status", "units", "reading_date"], order_by="route, route_sequence")
	today = frappe.utils.getdate()
	days_left = (getdate(c.reading_to) - today).days if c.reading_to else None
	return {
		**base, "utility": c.utility, "month_label": getdate(c.billing_month).strftime("%B %Y"), "status": c.status,
		"window": {"from": str(c.reading_from) if c.reading_from else None, "to": str(c.reading_to) if c.reading_to else None, "days_left": days_left},
		"cards": counts(rows),
		"by_route": grouped(rows, "route"),
		"by_reader": grouped(rows, "assigned_reader", lambda k: readers.get(k) or k),
		"review": sorted(({"name": r.name, "connection": r.connection, "consumer_name": r.consumer_name, "route": r.route, "units": r.units,
		                   "expected": r.expected_average, "exception_code": r.exception_code} for r in review), key=lambda x: x["route"] or "")[:100],
		"review_total": len(review),
		"check_meters": checks, "check_read": sum(1 for x in checks if x.status == "Read"),
		"refreshed": frappe.utils.now_datetime().strftime("%H:%M"),
	}
