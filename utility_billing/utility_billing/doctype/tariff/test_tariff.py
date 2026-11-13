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


class IntegrationTestApprovalPractice(IntegrationTestCase):
	"""Withdraw, approval history and notifications (best-practice review, 2026-11-13)."""

	def setUp(self):
		create_default_utilities()
		make_society()
		make_users()
		self.category = make_category("TST Practice")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_reject_edit_resubmit_approve_keeps_full_history(self):
		"""The user's scenario: rejected for missing charges, maker adds them, resubmits, GM approves."""
		ensure_charge("TST Service Charges", "Fixed")
		ensure_charge("TST Aquifer Charges", "Fixed")
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		reason = "service charges and aquifer charges are missing for the tariff"
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason(t.name, reason)
		frappe.set_user("Administrator")
		act(t, "Reject", GENERAL_MANAGER)

		frappe.set_user(BILLING_MANAGER)
		t.reload()
		t.append("charges", {"charge_type": "TST Service Charges", "rate": 1700})
		t.append("charges", {"charge_type": "TST Aquifer Charges", "rate": 1000})
		t.save()
		frappe.set_user("Administrator")
		act(t, "Submit for Approval", BILLING_MANAGER)
		act(t, "Approve", GENERAL_MANAGER)
		t.reload()

		self.assertEqual((t.docstatus, t.workflow_state), (1, "Approved"))
		self.assertEqual(len(t.charges), 2)
		steps = [(r.action, r.user, r.remarks) for r in t.approval_log]
		self.assertEqual(
			steps,
			[
				("Submit for Approval", BILLING_MANAGER, None),
				("Reject", GENERAL_MANAGER, reason),
				("Resubmit", BILLING_MANAGER, t.approval_log[2].remarks),
				("Approve", GENERAL_MANAGER, None),
			],
		)
		self.assertIn("+ Charges: TST Service Charges, Rate / Amount 1,700", t.approval_log[2].remarks)
		self.assertIn("+ Charges: TST Aquifer Charges, Rate / Amount 1,000", t.approval_log[2].remarks)

	def test_maker_can_withdraw_others_cannot(self):
		t = make_tariff(self.category, "2026-07-01", submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			act(t, "Withdraw", BILLING_MANAGER_2)
		act(t, "Withdraw", BILLING_MANAGER)
		t.reload()
		self.assertEqual(t.workflow_state, "Draft")
		self.assertEqual(t.approval_log[-1].action, "Withdraw")
		frappe.set_user(BILLING_MANAGER)
		t.reload()
		t.flat_rate = 71  # editable again after withdrawing
		t.save()

	def test_notifications_to_approver_and_maker(self):
		t = make_tariff(self.category, "2026-07-01", submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		self.assertTrue(
			frappe.db.exists("Notification Log", {"for_user": GENERAL_MANAGER, "document_name": t.name}),
			"approver not notified",
		)
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason(t.name, "Missing charges")
		frappe.set_user("Administrator")
		act(t, "Reject", GENERAL_MANAGER)
		note = frappe.db.get_value(
			"Notification Log", {"for_user": BILLING_MANAGER, "document_name": t.name}, "subject"
		)
		self.assertIn("rejected", (note or "").lower())
		self.assertIn("Missing charges", note)


def ensure_charge(name, basis):
	if not frappe.db.exists("Charge Type", name):
		frappe.get_doc(
			{"doctype": "Charge Type", "charge_name": name, "charge_group": "Society", "calculation_basis": basis}
		).insert()


class IntegrationTestChangesSinceSubmission(IntegrationTestCase):
	"""What changed since the last submission is shown to the approver (user request, 2026-11-13)."""

	def setUp(self):
		create_default_utilities()
		make_society()
		make_users()
		ensure_charge("TST Service Charges", "Fixed")
		ensure_charge("TST Aquifer Charges", "Fixed")
		self.category = make_category("TST Changes")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def reject(self, t, reason="Please correct the charges"):
		frappe.set_user(GENERAL_MANAGER)
		set_rejection_reason(t.name, reason)
		frappe.set_user("Administrator")
		act(t, "Reject", GENERAL_MANAGER)

	def maker_edit(self, t, change):
		frappe.set_user(BILLING_MANAGER)
		t.reload()
		change(t)
		t.save()
		frappe.set_user("Administrator")

	def test_first_submission_has_no_summary(self):
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		self.assertFalse(t.changes_since_last_submission)

	def test_resubmission_lists_added_rows_and_changed_fields(self):
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		self.reject(t)

		def edit(doc):
			doc.flat_rate = 82
			doc.append("charges", {"charge_type": "TST Service Charges", "rate": 1700})

		self.maker_edit(t, edit)
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		lines = t.changes_since_last_submission.splitlines()
		self.assertIn("~ Rate per Unit: 80 → 82", lines)
		self.assertIn("+ Charges: TST Service Charges, Rate / Amount 1,700", lines)
		self.assertEqual(t.approval_log[-1].remarks, t.changes_since_last_submission)
		note = frappe.db.get_value(
			"Notification Log",
			{"for_user": GENERAL_MANAGER, "document_name": t.name},
			"subject",
			order_by="creation desc",
		)
		self.assertIn("Changes since last submission", note)
		self.assertIn("Rate per Unit: 80 → 82", note)

	def test_changed_and_removed_rows(self):
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		t.append("charges", {"charge_type": "TST Service Charges", "rate": 1700})
		t.append("charges", {"charge_type": "TST Aquifer Charges", "rate": 1000})
		t.save()
		act(t, "Submit for Approval", BILLING_MANAGER)
		self.reject(t)

		def edit(doc):
			doc.charges[0].rate = 1800
			doc.charges = [doc.charges[0]]  # remove aquifer

		self.maker_edit(t, edit)
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		lines = t.changes_since_last_submission.splitlines()
		self.assertIn("~ Charges: TST Service Charges, Rate / Amount 1,700 → 1,800", lines)
		self.assertIn("- Charges: TST Aquifer Charges, Rate / Amount 1,000", lines)

	def test_added_then_removed_cancels_out(self):
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		self.reject(t)
		self.maker_edit(t, lambda d: d.append("charges", {"charge_type": "TST Aquifer Charges", "rate": 1000}))
		self.maker_edit(t, lambda d: setattr(d, "charges", []))
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		self.assertEqual(t.changes_since_last_submission, "No changes since the last submission.")

	def test_no_change_resubmission_says_so(self):
		t = make_tariff(self.category, "2026-07-01", rate=80, submit=False)
		act(t, "Submit for Approval", BILLING_MANAGER)
		self.reject(t)
		act(t, "Submit for Approval", BILLING_MANAGER)
		t.reload()
		self.assertEqual(t.changes_since_last_submission, "No changes since the last submission.")
