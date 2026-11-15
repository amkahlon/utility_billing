# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Opening Balance Import: balances brought over from the society's previous system at go-live (gap 9). See opening.py.
Prepared by a Billing Manager or Accounts Officer, approved by a General Manager (maker-checker); approval posts it. A posted
import isn't cancelled."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from utility_billing.utility_billing import approval, opening

DEFAULT_APPROVER = "General Manager"


class OpeningBalanceImport(Document):
	def validate(self):
		live = frappe.db.get_value("Society", self.society, "go_live_date")
		if live and self.docstatus == 0:
			frappe.throw(_("{0} went live on {1}: its opening balances are already posted. Correct balances with credit or debit notes.").format(
				self.society, frappe.utils.formatdate(live)))
		if getdate(self.as_of_date) >= getdate(frappe.utils.nowdate()):
			frappe.throw(_("Opening balances are as at a day that has ended: choose a date before today."))
		if self.docstatus == 0:
			opening.check(self)
		self.import_title = f"{self.society} {_('opening balances as at')} {frappe.utils.formatdate(self.as_of_date)}"
		approval.validate_transition(self)
		if self.workflow_state == approval.PENDING and self.error_count:
			frappe.throw(_("{0} rows have problems. Fix them (Download Problems lists them) before submitting.").format(self.error_count))
		if self.workflow_state == approval.PENDING and not self.row_count:
			frappe.throw(_("Load the file first: there are no accounts to bring over."))

	def on_update(self):
		approval.notify(self, DEFAULT_APPROVER)

	def before_submit(self):
		if approval.workflow_active(self.doctype) and self.workflow_state != approval.APPROVED:
			frappe.throw(_("Opening balances are approved through their workflow, not submitted directly."))
		if opening.check(self):
			frappe.throw(_("{0} rows have problems now (for example an account got entries since the check). Reject it, reload and check again.").format(
				self.error_count))
		opening.check_accounts(self)

	def on_submit(self):
		opening.post(self)
		approval.notify(self, DEFAULT_APPROVER)

	def before_cancel(self):
		frappe.throw(_("Posted opening balances aren't cancelled. Correct an account with a credit or debit note."))
