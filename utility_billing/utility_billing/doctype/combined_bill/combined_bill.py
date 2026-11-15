# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

from frappe.model.document import Document
from frappe.utils import getdate


class CombinedBill(Document):
	def autoname(self):
		"""CB-<billing account>-<billing month>: one combined bill per billing account and month (it is updated, not added to)."""
		self.name = f"CB-{self.billing_account}-{getdate(self.billing_month):%Y%m}"
