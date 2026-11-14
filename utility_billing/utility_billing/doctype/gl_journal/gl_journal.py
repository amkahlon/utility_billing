# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""GL Journal: a summary journal for the general ledger (FS-GL-02), made only by gl.py from customer
ledger entries. Never edited or deleted; only its export record changes."""

import frappe
from frappe import _
from frappe.model.document import Document

MUTABLE = {"export_count", "last_exported_on", "last_exported_by", "modified", "modified_by"}


class GLJournal(Document):
	def validate(self):
		if self.is_new():
			if not self.flags.from_gl:
				frappe.throw(_("Journals are made from the customer accounts (Make Journals), not entered by hand."))
			return
		before = self.get_doc_before_save()
		if before and any(self.get(f) != before.get(f) for f in self.meta.get_valid_columns() if f not in MUTABLE):
			frappe.throw(_("A journal can't be changed."))

	def on_trash(self):
		frappe.throw(_("A journal can't be deleted."))
