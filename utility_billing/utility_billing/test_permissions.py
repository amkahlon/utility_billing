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
	"Reading Exception Code": {"System Manager", "Society Admin", "Billing Manager"},
	"Reading Cycle": {"System Manager", "Billing Officer", "Billing Manager", "Society Admin"},
	# Meter readers enter readings; reviewing them is limited to officers and managers in readings.py.
	"Meter Reading": {"System Manager", "Meter Reader", "Billing Officer", "Billing Manager"},
	"Tariff Adjustment": {"System Manager", "Billing Manager", "General Manager"},
	"Bill Run": {"System Manager", "Billing Officer", "Billing Manager"},
	# Bills are created and posted only by bill runs (ignore_permissions there); nobody edits them.
	"Utility Bill": {"System Manager"},
	# Increment 7: customer accounts and payments
	"Reason Code": {"System Manager", "Society Admin", "Billing Manager"},
	"Fee Type": {"System Manager", "Society Admin", "Billing Manager"},
	"Cash Counter": {"System Manager", "Society Admin", "Billing Manager"},
	"Cashier Day": {"System Manager", "Cashier"},
	# Ledger entries are written only by account.py (and can't be changed or deleted by anyone).
	"Customer Ledger Entry": {"System Manager"},
	"Payment Receipt": {"System Manager", "Cashier", "Accounts Officer"},
	"Fee Charge": {"System Manager", "Cashier", "Billing Officer", "Billing Manager"},
	"Account Adjustment": {"System Manager", "Billing Officer", "Billing Manager", "General Manager"},
	# Increment 8: accounting
	"Ledger Account": {"System Manager", "Accounts Officer", "Society Admin"},
	"Journal Export Layout": {"System Manager", "Accounts Officer", "Society Admin"},
	# Journals and reconciliation records are made only by gl.py (and can't be changed by anyone).
	"GL Journal": {"System Manager"},
	"Receivables Reconciliation": {"System Manager"},
	# Increment 9: the KPI dictionary
	"KPI Definition": {"System Manager", "Society Admin", "Billing Manager"},
}
SUBMITTERS = {
	"Tariff": {"System Manager", "General Manager"},
	"Tariff Adjustment": {"System Manager", "General Manager"},
	"Bill Run": {"System Manager", "Billing Manager"},
	"Utility Bill": set(),  # posted and cancelled only by its bill run, never by a user
	"Payment Receipt": {"System Manager", "Cashier", "Accounts Officer"},  # reversals: account.reverse_document
	"Fee Charge": {"System Manager", "Cashier", "Billing Officer", "Billing Manager"},
	"Account Adjustment": {"System Manager", "General Manager"},
}


class IntegrationTestPermissions(IntegrationTestCase):
	def test_matrix(self):
		for dt, editors in EDITORS.items():
			for p in frappe.get_meta(dt).permissions:
				if p.permlevel:
					continue
				with self.subTest(doctype=dt, role=p.role):
					can_edit = bool(p.write or p.create)
					self.assertEqual(can_edit, p.role in editors, f"{dt}/{p.role} write/create")
					if p.role in READ_ONLY and p.role not in editors:
						self.assertTrue(p.read, f"{dt}/{p.role} should read")
						self.assertFalse(p.delete or p.submit or p.cancel, f"{dt}/{p.role} is read-only")
					self.assertEqual(bool(p.delete), p.role == "System Manager", f"{dt}/{p.role} delete")
					self.assertEqual(bool(p.submit), p.role in SUBMITTERS.get(dt, set()), f"{dt}/{p.role} submit")

	def test_pending_approval_is_read_only_for_business_roles(self):
		"""Found by the user on 2026-11-13: the General Manager could edit a pending tariff in the form
		(the server refused the save). A workflow state's allow_edit only controls the form, so pending
		states must not be editable by any business role."""
		business_roles = READ_ONLY | {"Society Admin", "Billing Manager", "General Manager", "Billing Officer"}
		for wf in frappe.get_all("Workflow", filters={"document_type": ["in", list(EDITORS)]}, pluck="name"):
			for s in frappe.get_doc("Workflow", wf).states:
				if s.state == "Pending Approval":
					with self.subTest(workflow=wf):
						self.assertNotIn(s.allow_edit, business_roles)

	def test_tariff_creation_only_by_billing_manager(self):
		# The approver (General Manager) can submit but not create, keeping maker and checker apart.
		gm = next(p for p in frappe.get_meta("Tariff").permissions if p.role == "General Manager")
		self.assertFalse(gm.create)
		self.assertTrue(gm.submit)
