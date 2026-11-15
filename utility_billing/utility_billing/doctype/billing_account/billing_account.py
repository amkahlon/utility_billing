# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Billing Account: one customer's accounts across services under one number (FS-MST-20, 21). See billaccounts.py."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import billaccounts


class BillingAccount(Document):
	def autoname(self):
		self.account_no = self.account_no or billaccounts.new_number()
		self.name = self.account_no

	def validate(self):
		self.consumer_name = frappe.db.get_value("Consumer", self.consumer, "consumer_name")
		if not self.connections:
			frappe.throw(_("Add the accounts (services) this billing account covers."))
		seen = set()
		for r in self.connections:
			c = frappe.db.get_value("Connection", r.connection, ["society", "utility", "property", "status"], as_dict=True)
			if c.society != self.society:
				frappe.throw(_("Row {0}: account {1} belongs to another society.").format(r.idx, r.connection))
			if r.connection in seen:
				frappe.throw(_("Row {0}: account {1} is listed twice.").format(r.idx, r.connection))
			seen.add(r.connection)
			r.utility, r.property = c.utility, c.property
			other = billaccounts.account_of_connection(r.connection)
			if other and other != self.name and self.status == "Active":
				frappe.throw(_("Row {0}: account {1} is already in billing account {2}.").format(r.idx, r.connection, other))
