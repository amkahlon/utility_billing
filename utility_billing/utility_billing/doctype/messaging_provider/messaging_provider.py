# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Messaging Provider: how a channel's messages leave the system (FS-NTF-07). Providers are interchangeable: change the
record, not the code. A key is kept encrypted on this site only."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MessagingProvider(Document):
	def validate(self):
		if flt(self.cost_per_message) < 0:
			frappe.throw(_("The cost can't be negative."))
		if self.provider_type == "Frappe Email" and self.channel != "Email":
			frappe.throw(_("Frappe Email sends email only."))
		if self.provider_type == "HTTP gateway":
			if not (self.endpoint_url or "").startswith("https://"):
				frappe.throw(_("The gateway URL must start with https://"))
		else:
			self.endpoint_url = self.sender_id = None
		self.provider_title = f"{self.channel} · {self.provider_type}" + (f" · {self.society}" if self.society else " · all societies")
