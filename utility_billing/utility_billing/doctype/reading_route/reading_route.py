# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading Route: an ordered list of the meters a reader walks, one reader per route each cycle (FS-MST-09).

A connection is on one route per utility; the route's code is kept on the connection (Connection.route), which is what bill
delivery order and reports group by.

Increment 60 (walk order): the order of the rows is the walk, used for reading sheets and for printing and delivering bills. On
save the rows are numbered 1, 2, 3 ... in their order, so a stop's Order is its place on the route ("Route 07 · 123" on a bill).
The checks are made for all rows at once, since a route of a migrated society holds well over a thousand accounts."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing.bulk import in_chunks


class ReadingRoute(Document):
	def validate(self):
		self.route_code = (self.route_code or "").strip()
		self.set("stops", sorted(self.stops, key=lambda r: r.idx or 0))
		seen_conn, seen_meter = set(), set()
		for n, s in enumerate(self.stops, start=1):
			s.idx = s.sequence = n
			if s.stop_type == "Check Meter":
				if not s.meter:
					frappe.throw(_("Row {0}: choose the check meter.").format(n))
				if s.meter in seen_meter:
					frappe.throw(_("Row {0}: meter {1} is on the route twice.").format(n, s.meter))
				seen_meter.add(s.meter)
				s.connection = None
				continue
			s.stop_type = "Connection"
			if not s.connection:
				frappe.throw(_("Row {0}: choose the account.").format(n))
			if s.connection in seen_conn:
				frappe.throw(_("Row {0}: account {1} is on the route twice.").format(n, s.connection))
			seen_conn.add(s.connection)
			s.meter = None
		self.check_accounts([s for s in self.stops if s.connection])
		self.stops_count = len(self.stops)

	def check_accounts(self, rows):
		"""Every account is one of this society's accounts of this utility, and (on a route in use) on no other route in use."""
		if not rows:
			return
		known, elsewhere = {}, {}
		for chunk in in_chunks([s.connection for s in rows], 1000):
			for c in frappe.get_all("Connection", filters={"name": ["in", chunk]}, fields=["name", "society", "utility"]):
				known[c.name] = c
			if self.enabled:
				for conn, route in frappe.db.sql(
					"""select s.connection, r.name from `tabRoute Stop` s join `tabReading Route` r on r.name = s.parent
					where s.parenttype = 'Reading Route' and s.connection in %(c)s and r.utility = %(u)s and r.enabled = 1
					and r.name != %(me)s""", {"c": chunk, "u": self.utility, "me": self.name or ""}):
					elsewhere.setdefault(conn, route)
		for s in rows:
			c = known.get(s.connection)
			if not c or c.society != self.society or c.utility != self.utility:
				frappe.throw(_("Row {0}: {1} isn't a {2} account of {3}.").format(s.idx, s.connection, self.utility, self.society))
			if s.connection in elsewhere:
				frappe.throw(_("Row {0}: account {1} is already on route {2}.").format(s.idx, s.connection, elsewhere[s.connection]))

	def on_update(self):
		"""Keep each account's route code in step with the routes that are in use."""
		before = self.get_doc_before_save()
		previous = {s.connection for s in (before.stops if before else []) if s.connection}
		now = {s.connection for s in self.stops if s.connection} if self.enabled else set()
		for chunk in in_chunks(sorted(now), 1000):
			frappe.db.sql("""update `tabConnection` set route = %(code)s where name in %(c)s and ifnull(route, '') != %(code)s""",
			              {"code": self.route_code, "c": chunk})
		for chunk in in_chunks(sorted(previous - now), 1000):
			frappe.db.sql("""update `tabConnection` set route = '' where name in %(c)s and route = %(code)s""",
			              {"code": self.route_code, "c": chunk})


def move_account(conn, route_code):
	"""Put an account at the end of the enabled route with this code (or on no route), taking it off the others of its utility
	first (a route refuses an account that is on another route)."""
	from utility_billing.utility_billing import walk  # noqa: PLC0415

	walk.place(conn.society, conn.utility, [conn.name], route_code or None, "end", check=False)
