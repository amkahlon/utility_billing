# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery Run: works out the notice each defaulter is due, lets the officer untick accounts, and issues the rest
(FS-REC-05, 06). A run that has been issued can't be edited."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from utility_billing.utility_billing import recovery


class RecoveryRun(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status != "Draft" and not self.flags.issuing:
			frappe.throw(_("This run is {0} and can't be changed.").format(before.status.lower()))
		self.run_title = f"{self.society} recovery {getdate(self.as_of).strftime('%d-%m-%Y')}"

	@frappe.whitelist()
	def prepare(self):
		recovery.require(recovery.RUNNERS, _("prepare a recovery run"))
		recovery.prepare_run(self)
		self.save()
		return self.accounts_due

	@frappe.whitelist()
	def issue(self):
		n = recovery.issue_run(self)
		self.flags.issuing = True
		self.save()
		return n
