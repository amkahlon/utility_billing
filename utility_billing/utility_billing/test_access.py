# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for access and configuration (increment 19; FS-ACC-01, 06, 07, 09, 11, 12, FS-CFG-18, 27, FS-REC-18)."""

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_days, nowdate

from utility_billing.install import create_default_reason_codes
from utility_billing.utility_billing import access, consumer360, mis, security, servicereq, templates
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	AUDITOR, BILLING_MANAGER, BILLING_OFFICER, CASHIER, GENERAL_MANAGER, MANAGEMENT, act,
)


class UnitTestMasking(UnitTestCase):
	def test_cnic_and_phone_are_masked(self):
		self.assertEqual(access.mask("35202-1234567-1"), "3520*-*****67-1")
		self.assertEqual(access.mask("0300-1234567"), "0300-****567")
		self.assertEqual(access.mask("1234567"), "***4567")
		self.assertEqual((access.mask(None), access.mask("12")), (None, "**"))


class IntegrationTestAccess(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()

	def adjustment(self, amount=500, user=BILLING_OFFICER):
		frappe.set_user(user)
		a = frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Debit Note", "connection": self.conn, "bucket": "Arrears",
		                    "amount": amount, "reason_code": "BILL-ERROR", "remarks": "Correction for the test"}).insert()
		frappe.set_user("Administrator")
		act(a, "Submit for Approval", user)
		a.reload()
		return a

	# ---- where users work (ACC-01)
	def test_user_access_becomes_user_permissions(self):
		ua = frappe.get_doc({"doctype": "User Access", "user": CASHIER, "societies": [{"society": SOC}]}).insert()
		self.assertEqual(frappe.get_all("User Permission", filters={"user": CASHIER, "allow": "Society"}, pluck="for_value"), [SOC])
		self.assertEqual(access.societies_of(CASHIER), [SOC])
		ua.all_societies = 1
		ua.save()
		self.assertIsNone(access.societies_of(CASHIER))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "User Access", "user": BILLING_OFFICER}).insert()  # no society chosen

	def test_a_recovery_officer_limited_to_blocks(self):
		frappe.db.set_value("Property", self.prop, "block", "N")
		frappe.get_doc({"doctype": "User Access", "user": BILLING_OFFICER, "societies": [{"society": SOC}],
		                "blocks": [{"society": SOC, "block": "Q"}]}).insert()
		self.assertEqual(access.blocks_of(BILLING_OFFICER, SOC), ["Q"])

	# ---- personal data (ACC-11)
	def test_cnic_and_phone_are_hidden_from_an_auditor(self):
		cons = frappe.db.get_value("Connection", self.conn, "consumer")
		frappe.db.set_value("Consumer", cons, {"mobile_no": "0300-1234567", "id_type": "CNIC", "id_number": "35202-1234567-1"})
		self.assertTrue(access.sees_personal(BILLING_OFFICER))
		self.assertFalse(access.sees_personal(AUDITOR))
		frappe.set_user(AUDITOR)
		try:
			v = consumer360.view(cons)
			rows = consumer360.search("N 2308")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((v["consumer"]["mobile_no"], v["consumer"]["id_number"]), ("0300-****567", "3520*-*****67-1"))
		self.assertEqual(rows[0]["mobile_no"], "0300-****567")
		frappe.set_user(BILLING_OFFICER)
		try:
			self.assertEqual(consumer360.view(cons)["consumer"]["mobile_no"], "0300-1234567")
		finally:
			frappe.set_user("Administrator")

	# ---- approval inbox (ACC-06)
	def test_the_inbox_approves_and_rejects_in_bulk(self):
		a, b, c = self.adjustment(100), self.adjustment(200), self.adjustment(300)
		frappe.set_user(GENERAL_MANAGER)
		try:
			names = {r["name"] for r in access.inbox()}
			self.assertTrue({a.name, b.name, c.name} <= names)
			out = access.bulk(frappe.as_json([{"doctype": "Account Adjustment", "name": a.name}, {"doctype": "Account Adjustment", "name": b.name}]), "Approve")
			self.assertEqual((sorted(out["done"]), out["failed"]), (sorted([a.name, b.name]), []))
			with self.assertRaises(frappe.ValidationError):
				access.bulk(frappe.as_json([{"doctype": "Account Adjustment", "name": c.name}]), "Reject", "")
			access.bulk(frappe.as_json([{"doctype": "Account Adjustment", "name": c.name}]), "Reject", "Wrong amount, please check")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual([frappe.db.get_value("Account Adjustment", x.name, "docstatus") for x in (a, b)], [1, 1])
		self.assertEqual(frappe.db.get_value("Account Adjustment", c.name, "workflow_state"), "Rejected")

	def test_the_maker_does_not_see_their_own_items(self):
		a = self.adjustment(100, user=BILLING_MANAGER)
		frappe.set_user(BILLING_MANAGER)
		try:
			self.assertNotIn(a.name, {r["name"] for r in access.inbox()})
			out = access.bulk(frappe.as_json([{"doctype": "Account Adjustment", "name": a.name}]), "Approve")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((out["done"], len(out["failed"])), ([], 1))  # a refusal is reported, not raised

	# ---- reminders and escalation (ACC-07)
	def test_waiting_items_are_reminded_then_escalated(self):
		a = self.adjustment(100)
		frappe.db.set_value("Account Adjustment", a.name, "requested_on", add_days(nowdate(), -3))
		access.remind_and_escalate()
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": GENERAL_MANAGER, "document_name": a.name, "subject": ["like", "Reminder%"]}))
		frappe.db.set_value("Account Adjustment", a.name, "requested_on", add_days(nowdate(), -6))
		access.remind_and_escalate()
		access.remind_and_escalate()
		self.assertEqual(frappe.db.count("Notification Log", {"for_user": MANAGEMENT, "document_name": a.name, "subject": ["like", "Escalated%"]}), 1)

	# ---- delegation (ACC-07)
	def test_a_delegation_lends_the_approver_roles_for_the_dates(self):
		frappe.set_user(GENERAL_MANAGER)
		try:
			d = frappe.get_doc({"doctype": "Approval Delegation", "delegator": GENERAL_MANAGER, "delegate": BILLING_MANAGER, "from_date": nowdate(),
			                    "to_date": add_days(nowdate(), 3), "reason": "Annual leave"}).insert()
		finally:
			frappe.set_user("Administrator")
		d.reload()
		self.assertEqual(d.status, "Active")
		self.assertIn("General Manager", frappe.get_roles(BILLING_MANAGER))
		access.run_delegations(as_of=add_days(nowdate(), 4))
		d.reload()
		self.assertEqual(d.status, "Ended")
		self.assertNotIn("General Manager", frappe.get_roles(BILLING_MANAGER))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Approval Delegation", "delegator": CASHIER, "delegate": BILLING_MANAGER, "from_date": nowdate(),
			                "to_date": add_days(nowdate(), 3)}).insert()  # a cashier approves nothing

	def test_a_scheduled_delegation_starts_on_its_first_day_and_can_be_cancelled(self):
		d = frappe.get_doc({"doctype": "Approval Delegation", "delegator": GENERAL_MANAGER, "delegate": BILLING_MANAGER,
		                    "from_date": add_days(nowdate(), 2), "to_date": add_days(nowdate(), 5)}).insert()
		self.assertEqual(d.status, "Scheduled")
		access.run_delegations(as_of=add_days(nowdate(), 2))
		d.reload()
		self.assertEqual(d.status, "Active")
		access.cancel_delegation(d.name)
		self.assertEqual(frappe.db.get_value("Approval Delegation", d.name, "status"), "Cancelled")
		self.assertNotIn("General Manager", frappe.get_roles(BILLING_MANAGER))

	# ---- security policy (ACC-09)
	def test_the_security_policy_is_applied_only_by_a_system_manager(self):
		frappe.set_user(GENERAL_MANAGER)
		try:
			self.assertTrue(security.get_status())
			with self.assertRaises(frappe.PermissionError):
				security.apply_policy()
		finally:
			frappe.set_user("Administrator")
		rows = security.apply_policy()
		self.assertTrue(all(r["ok"] for r in rows), rows)

	# ---- reports (ACC-12, REC-18)
	def test_the_rights_report_shows_who_can_do_what(self):
		frappe.set_user(AUDITOR)
		try:
			_c, data, *_x = mis.run("users_rights", {"user": BILLING_OFFICER, "doctype": "Consumer"})
		finally:
			frappe.set_user("Administrator")
		(row,) = data
		self.assertIn("Create", row["rights"])
		self.assertIn("Billing Officer", row["roles"])

	def test_revenue_by_utility_and_charge(self):
		sep = self.september_posted_with_arrears()
		bill = self.bill(sep)
		_c, data, *_x = mis.run("revenue_by_charge", {"society": SOC, "from_date": "2026-10-01", "to_date": "2026-10-31"})
		self.assertEqual(data[-1]["amount"], bill.current_amount)

	# ---- workflow rules and the template (CFG-18, 27)
	def test_a_reconnection_charges_the_societys_fee(self):
		fee = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Reconnection", "amount": 1500, "enabled": 1}).insert().name
		frappe.db.set_value("Society", SOC, "reconnection_fee_type", fee)
		frappe.db.set_value("Connection", self.conn, "status", "Disconnected")
		servicereq.ensure_types(SOC)
		sr = frappe.get_doc({"doctype": "Service Request", "request_type": f"{SOC}-RECON", "connection": self.conn}).insert()
		self.assertEqual(frappe.db.get_value("Fee Charge", sr.fee_charge, ["fee_type", "amount"]), (fee, 1500))

	def test_a_new_society_from_the_template(self):
		out = templates.new_society("Template Test Society", "TPLT")
		soc = frappe.get_doc("Society", out["society"])
		self.assertEqual((soc.energy_charge_type, soc.days_to_due_date), ("Energy Charges", 14))
		self.assertEqual(frappe.db.get_value("Fee Type", soc.reconnection_fee_type, "enabled"), 0)
		self.assertEqual(frappe.db.count("Service Request Type", {"society": soc.name}), len(servicereq.DEFAULT_TYPES))
		self.assertTrue(frappe.db.exists("Consumer Category", "Electricity-Residential"))
		self.assertEqual(len(out["to_do"]), 5)
		frappe.set_user(BILLING_OFFICER)
		try:
			with self.assertRaises(frappe.PermissionError):
				templates.new_society("Not Allowed", "NOPE")
		finally:
			frappe.set_user("Administrator")
