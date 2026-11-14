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
		self.validate_people()

	def validate_people(self):
		"""Company contacts (one main contact) and authorized representatives with their rights and dates (FS-MST-17, 18)."""
		if len([c for c in self.get("contacts") or [] if frappe.utils.cint(c.is_primary)]) > 1:
			frappe.throw(_("Only one contact can be the main contact."))
		for r in self.get("representatives") or []:
			r.rep_name = (r.rep_name or "").strip()
			if r.valid_to and r.valid_from and frappe.utils.getdate(r.valid_to) < frappe.utils.getdate(r.valid_from):
				frappe.throw(_("Representatives row {0}: To is before From.").format(r.idx))
			if r.id_number and format_cnic(r.id_number):
				r.id_number = format_cnic(r.id_number)
			if not any(frappe.utils.cint(r.get(k)) for k in ("can_pay", "can_receive_notices", "can_sign", "can_request_changes")):
				frappe.throw(_("Representatives row {0}: tick at least one thing {1} may do.").format(r.idx, r.rep_name))

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
