# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Connection Flag Type: a special case a connection can carry (staff, exempt, under dispute, court case, bill on hold) and what it
changes in billing, surcharge and recovery (FS-MST-14). See flags.py."""

import frappe
from frappe import _
from frappe.model.document import Document


class ConnectionFlagType(Document):
	def validate(self):
		self.flag_code = (self.flag_code or "").strip().upper().replace(" ", "-")

	def on_trash(self):
		if frappe.db.exists("Connection Flag Row", {"flag": self.name}):
			frappe.throw(_("Flag {0} is on connections; switch it off (In Use) instead of deleting it.").format(self.name))
