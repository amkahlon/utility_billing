# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tariff Adjustment: FPA or QTA for one society, utility and billing month (FS-CFG-09..12).

Each consumer pays the rate on their own units of the reference month, by default the billing
month minus 2 (the DISCO's lag; decided by the user). Approved through "Tariff Adjustment Approval"
(Billing Manager makes, General Manager approves), with the shared maker-checker rules."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, getdate

from utility_billing.utility_billing import approval
from utility_billing.utility_billing.readings import month_start

APPROVER_ROLE = "General Manager"


class TariffAdjustment(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		self.reference_month = month_start(self.reference_month) if self.reference_month else add_months(self.billing_month, -2)
		if getdate(self.reference_month) >= getdate(self.billing_month):
			frappe.throw(_("The reference month must be before the billing month."))
		whole = set()
		for row in self.exempt_categories:
			if row.consumer_category in whole:
				frappe.throw(_("{0} is listed twice.").format(row.consumer_category))
			whole.add(row.consumer_category)
		limited = set()
		for row in self.get("units_limit_categories") or []:  # increment 57: a units limit for some categories only
			if row.consumer_category in limited:
				frappe.throw(_("{0} is listed twice.").format(row.consumer_category))
			if row.consumer_category in whole:
				frappe.throw(_("{0} is exempt as a whole category and also given a units limit. Keep it in one list: Exempt Categories "
				               "for every account of it, or Units Limit Applies To for accounts up to {1} units.").format(
					row.consumer_category, frappe.utils.flt(self.exempt_below_units)))
			limited.add(row.consumer_category)
		if limited and not frappe.utils.flt(self.exempt_below_units):
			frappe.throw(_("Set Exempt Consumers Up To (units) for the categories under Units Limit Applies To."))
		self.adjustment_title = (
			f"{self.adjustment_type} {getdate(self.billing_month).strftime('%b %Y')} "
			f"(on {getdate(self.reference_month).strftime('%b %Y')} units)"
		)
		self.check_charge_type()
		self.check_duplicate()
		approval.validate_transition(self)

	def check_charge_type(self):
		"""An FPA or QTA is billed as its own charge, never as the energy charge (user's bill, 2026-11-16: an FPA saved with Charge
		Type Energy printed as a second 'Energy Charges' line). A charge type named after the adjustment is the default."""
		energy = frappe.db.get_value("Society", self.society, "energy_charge_type") if self.society else None
		if not self.charge_type and frappe.db.exists("Charge Type", self.adjustment_type):
			self.charge_type = self.adjustment_type
		if self.charge_type and self.charge_type == energy:
			frappe.throw(_("An {0} is billed as its own charge, not as the energy charge {1}. Choose the {0} charge type.").format(
				self.adjustment_type, energy))
		if self.charge_type and frappe.db.get_value("Charge Type", self.charge_type, "calculation_basis") != "Per Unit":
			frappe.throw(_("Charge type {0} isn't charged per unit, as an {1} is.").format(self.charge_type, self.adjustment_type))

	def check_duplicate(self):
		other = frappe.db.get_value(
			"Tariff Adjustment",
			{"society": self.society, "utility": self.utility, "billing_month": self.billing_month,
			 "adjustment_type": self.adjustment_type, "docstatus": ["<", 2], "name": ["!=", self.name]},
			"name",
		)
		if other:
			frappe.throw(
				_("{0} {1} already exists for this month. Amend or cancel it instead.").format(self.adjustment_type, other)
			)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("An adjustment is approved through its workflow, not submitted directly."))

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		approval.notify(self, APPROVER_ROLE)

	def on_cancel(self):
		if frappe.db.exists("Utility Bill", {"society": self.society, "billing_month": self.billing_month, "docstatus": 1}):
			frappe.throw(_("Bills for this month are posted; correct them through adjustments instead."))
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
