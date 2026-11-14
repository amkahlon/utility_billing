# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for reading routes, connection flags, security deposits, representatives and the Consumer 360 view (increment 17;
FS-MST-09, 10, 12, 13, 14, 15, 17, 18). House N 2308 (spec test case 1): the September bill leaves Rs 1,104 unpaid."""

import frappe

from utility_billing.utility_billing import account, consumer360, flags, mis, readings, recovery
from utility_billing.utility_billing.doctype.security_deposit.security_deposit import held
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	ACCOUNTS_OFFICER, BILLING_MANAGER, BILLING_OFFICER, CASHIER, GENERAL_MANAGER, act,
)


class IntegrationTestMasterData(BillingCase):
	def setUp(self):
		super().setUp()
		flags.create_default_flags()
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")

	def second_connection(self):
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2309"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M2", "society": SOC, "utility": "Electricity"}).insert().name
		return frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": self.consumer, "utility": "Electricity",
		                       "consumer_category": self.cat, "status": "Active",
		                       "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 100}]}).insert().name

	def disconnected_second_connection(self):
		"""A second account of the same consumer that isn't read (disconnected), so the monthly cycles close as before."""
		name = self.second_connection()
		frappe.db.set_value("Connection", name, "status", "Disconnected")
		return name

	def route(self, code, stops, **kw):
		return frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": code,
		                       "stops": [{"sequence": i + 1, "connection": c} for i, c in enumerate(stops)], **kw}).insert()

	def flag(self, code, from_date="2026-01-01", to_date=None):
		c = frappe.get_doc("Connection", self.conn)
		c.append("connection_flags", {"flag": code, "from_date": from_date, "to_date": to_date, "remarks": "test"})
		c.save()

	def deposit(self, kind, amount, approve=True, **kw):
		frappe.set_user(ACCOUNTS_OFFICER)
		d = frappe.get_doc({"doctype": "Security Deposit", "connection": self.conn, "deposit_type": kind, "amount": amount,
		                    "mode": None if kind == "Offset Against Arrears" else "Cash", "posting_date": "2026-11-02", **kw}).insert()
		frappe.set_user("Administrator")
		if approve:
			act(d, "Submit for Approval", ACCOUNTS_OFFICER)
			act(d, "Approve", GENERAL_MANAGER)
			d.reload()
		return d

	# ---- reading routes (MST-09)
	def test_a_route_orders_the_sheet_and_names_the_reader(self):
		other = self.second_connection()
		r = self.route("R1", [other, self.conn])
		self.assertEqual((r.name, r.stops_count), (f"{SOC}-Electricity-R1", 2))
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "route"), "R1")
		cycle = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-08-01",
		                        "route_assignments": [{"route": r.name, "reader": BILLING_OFFICER}]}).insert()
		readings.create_reading_sheet(cycle.name)
		rows = frappe.get_all("Meter Reading", filters={"reading_cycle": cycle.name}, fields=["connection", "route", "route_sequence", "assigned_reader"],
		                      order_by="creation, name")
		self.assertEqual([(x.connection, x.route, x.route_sequence, x.assigned_reader) for x in rows],
		                 [(other, "R1", 1, BILLING_OFFICER), (self.conn, "R1", 2, BILLING_OFFICER)])

	def test_an_account_is_on_one_route_and_belongs_to_the_society(self):
		self.route("R1", [self.conn])
		with self.assertRaises(frappe.ValidationError) as cm:
			self.route("R2", [self.conn])
		self.assertIn("already on route", str(cm.exception))
		with self.assertRaises(frappe.ValidationError):
			self.route("R3", [self.conn, self.conn])
		self.route("R4", [self.conn], enabled=0)  # a route not in use may list it

	def test_taking_an_account_off_a_route_clears_its_route(self):
		r = self.route("R1", [self.conn])
		r.stops = []
		r.save()
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "route"), "")

	# ---- flags (MST-14)
	def test_a_bill_on_hold_is_left_out_of_the_run(self):
		self.flag("BILL-HOLD", "2026-10-01")
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		self.assertEqual(run.bill_count, 0)
		self.assertIn("Bill on hold", run.problems[0].problem)

	def test_a_flag_that_has_ended_changes_nothing(self):
		self.flag("BILL-HOLD", "2026-01-01", "2026-08-31")
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		self.assertEqual(self.make_run("2026-10-01", "2026-11-01").bill_count, 1)

	def test_an_exempt_account_gets_no_surcharge(self):
		self.flag("EXEMPT")
		sep = self.september_posted_with_arrears()
		account.apply_surcharges(as_of="2026-11-10", society=SOC)
		self.assertEqual(self.bill(sep).surcharge_status, "Not charged (flagged)")

	def test_a_flag_holds_recovery(self):
		self.september_posted_with_arrears()
		self.flag("DISPUTE")
		row = next(r for r in recovery.accounts(SOC, include_all=True) if r["connection"] == self.conn)
		self.assertEqual((row["hold"], row["next_stage"]), ("Flag: Under dispute", None))

	# ---- security deposits (MST-10)
	def test_a_deposit_is_received_offset_and_refunded(self):
		self.september_posted_with_arrears()  # owes 1,104
		d = self.deposit("Received", 5_000)
		self.assertEqual((d.docstatus, d.balance_before, d.balance_after), (1, 0, 5_000))
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "security_deposit"), 5_000)
		with self.assertRaises(frappe.ValidationError) as cm:
			self.deposit("Refunded", 1_000, approve=False)
		self.assertIn("Offset the deposit", str(cm.exception))
		with self.assertRaises(frappe.ValidationError):
			self.deposit("Offset Against Arrears", 2_000, approve=False)  # more than is owed
		off = self.deposit("Offset Against Arrears", 1_104)
		entry = frappe.get_doc("Customer Ledger Entry", off.ledger_entry)
		self.assertEqual((entry.entry_type, entry.amount), ("Deposit offset", -1_104))
		self.assertEqual(account.balance(self.conn), 0)
		with self.assertRaises(frappe.ValidationError):
			self.deposit("Refunded", 4_000, approve=False)  # more than is held
		self.deposit("Refunded", 1_000)
		self.assertEqual((held(self.conn), frappe.db.get_value("Connection", self.conn, "security_deposit")), (2_896, 2_896))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Security Deposit", d.name).cancel()  # part of it was used
		frappe.get_doc("Security Deposit", off.name).cancel()
		self.assertEqual((account.balance(self.conn), held(self.conn)), (1_104, 4_000))

	def test_a_deposit_is_approved_at_its_level(self):
		d = self.deposit("Received", 5_000, approve=False)
		act(d, "Submit for Approval", ACCOUNTS_OFFICER)
		with self.assertRaises(Exception):
			act(d, "Approve", BILLING_MANAGER)  # default level: a General Manager
		act(d, "Approve", GENERAL_MANAGER)

	def test_the_deposit_report_shows_what_is_held(self):
		self.deposit("Received", 5_000)
		_c, data, *_x = mis.run("deposits_held", {"society": SOC})
		self.assertEqual((data[0]["connection"], data[0]["held"], data[-1]["held"]), (self.conn, 5_000, 5_000))

	# ---- customers (MST-17, 18)
	def test_representatives_need_a_right_and_one_main_contact(self):
		c = frappe.get_doc("Consumer", self.consumer)
		c.append("representatives", {"rep_name": "Agent", "can_pay": 0})
		with self.assertRaises(frappe.ValidationError):
			c.save()
		c.representatives[0].can_pay = 1
		c.representatives[0].id_number = "3520212345671"
		c.append("contacts", {"contact_name": "A", "is_primary": 1})
		c.append("contacts", {"contact_name": "B", "is_primary": 1})
		with self.assertRaises(frappe.ValidationError):
			c.save()
		c = frappe.get_doc("Consumer", self.consumer)
		c.append("representatives", {"rep_name": "Agent", "can_pay": 1, "id_number": "3520212345671"})
		c.append("contacts", {"contact_name": "A", "is_primary": 1})
		c.append("contacts", {"contact_name": "B"})
		c.save()
		self.assertEqual(c.representatives[0].id_number, "35202-1234567-1")

	# ---- Consumer 360 and the combined statement (MST-12, 13, 15)
	def test_the_consumer_view_brings_everything_together(self):
		other = self.disconnected_second_connection()
		self.september_posted_with_arrears()
		self.flag("STAFF")
		self.deposit("Received", 2_000)
		self.assertIn(self.consumer, [r.name for r in consumer360.search("N 2308")])
		self.assertIn(self.consumer, [r.name for r in consumer360.search("TST-BIL-M1")])
		v = consumer360.view(self.consumer)
		by = {a["name"]: a for a in v["accounts"]}
		self.assertEqual(set(by), {self.conn, other})
		self.assertEqual((by[self.conn]["owes"], by[self.conn]["flags"], by[self.conn]["security_deposit"]), (1_104, ["Staff"], 2_000))
		self.assertEqual((v["total_owed"], v["total_deposit"]), (1_104, 2_000))
		self.assertEqual(len(v["bills"]), 1)
		self.assertTrue(v["payments"] and v["ledger"] and v["readings"])

	def test_the_combined_statement_covers_every_account(self):
		self.disconnected_second_connection()
		self.september_posted_with_arrears()
		_c, data, _m, _ch, summary = mis.run("consumer_statement", {"consumer": self.consumer})
		self.assertEqual({r["connection"] for r in data}, {self.conn})
		self.assertEqual(summary[1]["value"], 1_104)
		_c, data, *_x = mis.run("consumer_statement", {"consumer": self.consumer, "from_date": "2026-10-05"})
		self.assertEqual(data[0]["entry"], "Opening balance")

	def test_the_consumer_view_is_for_staff(self):
		frappe.set_user(CASHIER)
		consumer360.search("N 2308")  # a cashier may look people up
		frappe.set_user("Administrator")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			consumer360.view(self.consumer)
		frappe.set_user("Administrator")
