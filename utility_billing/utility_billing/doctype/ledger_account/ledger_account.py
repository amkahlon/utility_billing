# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Ledger Account: one account of the society's chart of accounts, by the code used in its
accounting software (FS-GL-01). Charge types, fees, payment modes and society settings point to these."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


class LedgerAccount(Document):
	def before_insert(self):
		self.account_code = (self.account_code or "").strip()

	def validate(self):
		if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-/ ]{0,29}", self.account_code or ""):
			frappe.throw(_("Account Code: up to 30 letters, digits, dots, hyphens, slashes or spaces."))
