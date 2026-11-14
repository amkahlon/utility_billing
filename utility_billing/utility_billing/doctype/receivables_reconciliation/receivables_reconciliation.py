# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Receivables Reconciliation: the record of one comparison of the customer accounts total with the
receivables in the journals (FS-GL-04, FS-REC-13). Made by gl.reconcile, daily and on demand."""

import frappe
from frappe import _
from frappe.model.document import Document


class ReceivablesReconciliation(Document):
	def validate(self):
		if self.is_new() and not self.flags.from_gl:
			frappe.throw(_("Run the reconciliation instead of entering it by hand."))
		if not self.is_new():
			frappe.throw(_("A reconciliation record can't be changed."))
