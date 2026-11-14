# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Year End Close: the year's receivables and revenue summaries and the balances carried forward (see yearend.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class YearEndClose(Document):
	def validate(self):
		if getdate(self.year_end) <= getdate(self.year_start):
			frappe.throw(_("The year must end after it starts."))
		self.close_title = f"{self.society} · {getdate(self.year_start).strftime('%d %b %Y')} to {getdate(self.year_end).strftime('%d %b %Y')}"
		if self.status != "Closed":
			clash = frappe.db.exists("Year End Close", {
				"society": self.society, "name": ["!=", self.name or ""], "year_start": ["<=", self.year_end], "year_end": [">=", self.year_start]})
			if clash:
				frappe.throw(_("{0} already covers part of this year.").format(clash))

	def on_trash(self):
		if self.status == "Closed":
			frappe.throw(_("A closed year can't be deleted."))
