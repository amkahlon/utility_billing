# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Settlement File: a bank or channel's daily file, matched to the recorded payments (FS-PAY-10, 14). The matching and the
actions live in settlement.py; this class holds the form's buttons."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from utility_billing.utility_billing import settlement


class SettlementFile(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status == "Closed" and not self.flags.closing:
			frappe.throw(_("This settlement file is closed and can't be changed."))
		self.file_title = f"{self.society} {self.mode} {getdate(self.file_date).strftime('%d-%m-%Y')}"

	@frappe.whitelist()
	def import_file(self):
		settlement.import_rows(self)
		self.save()
		return {"matched": self.matched, "flagged": self.amount_differs + self.not_recorded + self.missing_from_file + self.other_flags}

	@frappe.whitelist()
	def post_unrecorded(self):
		out = settlement.post_unrecorded(self)
		self.save()
		return out

	@frappe.whitelist()
	def close_file(self):
		settlement.close_file(self)
		self.flags.closing = True
		self.save()
		return self.status
