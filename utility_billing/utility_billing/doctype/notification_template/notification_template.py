# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Notification Template: the text of a message per event, channel and language, with fields such as {consumer} and {amount}
(FS-NTF-03). A society's own template wins over the default one (no society)."""

import frappe
from frappe import _
from frappe.model.document import Document


class NotificationTemplate(Document):
	def validate(self):
		if not (self.body or "").strip():
			frappe.throw(_("Write the message text."))
		other = frappe.db.get_value("Notification Template", {"society": self.society or ["is", "not set"], "event": self.event,
		                                                      "channel": self.channel, "language": self.language, "name": ["!=", self.name]})
		if other:
			frappe.throw(_("{0} already is the template for this event, channel and language.").format(other))
		if self.channel != "WhatsApp":
			self.whatsapp_status, self.whatsapp_template_name = "Not submitted", None
		elif self.whatsapp_status == "Approved" and not (self.whatsapp_template_name or "").strip():
			frappe.throw(_("Enter the template name registered at Meta."))
		if self.channel == "SMS" and len(self.body) > 480:
			frappe.throw(_("An SMS of more than 480 characters costs three or more messages; shorten it."))
		self.title = f"{self.event} · {self.channel} · {self.language}" + (f" · {self.society}" if self.society else " · default")
