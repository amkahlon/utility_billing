# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading routes in a reading cycle (FS-MST-09): each route has a reader for the cycle, and the reading sheet follows the routes' order."""

import frappe
from frappe import _


def plan(cycle) -> dict:
	"""{connection: {route, seq, reader, rank}} for the routes assigned to readers in the cycle; rank is the order to read in."""
	info, rank = {}, 0
	for a in cycle.route_assignments:
		route = frappe.get_doc("Reading Route", a.route)
		if route.society != cycle.society or route.utility != cycle.utility:
			frappe.throw(_("Route {0} belongs to {1} {2}, not to this cycle.").format(route.name, route.society, route.utility))
		for s in sorted(route.stops, key=lambda r: r.sequence or 0):
			if s.connection and s.connection not in info:
				rank += 1
				info[s.connection] = {"route": route.route_code, "seq": s.sequence, "reader": a.reader, "rank": rank}
	return info


def apply(cycle) -> int:
	"""Put the route, order and reader on every reading of the cycle that belongs to an assigned route (and clear the rest)."""
	info = plan(cycle)
	n = 0
	for r in frappe.get_all("Meter Reading", filters={"reading_cycle": cycle.name}, fields=["name", "connection", "route", "route_sequence", "assigned_reader"]):
		want = info.get(r.connection) or {}
		values = {"route": want.get("route") or "", "route_sequence": want.get("seq") or 0, "assigned_reader": want.get("reader")}
		if (r.route or "", r.route_sequence or 0, r.assigned_reader) != (values["route"], values["route_sequence"], values["assigned_reader"]):
			frappe.db.set_value("Meter Reading", r.name, values, update_modified=False)
			n += 1
	return n
