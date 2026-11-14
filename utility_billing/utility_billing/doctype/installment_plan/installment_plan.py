# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Installment Plan: arrears, surcharge and fees split into a down payment and monthly installments (FS-WF-05, FS-BIL-14, FS-CFG-18).

A Billing Officer, Billing Manager or Recovery Officer makes it; the approver the Society's levels name for its amount (default a
Billing Manager) approves it, and only once the down payment has been received. From the first month, each bill adds one
installment; a missed installment cancels the plan (see plans.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate

from utility_billing.utility_billing import approval, plans
from utility_billing.utility_billing.readings import month_start

DEFAULT_APPROVER = "Billing Manager"


class InstallmentPlan(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		if self.docstatus == 0:
			self.check_terms()
		self.plan_title = f"{self.consumer_name or self.connection} · {cint(self.installments)} installments of Rs {flt(self.installment_amount):,.0f}"
		approval.validate_transition(self)

	def check_terms(self):
		soc = frappe.get_cached_doc("Society", self.society)
		self.created_on = self.created_on or nowdate()
		self.owed_now = round(plans.owed_arrears(self.connection), 2)
		self.amount_to_split = flt(self.amount_to_split, 2)
		self.down_payment = flt(self.down_payment, 2)
		n = cint(self.installments)
		if self.amount_to_split <= 0:
			frappe.throw(_("Enter the amount under the plan."))
		if self.amount_to_split > self.owed_now + 0.005:
			frappe.throw(_("The account owes Rs {0} in arrears, surcharge and fees; the plan can't be for more than that.").format(f"{self.owed_now:,.2f}"))
		if not 2 <= n <= cint(soc.max_installments):
			frappe.throw(_("A plan has from 2 to {0} installments.").format(soc.max_installments))
		least = round(self.amount_to_split * flt(soc.min_down_payment_percent) / 100, 2)
		if not 0 <= self.down_payment < self.amount_to_split:
			frappe.throw(_("The down payment must be less than the amount under the plan."))
		if self.down_payment + 0.005 < least:
			frappe.throw(_("The society asks for a down payment of at least {0}% (Rs {1}).").format(f"{flt(soc.min_down_payment_percent):g}", f"{least:,.0f}"))
		other = frappe.db.get_value("Installment Plan", {"connection": self.connection, "name": ["!=", self.name], "docstatus": ["<", 2],
		                                                 "workflow_state": ["in", ["Draft", "Pending Approval", "Approved"]],
		                                                 "plan_status": ["in", ["Not started", "Active"]]})
		if other:
			frappe.throw(_("{0} is already an open plan for this account.").format(other))
		self.first_month = month_start(self.first_month)
		if getdate(self.first_month) < getdate(self.created_on).replace(day=1):
			frappe.throw(_("The first installment can't be on a bill of a month before the plan was requested."))
		rows = plans.schedule(self.amount_to_split, self.down_payment, n, self.first_month)
		self.installment_amount = float(rows[0][2]) if rows else 0
		self.set("schedule", [{"number": no, "billing_month": month, "amount": float(amount), "status": "Scheduled"} for no, month, amount in rows])

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("An installment plan is approved through its workflow, not submitted directly."))
		if not plans.down_payment_received(self):
			frappe.throw(_("The down payment of Rs {0} has not been received on account {1} since the plan was requested ({2}). Record the payment first.").format(
				f"{flt(self.down_payment):,.0f}", self.connection, self.created_on))
		if flt(self.amount_to_split) > plans.owed_arrears(self.connection) + flt(self.down_payment) + 0.005:
			frappe.throw(_("The account no longer owes enough in arrears for this plan."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def on_submit(self):
		self.db_set("plan_status", "Active")
		approval.notify(self, DEFAULT_APPROVER)

	def on_cancel(self):
		for row in self.schedule:  # in memory too: db_update_all below writes these rows back
			if row.status == "Scheduled":
				row.status = "Cancelled"
		self.plan_status = "Cancelled"
		approval.log(self, approval.APPROVED, approval.CANCELLED)
		self.db_update_all()
