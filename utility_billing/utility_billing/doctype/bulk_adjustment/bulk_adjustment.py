# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bulk Adjustment: one credit or debit for a whole group of accounts, in one approved transaction (FS-WF-12), for example
compensation for a long outage. A Billing Manager makes it, a General Manager approves it, and only then does each account
get its own ledger entry. A credit that is more than an account's balance skips that account and says so."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import account, approval, corrections

APPROVER_ROLE = "General Manager"


class BulkAdjustment(Document):
	def validate(self):
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount per account."))
		account.check_reason(self.reason_code, "Bulk Adjustment")
		need = {"Block": "block", "Category": "consumer_category", "Feeder": "feeder", "Transformer": "transformer"}.get(self.audience)
		if need and not self.get(need):
			frappe.throw(_("Choose the {0}.").format(_(self.audience).lower()))
		for field, table in (("feeder", "Feeder"), ("transformer", "Transformer")):
			if self.get(field) and frappe.db.get_value(table, self.get(field), "society") != self.society:
				frappe.throw(_("{0} doesn't belong to {1}.").format(self.get(field), self.society))
		if len((self.remarks or "").strip()) < 5:
			frappe.throw(_("Explain the adjustment (at least 5 characters)."))
		if self.docstatus == 0:
			corrections.prepare_bulk(self)
		self.adjustment_title = f"{self.note_type} Rs {self.amount:,.0f} · {self.audience}" + (f": {self.get(need)}" if need else "")
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING and not self.accounts:
			frappe.throw(_("No account would take this adjustment."))

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A bulk adjustment is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		corrections.post_bulk(self)
		approval.notify(self, APPROVER_ROLE)

	def on_cancel(self):
		corrections.reverse_bulk(self)
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
