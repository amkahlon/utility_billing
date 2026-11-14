# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Due Date Extension: moves one account's latest bill due date within limits (FS-WF-03). A Billing Manager may extend up to the
society's limit of days and times a year; a General Manager beyond it. The bill itself is not edited: the extended date is kept
beside it, the surcharge follows the new date, and a surcharge already charged is reversed (to come again after the new date if
the account is still unpaid)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from utility_billing.utility_billing import account, corrections


class DueDateExtension(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("A due date extension can't be changed. Grant another one if needed."))
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		account.check_reason(self.reason_code, "Due Date Extension")
		bill, old, days = corrections.check_extension(self)
		self.flags.extension = (bill, old, days)
		self.old_due_date, self.days_extended, self.bill = old, days, bill.name
		self.granted_by = frappe.session.user
		self.extension_title = f"{self.connection} · {getdate(self.new_due_date).strftime('%d-%m-%Y')}"

	def after_insert(self):
		bill, old, days = self.flags.extension
		self.db_set("surcharge_action", corrections.apply_extension(self, bill, old, days))
