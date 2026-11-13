# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing.doctype.tariff.tariff import get_applicable_tariff, set_rejection_reason
from utility_billing.utility_billing.testing import (
	BILLING_MANAGER,
	BILLING_MANAGER_2,
	BILLING_OFFICER,
	BM_AND_GM,
	GENERAL_MANAGER,
	act,
	approve,
	make_users,
)

EXTRA_TEST_RECORD_DEPENDENCIES = []
# Our tests build their own records and users; don't load other apps' test data (ERPNext's User/Company).
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Society", "Consumer Category", "Utility Type", "Charge Type", "User", "Workflow State",
]

SOCIETY = "TST-SOC"


def make_society():
	if not frappe.db.exists("Society", SOCIETY):
		frappe.get_doc(
			{"doctype": "Society", "society_name": "Test Society", "short_name": SOCIETY}
		).insert()


def make_category(name, utility="Electricity"):
	cat = f"{utility}-{name}"
	if not frappe.db.exists("Consumer Category", cat):
		frappe.get_doc({"doctype": "Consumer Category", "category_name": name, "utility": utility}).insert()
	return cat


def make_tariff(category, start, end=None, rate=70, submit=True, **extra):
	doc = frappe.get_doc(
		{
			"doctype": "Tariff",
			"society": SOCIETY,
			"utility": "Electricity",
			"consumer_category": category,
			"effective_from": start,
			"effective_to": end,
			"pricing_method": "Flat rate",
			"flat_rate": rate,
			**extra,
		}
	).insert()
	if submit:
		approve(doc)  # through the Tariff Approval workflow: Billing Manager submits, General Manager approves
	return doc


class IntegrationTestTariff(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		make_society()
		make_users()
		self.category = make_category("TST Residential")

	def tearDown(self):
		frappe.db.rollback()

	def test_title_and_flat_charge(self):
		t = make_tariff(self.category, "2026-07-01")
		self.assertEqual(t.tariff_title, "TST Residential from 01 Jul 2026")  # same on every site
		self.assertEqual(float(t.calculate_energy(264).amount), 18480.0)

	def test_overlap_refused(self):
		make_tariff(self.category, "2026-07-01", "2026-12-31")
		with self.assertRaises(frappe.ValidationError):
			make_tariff(self.category, "2026-10-01", "2027-03-31")

	def test_new_version_closes_open_ended_predecessor(self):
		old = make_tariff(self.category, "2026-07-01", rate=70)
		new = make_tariff(self.category, "2027-01-01", rate=75)
		self.assertEqual(getdate(frappe.db.get_value("Tariff", old.name, "effective_to")), getdate("2026-12-31"))
		self.assertEqual(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-12-31"), old.name)
		self.assertEqual(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2027-01-01"), new.name)
		self.assertIsNone(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-06-30"))

	def test_draft_tariff_not_used(self):
		make_tariff(self.category, "2026-07-01", submit=False)
		self.assertIsNone(get_applicable_tariff(SOCIETY, "Electricity", self.category, "2026-08-01"))

	def test_category_must_match_utility(self):
		water_cat = make_category("TST Domestic", utility="Water")
		with self.assertRaises(frappe.ValidationError):
			make_tariff(water_cat, "2026-07-01")

	def test_slab_tariff_validated(self):
		with self.assertRaises(frappe.ValidationError):
			make_tariff(
				self.category,
				"2026-07-01",
				pricing_method="Progressive slabs",
				slabs=[{"from_units": 10, "to_units": 0, "rate": 5}],
			)


class IntegrationTestTariffApproval(IntegrationTestCase):
	"""Maker-checker approval (FS-CFG-06, FS-ACC-03/04/05)."""

	def setUp(self):
		create_default_utilities()
		make_society()
		make_users()
		self.category = make_category("TST Approval")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def draft(self, owner="Administrator", start="2026-07-01"):
		frappe.set_user(owner)
		try:
			return make_tariff(self.category, start, submit=False)
		finally:
			frappe.set_user("Administrator")

	def test_full_approval_records_people(self):
		t = approve(self.draft())
		self.assertEqual((t.docstatus, t.workflow_state), (1, "Approved"))
		self.assertEqual(t.requested_by, BILLING_MANAGER)
		self.assertEqual(t.approved_by, GENERAL_MANAGER)
		self.assertTrue(t.requested_on and t.approved_on)

	def test_submitter_cannot_approve_even_with_approver_role(self):
		t = self.draft()
		act(t, "Submit for Approval", BM_AND_GM)
		with self.assertRaises(frappe.PermissionError):
			act(t, "Approve", BM_AND_GM)
		t.reload()
		self.assertEqual(t.workflow_state, "Pending Approval")

	def test_creator_cannot_approve(self):
		t = self.draft(owner=BM_AND_GM)
		act(t, "Submit for Approval", BILLING_MANAGER_2)
		# Frappe's own self-approval check (creator) fires before ours; either way it's refused.
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			act(t, "Approve", BM_AND_GM)

	def test_administrator_is_bound_too(self):
		t = self.draft()  # created by Administrator
		act(t, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			act(t, "Approve", "Administrator")

	def test_billing_manager_cannot_approve(self):
		t = self.draft()
		act(t, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			act(t, "Approve", BILLING_MANAGER_2)  # no Approve transition for this role

	def test_pending_tariff_is_locked(self):
		t = self.draft()
		act(t, "Submit for Approval", BILLING_MANAGER)
		frappe.set_user(BILLING_MANAGER)
		t.reload()
		t.flat_rate = 99
		with self.assertRaises(frappe.ValidationError):
			t.save()

	def test_reject_needs_reason_then_resubmit(self):
		t = self.draft()
		act(t, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			act(t, "Reject", GENERAL_MANAGER)  # no reason yet
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason(t.name, "Rate should be 72 per NEPRA notice")
		frappe.set_user("Administrator")
		act(t, "Reject", GENERAL_MANAGER)
		t.reload()
		self.assertEqual((t.workflow_state, t.docstatus), ("Rejected", 0))
		# The maker corrects it and submits again; the reason is cleared for the new round.
		frappe.set_user(BILLING_MANAGER)
		t.reload()
		t.flat_rate = 72
		t.save()
		frappe.set_user("Administrator")
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		self.assertEqual(t.workflow_state, "Pending Approval")
		self.assertFalse(t.rejection_reason)

	def test_direct_submit_blocked(self):
		t = self.draft()
		with self.assertRaises(frappe.ValidationError):
			t.submit()

	def test_officer_cannot_create_tariff(self):
		frappe.set_user(BILLING_OFFICER)
		with self.assertRaises(frappe.PermissionError):
			make_tariff(self.category, "2026-07-01", submit=False)
