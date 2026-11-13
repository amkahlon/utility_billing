# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Permission matrix (FS-ACC-02, 03). Guards against a real defect found in QA on 2026-11-13:
Frappe's permission rows default write and create to on, which gave read-only roles edit rights."""

import frappe
from frappe.tests import IntegrationTestCase

READ_ONLY = {"Cashier", "Recovery Officer", "Meter Reader", "Accounts Officer", "Management", "Billing Auditor"}
MASTERS = ["Consumer", "Property", "Connection", "Meter", "Feeder", "Transformer"]

# Which roles may create/write each DocType. Everyone else listed may only read.
EDITORS = {
	"Society": {"System Manager", "Society Admin"},
	"Utility Type": {"System Manager", "Society Admin"},
	"Consumer Category": {"System Manager", "Society Admin", "Billing Manager"},
	"Charge Type": {"System Manager", "Society Admin", "Billing Manager"},
	"Tariff": {"System Manager", "Billing Manager", "General Manager"},
	**{dt: {"System Manager", "Billing Officer", "Billing Manager", "Society Admin"} for dt in MASTERS},
}
SUBMITTERS = {"Tariff": {"System Manager", "General Manager"}}


class IntegrationTestPermissions(IntegrationTestCase):
	def test_matrix(self):
		for dt, editors in EDITORS.items():
			for p in frappe.get_meta(dt).permissions:
				if p.permlevel:
					continue
				with self.subTest(doctype=dt, role=p.role):
					can_edit = bool(p.write or p.create)
					self.assertEqual(can_edit, p.role in editors, f"{dt}/{p.role} write/create")
					if p.role in READ_ONLY:
						self.assertTrue(p.read, f"{dt}/{p.role} should read")
						self.assertFalse(p.delete or p.submit or p.cancel, f"{dt}/{p.role} is read-only")
					self.assertEqual(bool(p.delete), p.role == "System Manager", f"{dt}/{p.role} delete")
					self.assertEqual(bool(p.submit), p.role in SUBMITTERS.get(dt, set()), f"{dt}/{p.role} submit")

	def test_tariff_creation_only_by_billing_manager(self):
		# The approver (General Manager) can submit but not create, keeping maker and checker apart.
		gm = next(p for p in frappe.get_meta("Tariff").permissions if p.role == "General Manager")
		self.assertFalse(gm.create)
		self.assertTrue(gm.submit)
