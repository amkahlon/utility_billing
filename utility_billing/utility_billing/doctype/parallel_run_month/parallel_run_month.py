# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Parallel Run Month: one month of the parallel run, the previous system's bills and payments against ours (increment 29).

During the parallel run (about two months, user's decision 2026-11-18) the society still bills from its previous system. Each month
Load Payments records here the payments that system took (each once), and Compare puts its bills beside our bill run account by
account, saying what explains each difference. Go-live is when a month comes out clean. See migration.py."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import migration
from utility_billing.utility_billing.readings import month_start


class ParallelRunMonth(Document):
	def validate(self):
		self.billing_month = month_start(self.billing_month)
		if self.bill_run:
			run = frappe.db.get_value("Bill Run", self.bill_run, ["society", "billing_month"], as_dict=True)
			if run.society != self.society or month_start(run.billing_month) != self.billing_month:
				frappe.throw(_("Bill run {0} isn't this society's run of this month.").format(self.bill_run))
		if self.profile and frappe.db.get_value("Migration Profile", self.profile, "society") != self.society:
			frappe.throw(_("Profile {0} belongs to another society.").format(self.profile))
		self.run_title = f"{frappe.utils.formatdate(self.billing_month, 'MMMM yyyy')} · {self.society}"
		if self.has_value_changed("bill_run") and not self.is_new():
			self.comparison_status = "Draft"


def _doc(name):
	doc = frappe.get_doc("Parallel Run Month", name)
	doc.check_permission("write")
	return doc


@frappe.whitelist(methods=["POST"])
def load_payments(name: str) -> dict:
	doc = _doc(name)
	out = migration.load_payments(doc)
	doc.save()
	return out


@frappe.whitelist(methods=["POST"])
def compare(name: str) -> dict:
	doc = _doc(name)
	out = migration.compare(doc)
	doc.save()
	return out
