# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Billing Period: one calendar month of a society, closed and locked at month end (see periods.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from utility_billing.utility_billing import periods


class BillingPeriod(Document):
	def validate(self):
		start, end = periods.bounds(self.period_start)
		self.period_start, self.period_end = start, end
		if start > getdate(nowdate()):
			frappe.throw(_("A month that hasn't started can't be set up for closing."))
		self.period_title = f"{self.society} · {start.strftime('%B %Y')}"
		clash = frappe.db.exists("Billing Period", {"society": self.society, "period_start": start, "name": ["!=", self.name or ""]})
		if clash:
			frappe.throw(_("{0} already exists for {1}.").format(start.strftime("%B %Y"), self.society))

	def on_trash(self):
		if self.status == "Closed":
			frappe.throw(_("A closed month can't be deleted; reopen it first."))
		if self.history:
			frappe.throw(_("A month that was closed once keeps its history and can't be deleted."))
