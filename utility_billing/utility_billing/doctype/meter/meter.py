# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter: a physical meter, in store or installed on one connection (FS-MST-06).
Its status and current connection are kept in step by Connection (meter history)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class Meter(Document):
	def validate(self):
		self.serial_no = (self.serial_no or "").strip()
		if cint(self.digits) < 1 or cint(self.digits) > 12:
			frappe.throw(_("Dial Digits must be between 1 and 12."))
		if flt(self.multiplier) <= 0:
			frappe.throw(_("Multiplier must be greater than 0."))
		if cint(self.registers) < 1:
			frappe.throw(_("A meter has at least 1 register."))
		if self.status == "Installed" and not self.current_connection:
			frappe.throw(_("A meter becomes Installed by adding it to a connection's meter history."))
		if self.status in ("In Store", "Removed") and self.current_connection:
			frappe.throw(
				_("This meter is still on connection {0}. Remove it there first.").format(self.current_connection)
			)

	def on_update(self):
		"""A meter marked (or unmarked) as a smart meter: its readings in open cycles know at once, so the Reading Entry screen
		and the form lock or unlock them without waiting for the reading's next save (increment 47)."""
		before = self.get_doc_before_save()
		if before and cint(before.get("digits")) != cint(self.digits):
			# the dial's digits corrected: its readings in open cycles are judged by the new ones from their next check (increment 50)
			frappe.db.sql("""update `tabMeter Reading` r join `tabReading Cycle` c on c.name = r.reading_cycle
			                 set r.digits = %s where r.meter = %s and c.status != 'Closed'""", (cint(self.digits), self.name))
		if before and cint(before.get("smart_meter")) != cint(self.smart_meter):
			frappe.db.sql("""update `tabMeter Reading` r join `tabReading Cycle` c on c.name = r.reading_cycle
			                 set r.smart_meter = %s, r.smart_meter_mute = if(%s, r.smart_meter_mute, 0)
			                 where r.meter = %s and c.status != 'Closed'""", (cint(self.smart_meter), cint(self.smart_meter), self.name))

	def on_trash(self):
		if frappe.db.exists("Connection Meter", {"meter": self.name}):
			frappe.throw(_("This meter has installation history and can't be deleted."))
