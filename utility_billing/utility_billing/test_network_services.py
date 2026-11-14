# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tests for network master data and service requests (increment 18; FS-MST-23 to 27, FS-WF-00, 09, 20 to 33).
House N 2308 (spec test case 1): the September bill leaves Rs 1,104 unpaid."""

import frappe
from frappe.utils import add_days, nowdate

from utility_billing.utility_billing import account, mis, network, readings, servicereq
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	ACCOUNTS_OFFICER, BILLING_MANAGER, BILLING_OFFICER, CASHIER, GENERAL_MANAGER, RECOVERY_OFFICER, act,
)

USER_OF = {"Billing Officer": BILLING_OFFICER, "Billing Manager": BILLING_MANAGER, "General Manager": GENERAL_MANAGER,
           "Accounts Officer": ACCOUNTS_OFFICER, "Cashier": CASHIER, "Recovery Officer": RECOVERY_OFFICER}


class IntegrationTestNetwork(BillingCase):
	def feeder(self):
		gs = frappe.get_doc({"doctype": "Grid Station", "society": SOC, "station_code": "GS1", "station_name": "Main Grid", "capacity_mva": 20}).insert()
		return frappe.get_doc({"doctype": "Feeder", "society": SOC, "feeder_code": "F1", "feeder_name": "Feeder One", "grid_station": gs.name}).insert().name

	def test_locations_come_in_pairs_and_in_range(self):
		gs = frappe.get_doc({"doctype": "Grid Station", "society": SOC, "station_code": "GS2", "station_name": "North", "latitude": 31.45})
		with self.assertRaises(frappe.ValidationError):
			gs.insert()
		gs.longitude = 274.3
		with self.assertRaises(frappe.ValidationError):
			gs.insert()
		gs.longitude = 74.3
		gs.insert()
		frappe.db.set_value("Property", self.prop, {"latitude": 31.5, "longitude": 74.2})
		kinds = {x["properties"]["kind"] for x in network.geojson(SOC)["features"]}
		self.assertEqual(kinds, {"Grid station", "Property"})

	def test_line_sections_give_the_feeders_estimated_technical_loss(self):
		f = self.feeder()
		for code, km, pct in (("L1", 2, 3), ("L2", 1, 6)):
			frappe.get_doc({"doctype": "Line Section", "society": SOC, "section_code": code, "feeder": f, "length_km": km, "estimated_loss_percent": pct}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Line Section", "society": SOC, "section_code": "L3", "feeder": f, "estimated_loss_percent": 120}).insert()
		frappe.db.set_value("Connection", self.conn, "feeder", f)
		self.september_posted_with_arrears()
		_c, data, *_x = mis.run("line_losses", {"society": SOC, "month": "2026-09-01", "group_by": "Feeder"})
		row = next(r for r in data if r["name"] == "Feeder One")
		self.assertEqual(row["technical_pct"], 4.0)  # (2 × 3 + 1 × 6) ÷ 3 km

	def test_a_common_area_cost_is_shared_by_its_method(self):
		other = self.common_account()
		self.september_two_accounts(other)
		frappe.get_doc({"doctype": "Common Area Facility", "society": SOC, "facility_name": "Tube well", "facility_type": "Water pump",
		                "connection": other, "allocation_method": network.EQUAL}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Common Area Facility", "society": SOC, "facility_name": "Lights", "facility_type": "Streetlights",
			                "connection": other, "allocation_method": network.SOCIETY}).insert()  # one meter, one facility
		(row,) = network.allocation(SOC, "2026-09-01")
		cost = frappe.db.get_value("Utility Bill", {"connection": other, "billing_month": "2026-09-01"}, "current_amount")
		self.assertEqual((row["amount"], row["accounts"], row["per_account"]), (cost, 1, cost))  # shared by the one other account
		_c, data, *_x = mis.run("common_area_allocation", {"society": SOC, "month": "2026-09-01"})
		self.assertEqual(data[0]["facility_name"], "Tube well")

	def common_account(self):
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "PUMP-1"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-PUMP", "society": SOC, "utility": "Electricity"}).insert().name
		cons = frappe.db.get_value("Connection", self.conn, "consumer")
		return frappe.get_doc({"doctype": "Connection", "society": SOC, "property": prop, "consumer": cons, "utility": "Electricity",
		                       "consumer_category": self.cat, "status": "Active",
		                       "meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 0}]}).insert().name

	def september_two_accounts(self, other):
		for month, mine, theirs in (("2026-08-01", 2830, 300), ("2026-09-01", 3641, 700)):
			cycle = self.month_reading(month, mine, close=False)
			r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": other})
			r.present_reading, r.reading_date = theirs, f"{month[:8]}05"
			r.save()
			if r.status == "Flagged":
				readings.approve_reading(r.name, "Confirmed")
			readings.close_cycle(cycle)
		self.fpa("2026-09-01", 0)
		return self.post(self.make_run("2026-09-01", "2026-10-01"))

	def test_assets_keep_their_maintenance_and_warranty(self):
		a = frappe.get_doc({"doctype": "Network Asset", "society": SOC, "asset_name": "Pump motor", "asset_type": "Pump", "purchase_date": "2026-01-10",
		                    "installation_date": "2026-01-05"})
		with self.assertRaises(frappe.ValidationError):
			a.insert()  # installed before it was bought
		a.installation_date, a.warranty_until = "2026-02-01", "2027-02-01"
		a.append("maintenance", {"maintenance_date": "2026-06-01", "kind": "Preventive", "next_due": "2026-09-01"})
		a.insert()
		self.assertEqual(str(a.next_maintenance), "2026-09-01")
		_c, data, msg, *_x = mis.run("asset_register", {"society": SOC})
		self.assertIn("Overdue", data[0]["flag"])
		self.assertIn("GeoJSON", msg)


class IntegrationTestServiceRequests(BillingCase):
	def setUp(self):
		super().setUp()
		servicereq.ensure_types(SOC)
		self.consumer = frappe.db.get_value("Connection", self.conn, "consumer")

	def request(self, code, user=BILLING_OFFICER, **kw):
		frappe.set_user(user)
		try:
			return frappe.get_doc({"doctype": "Service Request", "request_type": f"{SOC}-{code}", "connection": self.conn, **kw}).insert()
		finally:
			frappe.set_user("Administrator")

	def step(self, sr, user=None, **kw):
		sr.reload()
		s = next(x for x in sr.steps if x.idx == sr.current_step)
		frappe.set_user(user or USER_OF[s.role])
		try:
			return servicereq.advance(sr.name, s.idx, **kw)
		finally:
			frappe.set_user("Administrator")
			sr.reload()

	def finish(self, sr):
		while sr.status in ("Open", "In Progress"):
			self.step(sr)
		return sr

	def attach_all(self, sr):
		for d in sr.documents:
			frappe.db.set_value("Service Request Document", d.name, "file", "/files/scan.pdf")
		sr.reload()

	def test_the_standard_request_types_are_set_up_once(self):
		self.assertEqual(servicereq.ensure_types(SOC), 0)
		self.assertEqual(frappe.db.count("Service Request Type", {"society": SOC}), len(servicereq.DEFAULT_TYPES))

	def test_a_new_connection_from_application_to_first_bill(self):
		fee = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Connection fee", "amount": 3000, "enabled": 1}).insert().name
		frappe.db.set_value("Service Request Type", f"{SOC}-NEW", {"fee_type": fee, "deposit_amount": 5000})
		frappe.clear_document_cache("Service Request Type", f"{SOC}-NEW")
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2400"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-NEW", "society": SOC, "utility": "Electricity"}).insert().name
		sr = self.request("NEW", connection=None, property=prop, consumer=self.consumer, utility="Electricity", new_category=self.cat, new_load_kw=3)
		conn = sr.connection
		self.assertEqual(frappe.db.get_value("Connection", conn, "status"), "Applied")
		self.assertEqual((account.balance(conn), frappe.db.get_value("Security Deposit", sr.security_deposit, "docstatus")), (3000, 0))
		self.assertEqual([s.step_kind for s in sr.steps], ["Office", "Document check", "Field", "Approval", "Payment", "Field"])
		self.step(sr)
		with self.assertRaises(frappe.ValidationError):
			self.step(sr)  # documents missing
		self.attach_all(sr)
		self.step(sr)
		self.step(sr, latitude=31.45, longitude=74.27, remarks="Transformer T1 has room")
		with self.assertRaises(frappe.PermissionError):
			self.step(sr, user=BILLING_OFFICER)  # an approval by the Billing Manager
		self.step(sr)
		with self.assertRaises(frappe.ValidationError):
			self.step(sr)  # the demand isn't paid
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": conn, "payment_channel": "Bank / Channel", "payment_date": nowdate(),
		                    "amount": 3000, "mode": "Bank Transfer", "channel_txn_id": "SR-FEE"}).insert()
		r.submit()
		dep = frappe.get_doc("Security Deposit", sr.security_deposit)
		act(dep, "Submit for Approval", ACCOUNTS_OFFICER)
		act(dep, "Approve", GENERAL_MANAGER)
		self.step(sr)
		sr.new_meter = meter
		sr.save()
		self.step(sr, reading=15)
		c = frappe.get_doc("Connection", conn)
		self.assertEqual((sr.status, c.status, c.current_meter, c.meters[0].start_reading, c.security_deposit), ("Completed", "Active", meter, 15, 5000))
		self.assertIn("active", sr.result_note)

	def test_a_rejected_request_takes_its_demand_back(self):
		fee = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Connection fee", "amount": 3000, "enabled": 1}).insert().name
		frappe.db.set_value("Service Request Type", f"{SOC}-NEW", {"fee_type": fee, "deposit_amount": 5000})
		frappe.clear_document_cache("Service Request Type", f"{SOC}-NEW")
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2401"}).insert().name
		sr = self.request("NEW", connection=None, property=prop, consumer=self.consumer, utility="Electricity", new_category=self.cat)
		deposit = sr.security_deposit
		self.step(sr)
		self.attach_all(sr)
		self.step(sr)
		self.step(sr)
		with self.assertRaises(frappe.ValidationError):
			self.step(sr, reject=1, remarks="")  # a reason is needed
		self.step(sr, reject=1, remarks="No capacity on the transformer")
		self.assertEqual(sr.status, "Rejected")
		self.assertEqual((account.balance(sr.connection), frappe.db.get_value("Connection", sr.connection, "status")), (0, "Closed"))
		self.assertFalse(frappe.db.exists("Security Deposit", deposit))

	def test_disconnection_and_reconnection(self):
		self.september_posted_with_arrears()  # owes 1,104
		sr = self.request("DISC", disconnection_reason="Defaulter", effective_date="2026-11-03")
		self.finish(sr)
		c = frappe.get_doc("Connection", self.conn)
		self.assertEqual((c.status, str(c.disconnected_on)), ("Disconnected", "2026-11-03"))
		with self.assertRaises(frappe.ValidationError):
			self.request("DISC", disconnection_reason="Safety")  # not active any more
		rc = self.request("RECON")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.step(rc)
		self.assertIn("installment plan", str(cm.exception))
		self.pay(1104, nowdate())
		self.finish(rc)
		c.reload()
		self.assertEqual((c.status, c.disconnected_on), ("Active", None))

	def test_a_meter_change_closes_the_old_meter_and_opens_the_new(self):
		new = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M9", "society": SOC, "utility": "Electricity"}).insert().name
		sr = self.request("METER", new_meter=new, old_meter_reading=2500, new_meter_reading=0, seal_no="S-99", meter_reason="Burnt", effective_date="2026-11-04")
		self.finish(sr)
		rows = frappe.get_doc("Connection", self.conn).meters
		self.assertEqual([(r.meter, str(r.removed_on) if r.removed_on else None, r.end_reading) for r in rows],
		                 [("TST-BIL-M1", "2026-11-04", 2500), (new, None, 0)])
		self.assertEqual(frappe.db.get_value("Meter", new, ["status", "seal_no"]), ("Installed", "S-99"))

	def test_category_and_load_change(self):
		cat2 = frappe.get_doc({"doctype": "Consumer Category", "category_name": "TST Commercial", "utility": "Electricity"}).insert().name
		sr = self.request("CAT", new_category=cat2, effective_date="2026-11-01")
		self.attach_all(sr)
		self.finish(sr)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "consumer_category"), cat2)
		ld = self.request("LOAD", new_load_kw=7.5)
		self.finish(ld)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "sanctioned_load_kw"), 7.5)

	def test_a_change_of_owner_needs_no_dues_and_keeps_the_history(self):
		self.september_posted_with_arrears()
		buyer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "New Owner"}).insert().name
		sr = self.request("OWNER", new_consumer=buyer, effective_date="2026-11-05")
		self.attach_all(sr)
		self.step(sr)
		with self.assertRaises(frappe.ValidationError) as cm:
			self.step(sr)
		self.assertIn("owes", str(cm.exception))
		self.pay(1104, nowdate())
		self.step(sr)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "consumer"), buyer)
		billed = [r.consumer for r in frappe.get_doc("Property", self.prop).occupants if r.is_billed and not r.to_date]
		self.assertEqual(billed, [buyer])
		self.assertEqual(frappe.db.count("Customer Ledger Entry", {"connection": self.conn}) > 0, True)  # the account keeps its history

	def test_a_tenant_moves_in_and_leaves(self):
		tenant = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Tenant"}).insert().name
		sr = self.request("TENANT-IN", new_consumer=tenant, effective_date="2026-11-01")
		self.attach_all(sr)
		self.finish(sr)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "consumer"), tenant)
		out = self.request("TENANT-OUT", new_consumer=self.consumer, effective_date="2026-11-20")
		self.finish(out)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "consumer"), self.consumer)

	def test_a_no_dues_certificate_can_be_checked_by_anyone(self):
		self.september_posted_with_arrears()
		sr = self.request("NODUES")
		with self.assertRaises(frappe.ValidationError):
			self.step(sr)
		self.pay(1104, nowdate())
		self.finish(sr)
		self.assertTrue(sr.certificate_no.startswith("NDC-"))
		frappe.set_user("Guest")
		ok = servicereq.verify_certificate(sr.verification_code)
		frappe.set_user("Administrator")
		self.assertEqual((ok["valid"], ok["certificate_no"], ok["account"]), (True, sr.certificate_no, self.conn))
		self.assertEqual(servicereq.verify_certificate("NOPE1234"), {"valid": False})

	def test_contact_details_are_updated(self):
		self.finish(self.request("CONTACT", new_mobile="0300-7654321", new_email="new@example.com"))
		self.assertEqual(frappe.db.get_value("Consumer", self.consumer, ["mobile_no", "email"]), ("0300-7654321", "new@example.com"))

	def test_a_fast_meter_gives_a_credit_note_for_approval(self):
		sep = self.september_posted_with_arrears()
		sr = self.request("TEST", test_error_percent=5, months_to_revise=1)
		self.finish(sr)
		energy = frappe.db.get_value("Bill Line", {"parent": self.bill(sep).name, "charge_type": "TST Energy"}, "amount")
		adj = frappe.get_doc("Account Adjustment", sr.adjustment)
		self.assertEqual((adj.note_type, adj.docstatus, adj.amount), ("Credit Note", 0, round(energy * 5 / 105, 2)))

	def test_a_detection_bill_is_assessed_and_approved_by_a_general_manager(self):
		sr = self.request("DETECT", assessed_load_kw=5, hours_per_day=8, assessed_days=30, rate_per_unit=50)
		self.assertEqual((sr.assessed_units, sr.assessed_amount), (1200, 60000))
		self.attach_all(sr)
		self.step(sr, reading=2400)
		with self.assertRaises(frappe.PermissionError):
			self.step(sr, user=BILLING_MANAGER)
		self.step(sr)
		self.assertEqual(frappe.db.get_value("Account Adjustment", sr.adjustment, ["note_type", "amount"]), ("Debit Note", 60000))

	def test_closing_an_account_offsets_its_deposit(self):
		self.september_posted_with_arrears()
		d = frappe.get_doc({"doctype": "Security Deposit", "connection": self.conn, "deposit_type": "Received", "amount": 5000, "mode": "Cash",
		                    "posting_date": nowdate()}).insert()
		act(d, "Submit for Approval", ACCOUNTS_OFFICER)
		act(d, "Approve", GENERAL_MANAGER)
		sr = self.request("CLOSE", effective_date=nowdate())
		self.attach_all(sr)
		self.finish(sr)
		self.assertEqual(frappe.db.get_value("Connection", self.conn, "status"), "Closed")
		offset = frappe.get_doc("Security Deposit", sr.security_deposit)
		self.assertEqual((offset.deposit_type, offset.amount, offset.docstatus), ("Offset Against Arrears", 1104, 0))

	def test_steps_are_done_in_order_and_requests_are_not_edited_by_hand(self):
		sr = self.request("DISC", disconnection_reason="Safety")
		frappe.set_user(BILLING_MANAGER)
		with self.assertRaises(frappe.ValidationError):
			servicereq.advance(sr.name, 2)
		frappe.set_user("Administrator")
		sr.status = "Completed"
		with self.assertRaises(frappe.ValidationError):
			sr.save()
		with self.assertRaises(frappe.ValidationError):
			self.request("DISC", disconnection_reason="Safety")  # one open per account and kind
		frappe.set_user(BILLING_OFFICER)
		servicereq.cancel(sr.name, "Raised by mistake")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Service Request", sr.name, "status"), "Cancelled")

	def test_the_service_standards_report(self):
		sr = self.request("CONTACT", new_mobile="0300-1111111", requested_on=add_days(nowdate(), -5))
		self.finish(sr)
		self.request("DISC", disconnection_reason="Safety", requested_on=add_days(nowdate(), -9))
		_c, data, *_x = mis.run("service_requests", {"society": SOC, "from_date": add_days(nowdate(), -30), "to_date": nowdate()})
		total = data[-1]
		self.assertEqual((total["received"], total["completed"], total["open"], total["late"]), (2, 1, 1, 1))
