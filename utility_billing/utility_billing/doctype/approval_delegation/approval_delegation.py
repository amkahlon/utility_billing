# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Approval Delegation: an approver away (for example on leave) lends their approver roles to another user for the dates (FS-ACC-07).
The roles are given on the first day and taken back after the last (access.run_delegations, daily); maker-checker still applies."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from utility_billing.utility_billing import access

OVERSEERS = {"System Manager", "General Manager", "Society Admin"}


class ApprovalDelegation(Document):
	def validate(self):
		if self.delegator == self.delegate:
			frappe.throw(_("Choose someone else to approve instead."))
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The delegation ends before it starts."))
		if self.is_new() and getdate(self.to_date) < getdate(nowdate()):
			frappe.throw(_("The dates are already past."))
		if frappe.session.user != self.delegator and not set(frappe.get_roles()) & OVERSEERS:
			frappe.throw(_("Only the approver themselves or a General Manager can delegate their approvals."))
		if not set(access.APPROVER_ROLES) & access.roles_of(self.delegator):
			frappe.throw(_("{0} doesn't approve anything, so there is nothing to delegate.").format(self.delegator))
		if not frappe.db.get_value("User", self.delegate, "enabled"):
			frappe.throw(_("{0} is not an active user.").format(self.delegate))
		if not self.is_new() and self.get_doc_before_save().status in ("Active", "Ended", "Cancelled") and self.has_value_changed("delegate"):
			frappe.throw(_("A delegation that has started can't be moved to someone else; cancel it and make a new one."))

	def on_update(self):
		if self.status == "Scheduled" and getdate(self.from_date) <= getdate(nowdate()) <= getdate(self.to_date):
			access.start_delegation(self)

	def on_trash(self):
		if self.status == "Active":
			frappe.throw(_("Cancel the delegation first; its roles are taken back then."))
