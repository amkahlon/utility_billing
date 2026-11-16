# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""RA Finding: one revenue assurance finding as a case (FS-RA-09). The checks create it (revenue_assurance.py); people assign it,
investigate, and close it with a result and the document that settled it (a detection bill, a bill revision, a correction).
Only a Billing Manager or General Manager reopens a closed finding."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime
from utility_billing.utility_billing import rights

MANAGERS = {"System Manager", "Billing Manager", "General Manager"}


class RAFinding(Document):
	def validate(self):
		self.finding_title = f"{(self.check_type or '')[6:]}: {self.subject or ''}".strip(": ")
		before = self.get_doc_before_save()
		was = before.status if before else None
		if self.assigned_to and self.status == "Open":
			self.status = "Assigned"
		if self.status == "Assigned" and not self.assigned_to:
			frappe.throw(_("Choose who the finding is assigned to."))
		if self.status == "Closed":
			if not self.result:
				frappe.throw(_("Choose the result before closing the finding."))
			if self.result != "No issue" and not (self.result_document or (self.notes or "").strip()):
				frappe.throw(_("Name the document that settled it, or write what was done in the notes."))
			if flt(self.amount_recovered) < 0:
				frappe.throw(_("The amount recovered can't be negative."))
			if was != "Closed":
				self.closed_by, self.closed_on = frappe.session.user, now_datetime()
		elif was == "Closed":
			if not rights.can("ra.manage"):
				raise frappe.PermissionError(_("Only a Billing Manager or General Manager reopens a closed finding."))
			self.closed_by = self.closed_on = None
