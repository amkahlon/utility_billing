# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Held Amount (increment 35): an amount an account owes but that a court order has set aside (LESCO's "Deferred"). It is part of
the receivable, on its own Held Receivables account, but it isn't in the customer ledger, so the bills, the late payment surcharge,
payments and recovery never see it. When the court decides, a Billing Manager or General Manager releases it (it moves into the
account's arrears and is billed with the next bill), or a General Manager writes it off. Rules in held.py."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import held


class HeldAmount(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status != "Held" and not self.flags.deciding:
			frappe.throw(_("This held amount was {0} and can't be changed.").format(_(before.status).lower()))
		if before and not self.flags.deciding:
			for f in ("amount", "connection", "society", "status"):
				if self.get(f) != before.get(f):
					frappe.throw(_("Only the court order reference can be changed here; use Release or Write Off."))
		self.held_title = f"{self.connection} · Rs {self.amount:,.0f} · {_(self.status)}"

	def on_trash(self):
		if not frappe.flags.in_test and not frappe.flags.in_migration_undo:
			frappe.throw(_("A held amount is kept: release it or write it off."))

	@frappe.whitelist()
	def release(self, remarks):
		return held.release(self, remarks)

	@frappe.whitelist()
	def write_off(self, remarks):
		return held.write_off(self, remarks)
