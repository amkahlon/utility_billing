# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Template: a society's bill format, designed on the Bill Designer page from blocks (increment 27).

SoftVerx staff, a Society Admin or a Billing Manager make it; a Billing Manager or General Manager who didn't make it approves it
(maker-checker). Once approved it can't change: a change is an amended copy that is approved again, so a bill always reprints
with the exact template it was issued with (FS-RUN-09)."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import approval, bill_designer

DEFAULT_APPROVER = "Billing Manager"


class BillTemplate(Document):
	def validate(self):
		self.layout = bill_designer.clean_layout(self.layout)
		bill_designer.check_page(self)
		approval.validate_transition(self)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A bill template is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_submit(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_cancel(self):
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
