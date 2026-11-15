# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Check Meter Reading: a feeder's or transformer's check meter read on its reading route with the month's readings (FS-RDG-12).
Its units are the network input that line losses compare with what was billed, when the DISCO bill gives none for that feeder or
transformer (increment 23)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate


def measured_by(meter):
	"""(feeder, transformer) whose input this check meter records."""
	tx = frappe.db.get_value("Transformer", {"check_meter": meter}, ["name", "feeder"], as_dict=True)
	if tx:
		return tx.feeder, tx.name
	return frappe.db.get_value("Feeder", {"check_meter": meter}, "name"), None


def previous_of(meter, before_month):
	last = frappe.db.get_value("Check Meter Reading", {"meter": meter, "status": "Read", "billing_month": ["<", before_month]}, "present_reading",
	                           order_by="billing_month desc")
	return flt(last)


class CheckMeterReading(Document):
	def validate(self):
		cycle = frappe.db.get_value("Reading Cycle", self.reading_cycle, ["society", "utility", "billing_month", "status"], as_dict=True)
		if cycle.status == "Closed" and not self.is_new():
			frappe.throw(_("Reading cycle {0} is closed.").format(self.reading_cycle))
		self.society, self.utility, self.billing_month = cycle.society, cycle.utility, cycle.billing_month
		meter = frappe.db.get_value("Meter", self.meter, ["society", "utility", "multiplier"], as_dict=True)
		if meter.society != self.society or meter.utility != self.utility:
			frappe.throw(_("Meter {0} isn't a {1} meter of {2}.").format(self.meter, self.utility, self.society))
		if frappe.db.exists("Check Meter Reading", {"reading_cycle": self.reading_cycle, "meter": self.meter, "name": ["!=", self.name or ""]}):
			frappe.throw(_("Meter {0} already has a reading in this cycle.").format(self.meter))
		self.feeder, self.transformer = measured_by(self.meter)
		self.measures = (_("Transformer {0}").format(self.transformer) if self.transformer else
		                 _("Feeder {0}").format(self.feeder) if self.feeder else _("Not linked to a feeder or transformer"))
		self.multiplier = flt(meter.multiplier) or 1
		if self.is_new() and not self.previous_reading:
			self.previous_reading = previous_of(self.meter, self.billing_month)
		if self.present_reading in (None, "") or (not flt(self.present_reading) and not self.reading_date):
			self.status, self.units = "Pending", 0
			return
		if flt(self.present_reading) < flt(self.previous_reading) and len((self.remarks or "").strip()) < 5:
			frappe.throw(_("The present reading is below the previous one ({0}). Say why in Remarks (roll-over or meter replaced).").format(
				flt(self.previous_reading)))
		self.units = round(max(flt(self.present_reading) - flt(self.previous_reading), 0) * self.multiplier, 2)
		self.reading_date = self.reading_date or nowdate()
		self.status = "Read"
