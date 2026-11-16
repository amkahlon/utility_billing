# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Broadcast: one message to a feeder, transformer, block or the whole society, for outages and maintenance (FS-NTF-09)."""

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import notify
from utility_billing.utility_billing import rights


class Broadcast(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.status == "Sent" and not self.flags.sending:
			frappe.throw(_("A sent broadcast can't be changed."))
		need = {"Feeder": "feeder", "Transformer": "transformer", "Block": "block"}.get(self.audience)
		if need and not self.get(need):
			frappe.throw(_("Choose the {0} to send to.").format(_(self.audience).lower()))
		for field, table in (("feeder", "Feeder"), ("transformer", "Transformer")):
			if self.get(field) and frappe.db.get_value(table, self.get(field), "society") != self.society:
				frappe.throw(_("{0} doesn't belong to {1}.").format(self.get(field), self.society))
		if not (self.message or "").strip():
			frappe.throw(_("Write the message."))
		self.event = "outage.notice"
		self.broadcast_title = f"{self.society} · {self.audience}" + (f": {self.get(need)}" if need else "")

	@frappe.whitelist()
	def send(self):
		if not rights.can("messages.send"):
			frappe.throw(_("You can't send broadcasts."), frappe.PermissionError)
		self.flags.sending = True
		n = notify.send_broadcast(self)
		self.save()
		return n
