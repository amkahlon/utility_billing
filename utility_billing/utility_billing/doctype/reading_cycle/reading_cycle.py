# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Reading Cycle: one billing month's readings for one society and utility (FS-RDG-01)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing.readings import month_start

KEY_FIELDS = ("society", "utility", "billing_month")


class ReadingCycle(Document):
	def before_insert(self):
		self.billing_month = month_start(self.billing_month)
		self.cycle_name = f"{self.society}-{self.utility}-{getdate(self.billing_month).strftime('%Y-%m')}"

	def validate(self):
		self.billing_month = month_start(self.billing_month)
		if self.reading_from and self.reading_to and getdate(self.reading_to) < getdate(self.reading_from):
			frappe.throw(_("Reading Window To can't be before Reading Window From."))
		if not self.is_new():
			before = self.get_doc_before_save()
			if before and any(str(before.get(f)) != str(self.get(f)) for f in KEY_FIELDS):
				frappe.throw(_("Society, utility and month can't be changed after the cycle is created."))
			if before and before.status == "Closed" and not self.flags.counts_only:
				frappe.throw(_("This reading cycle is closed."))

	def on_trash(self):
		if frappe.db.exists("Meter Reading", {"reading_cycle": self.name}):
			frappe.throw(_("This cycle has readings and can't be deleted."))

	def refresh_counts(self):
		rows = frappe.get_all("Meter Reading", filters={"reading_cycle": self.name}, fields=["status", "units"])

		def n(*statuses):
			return sum(1 for r in rows if r.status in statuses)

		values = {
			"total_readings": len(rows),
			"pending_readings": n("Pending", "Re-read Requested"),
			"flagged_readings": n("Flagged"),
			"valid_readings": n("Valid", "Approved"),
			"estimated_readings": n("Estimated"),
			"total_units": sum(flt(r.units) for r in rows if r.status in ("Valid", "Approved", "Estimated")),
		}
		self.update(values)
		self.db_set(values, update_modified=False)
