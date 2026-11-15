# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Service Agreement (FS-MST-19, FS-CFG-32; increment 22): the terms an account is supplied on for a period: start and end, renewal,
termination, a contract tariff that replaces the category tariff while it is in force, who is liable for arrears and the deposit asked.
Made by a Billing Manager and put in force by submitting it (a General Manager or Billing Manager); a daily job renews agreements set to
renew and ends those past their end date. One agreement per account at a time."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, getdate, nowdate


class ServiceAgreement(Document):
	def validate(self):
		conn = frappe.db.get_value("Connection", self.connection, ["consumer", "society", "utility", "consumer_name"], as_dict=True)
		self.consumer, self.society, self.utility = conn.consumer, conn.society, conn.utility
		if self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("The end is before the start."))
		if self.tariff:
			t = frappe.db.get_value("Tariff", self.tariff, ["society", "utility", "docstatus"], as_dict=True)
			if not t or t.docstatus != 1:
				frappe.throw(_("Tariff {0} isn't approved: a contract tariff goes through the tariff approval first.").format(self.tariff))
			if t.society != self.society or t.utility != self.utility:
				frappe.throw(_("Tariff {0} is for another society or utility.").format(self.tariff))
		if cint(self.auto_renew) and cint(self.renewal_months) < 1:
			frappe.throw(_("Say for how many months it renews."))
		self.check_overlap()
		self.agreement_title = f"{conn.consumer_name or self.connection} · {frappe.utils.formatdate(self.start_date)}" + (
			f" – {frappe.utils.formatdate(self.end_date)}" if self.end_date else "")

	def check_overlap(self):
		end = getdate(self.end_date) if self.end_date else getdate("2999-12-31")
		for other in frappe.get_all("Service Agreement", filters={"connection": self.connection, "docstatus": 1, "name": ["!=", self.name],
		                                                          "status": "Active"}, fields=["name", "start_date", "end_date"]):
			o_end = getdate(other.end_date) if other.end_date else getdate("2999-12-31")
			if getdate(self.start_date) <= o_end and getdate(other.start_date) <= end:
				frappe.throw(_("Agreement {0} already covers this account in that period. End it first.").format(other.name))

	def on_submit(self):
		self.db_set("status", "Active" if not self.end_date or getdate(self.end_date) >= getdate(nowdate()) else "Ended")

	def on_cancel(self):
		self.db_set("status", "Terminated")


@frappe.whitelist(methods=["POST"])
def terminate(name: str, on: str, reason: str):
	"""End an agreement early, from a date, with a reason; bills after that date use the category tariff again."""
	doc = frappe.get_doc("Service Agreement", name)
	doc.check_permission("submit")
	if doc.docstatus != 1 or doc.status != "Active":
		frappe.throw(_("Only an agreement in force can be terminated."))
	if len((reason or "").strip()) < 5:
		frappe.throw(_("Give the reason for terminating it."))
	if getdate(on) < getdate(doc.start_date):
		frappe.throw(_("It can't end before it started."))
	doc.db_set({"status": "Terminated", "end_date": getdate(on), "terminated_on": getdate(on), "termination_reason": reason.strip()})
	doc.add_comment("Info", _("Terminated from {0}: {1}").format(frappe.utils.formatdate(on), reason.strip()))
	return doc.name


def daily(today=None):
	"""Renew agreements set to renew whose end has come; end the others past their end date."""
	today = getdate(today or nowdate())
	for a in frappe.get_all("Service Agreement", filters={"docstatus": 1, "status": "Active", "end_date": ["<", today]},
	                        fields=["name", "end_date", "auto_renew", "renewal_months"]):
		if cint(a.auto_renew):
			new_end = add_days(add_months(add_days(a.end_date, 1), cint(a.renewal_months) or 12), -1)
			frappe.db.set_value("Service Agreement", a.name, "end_date", new_end)
			frappe.get_doc("Service Agreement", a.name).add_comment("Info", _("Renewed to {0}").format(frappe.utils.formatdate(new_end)))
		else:
			frappe.db.set_value("Service Agreement", a.name, "status", "Ended")
