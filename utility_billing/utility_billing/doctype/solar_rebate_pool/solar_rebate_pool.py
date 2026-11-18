# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Solar Rebate Pool: a society's monthly net-metering pool (FS-CFG-23, FS-BIL-13, SC-26, test case 2; increment 28).

Societies on the "Society rebate pool" method share, each month, what their solar residents saved the society:
    rebate pool   = residents' imports (all meters) − what the DISCO billed the society
    credit factor = rebate pool ÷ residents' exports
and each solar consumer is credited their exported units × the factor. A Billing Officer or Billing Manager prepares the month's
pool from the readings and the DISCO bill; a General Manager (or another approver than the maker) approves it, and only then can
the month's solar accounts be billed. The figures are printed as a notice on the month's bills."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import approval, netmeter
from utility_billing.utility_billing.readings import month_start

DEFAULT_APPROVER = "General Manager"


class SolarRebatePool(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		other = frappe.db.get_value("Solar Rebate Pool", {"society": self.society, "utility": self.utility, "billing_month": self.billing_month,
		                                                  "docstatus": ["<", 2], "name": ["!=", self.name]})
		if other:
			frappe.throw(_("{0} is already the solar rebate pool of this month.").format(other))
		if self.disco_bill:
			disco = frappe.db.get_value("DISCO Bill", self.disco_bill, ["society", "units_kwh", "export_units_kwh"], as_dict=True)
			if disco.society != self.society:
				frappe.throw(_("DISCO bill {0} belongs to another society.").format(self.disco_bill))
		for f in ("residents_import_units", "disco_import_units", "residents_export_units", "disco_export_units"):
			if flt(self.get(f)) < 0:
				frappe.throw(_("{0} can't be negative.").format(_(self.meta.get_label(f))))
		figures = netmeter.pool_figures(self.residents_import_units, self.disco_import_units, self.residents_export_units,
		                                self.disco_export_units)
		self.update({k: float(v) for k, v in figures.items()})
		if not (self.notice or "").strip():
			self.notice = netmeter.pool_notice(self)
		self.pool_title = f"{frappe.utils.formatdate(self.billing_month, 'MMMM yyyy')} · {self.society} · factor {flt(self.credit_factor):.4f}"
		approval.validate_transition(self)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("A solar rebate pool is approved through its workflow, not submitted directly."))
		if flt(self.residents_export_units) <= 0:
			frappe.throw(_("There are no solar exports this month, so there is nothing to share."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_submit(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_cancel(self):
		billed = frappe.db.get_value("Utility Bill", {"solar_pool": self.name, "docstatus": 1})
		if billed:
			frappe.throw(_("Bill {0} was posted with this pool. Revise the month's solar bills before cancelling it.").format(billed))
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()


@frappe.whitelist(methods=["POST"])
def get_figures(name: str):
	"""Residents' imports and exports from the month's readings, and the DISCO's figures from its bill, into a draft pool."""
	doc = frappe.get_doc("Solar Rebate Pool", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft pool can be refreshed."))
	doc.update(netmeter.residents_figures(doc.society, doc.utility, doc.billing_month))
	if doc.disco_bill:
		disco = frappe.db.get_value("DISCO Bill", doc.disco_bill, ["units_kwh", "export_units_kwh"], as_dict=True)
		doc.disco_import_units, doc.disco_export_units = flt(disco.units_kwh), flt(disco.export_units_kwh)
	doc.notice = None  # written again from the new figures
	doc.save()
	return doc.as_dict()
