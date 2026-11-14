# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Cashier Day (FS-PAY-03): a cashier opens a day at a counter, takes receipts, closes it with a cash
count and deposit slip, and a supervisor approves it. Receipts need an open day for today; nothing
changes after closing (a later reversal is recorded on the receipt, not on the closed day)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime, nowdate

APPROVERS = {"System Manager", "Billing Manager", "Accounts Officer"}
CHEQUE_MODES = {"Cheque", "Pay Order"}
CARD_MODES = {"Card"}


class CashierDay(Document):
	def before_insert(self):
		self.cashier = frappe.session.user
		self.business_date = nowdate()
		self.status = "Open"

	def validate(self):
		counter = frappe.db.get_value("Cash Counter", self.cash_counter, ["society", "enabled"], as_dict=True)
		if not counter or not counter.enabled:
			frappe.throw(_("Cash counter {0} isn't enabled.").format(self.cash_counter))
		self.society = counter.society
		self.opening_float = flt(self.opening_float)
		if self.opening_float < 0:
			frappe.throw(_("Opening cash can't be negative."))
		self.day_title = f"{self.cash_counter} {getdate(self.business_date).strftime('%d-%m-%Y')} {self.cashier}"
		if self.is_new():
			for field, value, who in (("cash_counter", self.cash_counter, _("Counter {0}").format(self.cash_counter)),
			                          ("cashier", self.cashier, _("You"))):
				other = frappe.db.get_value("Cashier Day", {field: value, "status": "Open"}, "name")
				if other:
					frappe.throw(_("{0} already has an open day ({1}). Close it first.").format(who, other))
		elif not self.flags.from_day_action:
			before = self.get_doc_before_save()
			if before and before.status != "Open":
				frappe.throw(_("A closed day can't be changed."))
			if before and before.status != self.status:
				frappe.throw(_("Use Close Day or Approve Day."))


def refresh_totals(day):
	"""Totals from the day's receipts; receipts reversed (with approval) are counted separately."""
	rows = frappe.get_all("Payment Receipt", filters={"cashier_day": day.name, "docstatus": ["in", [1, 2]]},
	                      fields=["docstatus", "mode", "amount"])
	reversed_ = [r for r in rows if r.docstatus == 2]
	day.receipt_count = len(rows)
	day.cash_total = sum(flt(r.amount) for r in rows if r.mode == "Cash")
	day.cheque_total = sum(flt(r.amount) for r in rows if r.mode in CHEQUE_MODES)
	day.card_total = sum(flt(r.amount) for r in rows if r.mode in CARD_MODES)
	day.total_collected = sum(flt(r.amount) for r in rows)
	day.reversed_count = len(reversed_)
	day.reversed_total = sum(flt(r.amount) for r in reversed_)
	cash_reversed = sum(flt(r.amount) for r in reversed_ if r.mode == "Cash")
	day.expected_cash = flt(day.opening_float) + day.cash_total - cash_reversed


def open_day_of(user=None):
	return frappe.db.get_value("Cashier Day", {"cashier": user or frappe.session.user, "status": "Open"},
	                           ["name", "cash_counter", "society", "business_date"], as_dict=True)


@frappe.whitelist(methods=["POST"])
def close_day(name: str, cash_counted: float, deposit_slip_no: str | None = None, remarks: str | None = None):
	day = frappe.get_doc("Cashier Day", name)
	if day.cashier != frappe.session.user and "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError(_("Only the cashier closes their own day."))
	if day.status != "Open":
		frappe.throw(_("This day is already closed."))
	refresh_totals(day)
	day.cash_counted = flt(cash_counted)
	if day.cash_counted < 0:
		frappe.throw(_("Cash counted can't be negative."))
	day.difference = day.cash_counted - flt(day.expected_cash)
	if day.difference and not (remarks or "").strip():
		frappe.throw(_("The cash count differs from the expected cash by Rs {0}; explain it in the remarks.").format(
			day.difference))
	day.deposit_slip_no = (deposit_slip_no or "").strip()
	day.close_remarks = remarks
	day.status = "Closed"
	day.closed_on = now_datetime()
	day.flags.from_day_action = True
	day.flags.ignore_permissions = True
	day.save()
	return day.as_dict()


@frappe.whitelist(methods=["POST"])
def approve_day(name: str):
	day = frappe.get_doc("Cashier Day", name)
	if not set(frappe.get_roles()) & APPROVERS:
		raise frappe.PermissionError(_("Only a Billing Manager or Accounts Officer approves a cashier day."))
	if day.cashier == frappe.session.user and frappe.session.user != "Administrator":
		frappe.throw(_("You can't approve your own day."))
	if day.status != "Closed":
		frappe.throw(_("Only a closed day can be approved."))
	day.status = "Approved"
	day.approved_by = frappe.session.user
	day.approved_on = now_datetime()
	day.flags.from_day_action = True
	day.flags.ignore_permissions = True
	day.save()
	return day.as_dict()


@frappe.whitelist()
def my_open_day():
	return open_day_of()
