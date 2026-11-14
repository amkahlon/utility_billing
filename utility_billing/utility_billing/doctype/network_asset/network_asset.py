# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Network Asset: the asset register for meters, transformers and other infrastructure, with purchase, installation, warranty,
status and maintenance history (FS-MST-26)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing import network


class NetworkAsset(Document):
	def validate(self):
		if self.reference_doctype and not self.reference_name:
			frappe.throw(_("Choose the record this asset is."))
		if self.reference_doctype and self.reference_name and frappe.get_meta(self.reference_doctype).has_field("society"):
			network.check_society(self, self.reference_doctype, self.reference_name, self.reference_doctype)
		if self.purchase_date and self.installation_date and getdate(self.installation_date) < getdate(self.purchase_date):
			frappe.throw(_("An asset can't be installed before it was bought."))
		if flt(self.purchase_cost) < 0:
			frappe.throw(_("Cost can't be negative."))
		for r in self.maintenance:
			if r.next_due and getdate(r.next_due) < getdate(r.maintenance_date):
				frappe.throw(_("Maintenance row {0}: the next due date is before the work.").format(r.idx))
		self.next_maintenance = network.next_maintenance(self)
		network.check_location(self)
