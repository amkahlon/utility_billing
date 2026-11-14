# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""User Access: the societies, cash counters and blocks a user works in (FS-ACC-01). Saved as Frappe User Permissions (see access.py)."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import access


class UserAccess(Document):
	def validate(self):
		self.full_name = frappe.db.get_value("User", self.user, "full_name") or self.user
		if self.user in ("Administrator", "Guest"):
			frappe.throw(_("{0} isn't limited to societies.").format(self.user))
		if not self.all_societies and not self.societies:
			frappe.throw(_("Choose the societies this user works in, or tick All Societies."))
		if self.all_societies:
			self.societies = []
		mine = {r.society for r in self.societies}
		for r in self.counters:
			soc = frappe.db.get_value("Cash Counter", r.cash_counter, "society")
			if not self.all_societies and soc not in mine:
				frappe.throw(_("Cash counter {0} is in {1}, which isn't one of this user's societies.").format(r.cash_counter, soc))
		for r in self.blocks:
			if not self.all_societies and r.society not in mine:
				frappe.throw(_("Block {0}: {1} isn't one of this user's societies.").format(r.block, r.society))

	def on_update(self):
		access.sync_user_permissions(self)

	def on_trash(self):
		self.societies, self.counters, self.all_societies = [], [], 1
		access.sync_user_permissions(self)
