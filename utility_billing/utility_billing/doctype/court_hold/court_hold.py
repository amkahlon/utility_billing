# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Court Hold (increment 36): sets part of what an account owes aside by court order, after go-live. User's decision 2026-11-20: "on hold
will be a separate transaction just like the installments or bill correction or due date extension. it would also generate a revised
bill", and the held amount "is part of total receivables but it is not part of payable amount unless its withdrawn".

A Billing Officer or Billing Manager makes it, a Billing Manager or General Manager other than the maker approves it. On approval
(held.place_hold) the amount leaves what the account owes, a Held Amount records it, and the latest bill is reissued as a revised bill
without it, with a new due date. Releasing or writing off the Held Amount is done on the Held Amount, as for the go-live holds."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import approval, held

APPROVER_ROLE = "Billing Manager"


class CourtHold(Document):
	def validate(self):
		if not self.connection:
			frappe.throw(_("Choose the account."))
		self.court_order = (self.court_order or "").strip()
		if not self.court_order:
			frappe.throw(_("Enter the court order reference."))
		if flt(self.amount) <= 0:
			frappe.throw(_("Enter the amount the court has set aside."))
		if len((self.remarks or "").strip()) < 5:
			frappe.throw(_("Explain the court's order (at least 5 characters)."))
		other = frappe.db.get_value("Court Hold", {"connection": self.connection, "name": ["!=", self.name or ""], "docstatus": 0,
		                                           "workflow_state": ["in", [approval.DRAFT, approval.PENDING, approval.REJECTED]]})
		if other:
			frappe.throw(_("{0} is already open for account {1}: finish it first.").format(other, self.connection))
		if self.docstatus == 0:
			held.fill_hold_preview(self)
		self.hold_title = f"{self.connection} · Rs {flt(self.amount):,.0f} · {self.court_order}"
		approval.validate_transition(self)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A court hold is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		held.place_hold(self)
		approval.notify(self, APPROVER_ROLE)

	def before_cancel(self):
		frappe.throw(_("A court hold isn't undone. Release the Held Amount when the court decides the consumer pays, or write it off."))
