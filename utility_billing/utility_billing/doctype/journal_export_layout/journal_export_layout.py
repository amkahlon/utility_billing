# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Journal Export Layout: which columns, in which order and format, an export file has, so journals can
be imported into the society's accounting software (FS-GL-03)."""

import frappe
from frappe import _
from frappe.model.document import Document


class JournalExportLayout(Document):
	def validate(self):
		if not self.columns:
			frappe.throw(_("Add at least one column."))
		for c in self.columns:
			c.header = (c.header or "").strip()
			if c.source == "Fixed Text" and not c.fixed_text:
				frappe.throw(_("Column {0}: enter the fixed text.").format(c.header))
