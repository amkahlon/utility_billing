# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Customer Ledger Entry: one line of a consumer account (FS-REC-01, 10, 11). Written only by
account.py; never edited or deleted (FS-REC-12). Mistakes are corrected by a reversal entry."""

import frappe
from frappe import _
from frappe.model.document import Document

# Only the link to the entry that reversed it may change after an entry is written.
MUTABLE = {"reversed_by", "modified", "modified_by"}


class CustomerLedgerEntry(Document):
	def validate(self):
		if self.is_new():
			if not self.flags.from_account:
				frappe.throw(_("Ledger entries are posted by bills, payments, fees and notes, not entered by hand."))
			return
		before = self.get_doc_before_save()
		if before:
			changed = [f for f in self.meta.get_valid_columns() if f not in MUTABLE and self.get(f) != before.get(f)]
			if changed:
				frappe.throw(_("A posted ledger entry can't be changed; reverse it instead."))

	def on_trash(self):
		frappe.throw(_("A posted ledger entry can't be deleted; reverse it instead."))
