# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery Case: one open case per account being chased. It names the recovery officer, records follow-ups (visit, call,
promise to pay) and holds the account out of automatic recovery steps while a legal case or an unexpired promise is open
(FS-REC-08, 15)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from utility_billing.utility_billing import recovery_engine as engine
from utility_billing.utility_billing import rights


class RecoveryCase(Document):
	def validate(self):
		self.fill_from_connection()
		self.check_unique_open_case()
		self.check_assignee()
		self.stamp_followups()
		self.apply_latest_promise()
		self.check_status()
		self.case_title = f"{self.consumer_name or self.connection} · {self.status}"

	def fill_from_connection(self):
		c = frappe.db.get_value("Connection", self.connection, ["society", "consumer_name", "property", "consumer_category"], as_dict=True)
		if not c:
			frappe.throw(_("Choose an account."))
		if self.society != c.society:
			frappe.throw(_("{0} doesn't belong to {1}.").format(self.connection, self.society))
		self.consumer_name = c.consumer_name
		self.consumer_category = c.consumer_category
		self.block = frappe.db.get_value("Property", c.property, "block") if c.property else None

	def check_unique_open_case(self):
		if self.status == engine.CASE_CLOSED:
			return
		other = frappe.db.get_value("Recovery Case", {"connection": self.connection, "status": ["!=", engine.CASE_CLOSED],
		                                              "name": ["!=", self.name]})
		if other:
			frappe.throw(_("{0} already has an open recovery case, {1}.").format(self.connection, other))

	def check_assignee(self):
		if self.assigned_to and not rights.can("recovery.handle_case", self.assigned_to):
			frappe.throw(_("{0} is not a Recovery Officer.").format(self.assigned_to))

	def stamp_followups(self):
		for row in self.followups:
			row.done_by = row.done_by or frappe.session.user
			if row.kind == "Promise to Pay":
				if not row.promise_date:
					frappe.throw(_("Row {0}: enter the date the consumer promised to pay by.").format(row.idx))
				if flt(row.promise_amount) <= 0:
					frappe.throw(_("Row {0}: enter the amount promised.").format(row.idx))

	def apply_latest_promise(self):
		"""A new promise-to-pay follow-up puts the case on a promise (unless it is closed or in court)."""
		if self.status in (engine.CASE_CLOSED, engine.CASE_LEGAL):
			return
		before = self.get_doc_before_save()
		known = len(before.followups) if before else 0
		for row in list(self.followups)[known:]:
			if row.kind == "Promise to Pay":
				self.status, self.promise_date, self.promise_amount = engine.CASE_PROMISE, row.promise_date, row.promise_amount

	def check_status(self):
		if self.status == engine.CASE_PROMISE:
			if not self.promise_date or flt(self.promise_amount) <= 0:
				frappe.throw(_("Enter the promised date and amount."))
		else:
			self.promise_date = self.promise_amount = None
		if self.status == engine.CASE_LEGAL and not (self.legal_case_no or "").strip():
			frappe.throw(_("Enter the case number of the legal case."))
		if self.status == engine.CASE_CLOSED:
			if not (self.close_reason or "").strip():
				frappe.throw(_("Give a reason for closing the case."))
			self.closed_on = self.closed_on or nowdate()
		else:
			self.closed_on = None
