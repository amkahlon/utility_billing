# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Legacy Bill: a month billed by the society's previous system, brought over by a migration batch (increment 29). Read-only history:
the bill's history table and Consumer 360 show it for the months before the account's first bill here. Nothing is posted from it;
what the account owed at go-live comes over as its opening balance."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing.readings import month_start


class LegacyBill(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		self.consumer_name, self.society = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"])
		other = frappe.db.get_value("Legacy Bill", {"connection": self.connection, "billing_month": self.billing_month, "name": ["!=", self.name]})
		if other:
			frappe.throw(_("Account {0} already has its {1} bill from the previous system.").format(
				self.connection, frappe.utils.formatdate(self.billing_month, "MMMM yyyy")))
		if not self.is_new() and not frappe.flags.in_migration_undo and "System Manager" not in frappe.get_roles():
			frappe.throw(_("History from the previous system can't be changed."))
