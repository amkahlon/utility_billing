# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill Dispute: the consumer disputes a bill (FS-WF-10). While it is open the late payment surcharge on that bill is held and
the disputed amount is left out of recovery. A Billing Manager or General Manager then decides: release the hold (the consumer
pays), settle it with an approved bill revision, or record that it was withdrawn."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import corrections


class BillDispute(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status != "Open" and not self.flags.resolving:
			frappe.throw(_("This dispute was decided and can't be changed."))
		conn = frappe.db.get_value("Connection", self.connection, ["consumer_name", "society"], as_dict=True)
		self.consumer_name, self.society = conn.consumer_name, conn.society
		bill = frappe.db.get_value("Utility Bill", self.bill, ["connection", "docstatus", "status", "payable_within_due"], as_dict=True)
		# a new dispute needs a posted bill; one already open stays valid if the bill is later replaced by a revised bill
		replaced = (not self.is_new()) and bill and bill.status == "Superseded"
		if not bill or bill.connection != self.connection or (bill.docstatus != 1 and not replaced):
			frappe.throw(_("{0} isn't a posted bill of account {1}.").format(self.bill, self.connection))
		self.disputed_amount = flt(self.disputed_amount, 2)
		if not 0 < self.disputed_amount <= flt(bill.payable_within_due):
			frappe.throw(_("The amount in dispute must be more than zero and no more than the bill's Rs {0}.").format(f"{flt(bill.payable_within_due):,.0f}"))
		if len((self.reason or "").strip()) < 5:
			frappe.throw(_("Write what the consumer says is wrong (at least 5 characters)."))
		if self.status == "Open":
			other = frappe.db.get_value("Bill Dispute", {"bill": self.bill, "status": "Open", "name": ["!=", self.name]})
			if other:
				frappe.throw(_("{0} already disputes this bill.").format(other))
		self.dispute_title = f"{self.connection} · Rs {self.disputed_amount:,.0f} · {self.status}"

	def after_insert(self):
		corrections.hold_surcharge(self)

	@frappe.whitelist()
	def resolve(self, outcome, remarks, revision=None):
		corrections.resolve_dispute(self, outcome, remarks, revision)
		self.flags.resolving = True
		self.save()
		return self.status
