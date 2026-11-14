# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading Route: an ordered list of the meters a reader walks, one reader per route each cycle (FS-MST-09).

A connection is on one route per utility; the route's code is kept on the connection (Connection.route), which is what bill
delivery order and reports group by."""

import frappe
from frappe import _
from frappe.model.document import Document


class ReadingRoute(Document):
	def validate(self):
		seen_conn, seen_meter, seen_seq = set(), set(), set()
		for s in sorted(self.stops, key=lambda r: (r.sequence or 0)):
			if s.sequence in seen_seq:
				frappe.throw(_("Row {0}: order {1} is used twice.").format(s.idx, s.sequence))
			seen_seq.add(s.sequence)
			if s.stop_type == "Check Meter":
				if not s.meter:
					frappe.throw(_("Row {0}: choose the check meter.").format(s.idx))
				if s.meter in seen_meter:
					frappe.throw(_("Row {0}: meter {1} is on the route twice.").format(s.idx, s.meter))
				seen_meter.add(s.meter)
				s.connection = None
				continue
			if not s.connection:
				frappe.throw(_("Row {0}: choose the account.").format(s.idx))
			if s.connection in seen_conn:
				frappe.throw(_("Row {0}: account {1} is on the route twice.").format(s.idx, s.connection))
			seen_conn.add(s.connection)
			s.meter = None
			conn = frappe.db.get_value("Connection", s.connection, ["society", "utility"], as_dict=True)
			if not conn or conn.society != self.society or conn.utility != self.utility:
				frappe.throw(_("Row {0}: {1} isn't a {2} account of {3}.").format(s.idx, s.connection, self.utility, self.society))
			if self.enabled:
				other = frappe.db.sql(
					"""select r.name from `tabRoute Stop` s join `tabReading Route` r on r.name = s.parent
					where s.connection = %s and r.utility = %s and r.enabled = 1 and r.name != %s limit 1""",
					(s.connection, self.utility, self.name or ""))
				if other:
					frappe.throw(_("Row {0}: account {1} is already on route {2}.").format(s.idx, s.connection, other[0][0]))
		self.stops_count = len(self.stops)

	def on_update(self):
		"""Keep each account's route code in step with the routes that are in use."""
		before = self.get_doc_before_save()
		previous = {s.connection for s in (before.stops if before else []) if s.connection}
		now = {s.connection for s in self.stops if s.connection} if self.enabled else set()
		for conn in now:
			frappe.db.set_value("Connection", conn, "route", self.route_code, update_modified=False)
		for conn in previous - now:
			if frappe.db.get_value("Connection", conn, "route") == self.route_code:
				frappe.db.set_value("Connection", conn, "route", "", update_modified=False)
