# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumer: a person or company who owns, rents or is billed for a property (FS-MST-02)."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


def format_cnic(value: str) -> str | None:
	"""Return a CNIC as 12345-1234567-1, or None if it doesn't have 13 digits."""
	digits = re.sub(r"\D", "", value or "")
	if len(digits) != 13:
		return None
	return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"


class Consumer(Document):
	def validate(self):
		self.consumer_name = (self.consumer_name or "").strip()
		self.id_number = (self.id_number or "").strip() or None
		if self.id_number and self.id_type == "CNIC":
			formatted = format_cnic(self.id_number)
			if not formatted:
				frappe.throw(_("CNIC must have 13 digits, for example 35202-1234567-1."))
			self.id_number = formatted
		self.check_duplicate_id()

	def check_duplicate_id(self):
		if not self.id_number:
			return
		other = frappe.db.get_value(
			"Consumer",
			{"id_type": self.id_type, "id_number": self.id_number, "name": ["!=", self.name]},
			["name", "consumer_name"],
			as_dict=True,
		)
		if other:
			frappe.throw(
				_("{0} {1} already belongs to {2} ({3}).").format(
					self.id_type, self.id_number, other.consumer_name, other.name
				),
				title=_("Duplicate consumer"),
			)
