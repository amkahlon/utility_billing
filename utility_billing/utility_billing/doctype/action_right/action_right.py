# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Action Right: the roles allowed one action of the app (increment 25). See utility_billing.rights."""

import frappe
from frappe import _
from frappe.model.document import Document


class ActionRight(Document):
	def validate(self):
		seen = set()
		for r in self.roles:
			if r.role in seen:
				frappe.throw(_("Row {0}: role {1} is listed twice.").format(r.idx, r.role))
			seen.add(r.role)

	def on_update(self):
		from utility_billing.utility_billing import rights  # noqa: PLC0415

		rights.clear_cache()
