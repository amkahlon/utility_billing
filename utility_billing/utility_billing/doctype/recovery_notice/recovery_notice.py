# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Recovery Notice: one stage issued to one account on one date (FS-REC-05). Created by a Recovery Run."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from utility_billing.utility_billing import recovery_engine as engine


class RecoveryNotice(Document):
	def validate(self):
		self.notice_title = f"{self.stage} · {self.consumer_name or self.connection} · {getdate(self.issue_date).strftime('%d-%m-%Y')}"
		before = self.get_doc_before_save()
		if self.status == "Cancelled" and not (self.cancel_reason or "").strip():
			frappe.throw(_("Give a reason for cancelling the notice."))
		if (before and self.stage == engine.DISCONNECTION and before.status == "Awaiting Approval" and self.status == "Issued"
		        and not self.approved_by):
			frappe.throw(_("A disconnection order is approved with the Approve button, by a second user."))
