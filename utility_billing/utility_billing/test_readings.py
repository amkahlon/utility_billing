# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for meter readings (increment 4: FS-RDG-01, 04-11, 15)."""

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing.install import create_default_exception_codes, create_default_utilities
from utility_billing.utility_billing import readings
from utility_billing.utility_billing.testing import BILLING_OFFICER, make_users

SOC = "TST-RDG"
READER = "tst.meter.reader@example.com"


def ensure(doctype, name, data):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **data}).insert()
	return name


class IntegrationTestReadings(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		create_default_exception_codes()
		make_users()
		if not frappe.db.exists("User", READER):
			u = frappe.get_doc({"doctype": "User", "email": READER, "first_name": "Reader", "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Meter Reader")
		ensure("Society", SOC, {"society_name": "Readings Test Society", "short_name": SOC})
		self.cat = ensure("Consumer Category", "Electricity-TST RDG", {"category_name": "TST RDG", "utility": "Electricity"})
		self.consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Reading Consumer"}).insert().name
		self.prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "N 2308"}).insert().name
		self.meter = self.new_meter("TST-RDG-M1")
		self.conn = self.new_connection(self.meter, start=3641, installed="2026-01-01")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ----- helpers -----
	def new_meter(self, serial, digits=5, multiplier=1):
		return frappe.get_doc({"doctype": "Meter", "serial_no": serial, "society": SOC, "utility": "Electricity",
		                       "digits": digits, "multiplier": multiplier}).insert().name

	def new_connection(self, meter, start, installed):
		return frappe.get_doc({
			"doctype": "Connection", "society": SOC, "property": self.prop, "consumer": self.consumer,
			"utility": "Electricity", "consumer_category": self.cat, "status": "Active",
			"meters": [{"meter": meter, "installed_on": installed, "start_reading": start}],
		}).insert().name

	def cycle(self, month):
		name = f"{SOC}-Electricity-{month[:7]}"
		if not frappe.db.exists("Reading Cycle", name):
			frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": month}).insert()
		readings.create_reading_sheet(name)
		return name

	def reading(self, month, conn=None):
		cycle = self.cycle(month)
		return frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": conn or self.conn})

	def read(self, month, present, day="05", conn=None):
		r = self.reading(month, conn)
		r.present_reading = present
		r.reading_date = f"{month[:8]}{day}"
		r.save()
		return r

	# ----- tests -----
	def test_case_1_october_reading(self):
		"""House N 2308, October: 3,641 -> 3,905 = 264 units."""
		r = self.reading("2026-10-01")
		self.assertEqual((r.previous_reading, r.status), (3641, "Pending"))
		r = self.read("2026-10-01", 3905)
		self.assertEqual((r.units, r.status, r.consumption_type), (264, "Valid", "Actual"))
		self.assertEqual(readings.get_billable_units(self.conn, "2026-10-20"), 264)

	def test_cycle_name_month_and_duplicates(self):
		name = self.cycle("2026-10-17")
		self.assertEqual(name, f"{SOC}-Electricity-2026-10")
		self.assertEqual(str(frappe.db.get_value("Reading Cycle", name, "billing_month")), "2026-10-01")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Meter Reading", "reading_cycle": name, "connection": self.conn}).insert()

	def test_previous_reading_comes_from_last_actual(self):
		self.read("2026-09-01", 3800)
		r = self.reading("2026-10-01")
		self.assertEqual(r.previous_reading, 3800)
		self.assertEqual(self.read("2026-10-01", 3905).units, 105)

	def build_history(self, units=(250, 250, 250), start_month=7):
		value = 3641
		for i, u in enumerate(units):
			value += u
			self.read(f"2026-{start_month + i:02d}-01", value)
		return value

	def test_out_of_range_flagged_then_approved_and_kept(self):
		last = self.build_history()
		r = self.read("2026-10-01", last + 1000)
		self.assertEqual(r.status, "Flagged")
		self.assertIn("Above expected range", r.checks)
		self.assertEqual((r.expected_average, r.expected_low, r.expected_high), (250, 125, 375))
		with self.assertRaises(frappe.ValidationError):
			readings.approve_reading(r.name, "ok")  # note too short
		readings.approve_reading(r.name, "AC installed this month, confirmed by phone")
		r.reload()
		self.assertEqual((r.status, r.reviewed_by), ("Approved", "Administrator"))
		r.save()  # an unrelated save keeps the reviewer's decision
		r.reload()
		self.assertEqual(r.status, "Approved")

	def test_lower_reading_corrected_with_reason(self):
		# lower than the previous 3,641 and no roll-over: taken as the same reading, 0 units, for a reviewer's look (increment 50; user,
		# 2026-11-24: "system should not accept present reading less than previous reading ... auto consider reading as same-to-same")
		r = self.read("2026-10-01", 3500)
		self.assertEqual((r.status, r.units, r.raw_units, r.present_reading), ("Flagged", 0, 0, 3641))
		self.assertEqual(r.checks, "Lower than previous reading (3500 entered): taken as the same reading, 0 units")
		r = frappe.get_doc("Meter Reading", r.name)
		r.save()  # opened and saved again with nothing new: the note of what was entered stays
		self.assertEqual((r.status, r.checks), ("Flagged", "Lower than previous reading (3500 entered): taken as the same reading, 0 units"))
		readings.correct_reading(r.name, 3905, "Reader typed 3500 instead of 3905")
		r.reload()
		self.assertEqual((r.status, r.units, r.corrected_from), ("Valid", 264, 3641))
		self.assertEqual(r.correction_reason, "Reader typed 3500 instead of 3905")

	def test_a_same_to_same_reading_is_approved_as_no_use_and_the_next_month_counts_on(self):
		r = self.read("2026-10-01", 3500)
		readings.approve_reading(r.name, "The reader confirms the meter stands at 3641")
		r.reload()
		self.assertEqual((r.status, r.units, r.present_reading), ("Approved", 0, 3641))
		nxt = self.read("2026-11-01", 3700)
		self.assertEqual((nxt.previous_reading, nxt.units), (3641, 59))  # counted from the reading that was kept, not from 3,500

	def test_a_reading_the_dial_cant_show_is_refused(self):
		r = self.reading("2026-10-01")  # a 5-digit dial
		r.present_reading, r.reading_date = 123905, "2026-10-05"
		self.assertRaisesRegex(frappe.ValidationError, "has more digits than the dial of meter TST-RDG-M1 \\(5 digits\\)", r.save)
		# the dial corrected on the meter: its reading in the open cycle is judged by the new digits at once
		m = frappe.get_doc("Meter", self.meter)
		m.digits = 6
		m.save()
		r = self.reading("2026-10-01")
		self.assertEqual(r.digits, 6)
		r.present_reading, r.reading_date = 3905, "2026-10-05"
		r.save()
		self.assertEqual((r.units, r.status), (264, "Valid"))
		self.assertEqual(frappe.get_meta("Meter").get_field("digits").default, "6")  # a new meter's dial has 6 digits unless told

	def test_rollover_is_valid(self):
		meter = self.new_meter("TST-RDG-M2")
		conn = self.new_connection(meter, start=99950, installed="2026-01-01")
		r = self.read("2026-10-01", 123, conn=conn)
		self.assertEqual((r.units, r.status), (173, "Valid"))
		self.assertIn("Meter roll-over", r.checks)

	def test_reread_request_then_new_reading(self):
		r = self.read("2026-10-01", 3641)  # zero use -> flagged
		self.assertEqual(r.status, "Flagged")
		readings.request_reread(r.name, "Check the meter is running")
		r.reload()
		self.assertEqual(r.status, "Re-read Requested")
		r.present_reading = 3905
		r.save()
		self.assertEqual((r.status, r.units), ("Valid", 264))

	def test_estimate_then_true_up(self):
		last = self.build_history()  # Jul-Sep 250 each; last actual = 4391
		cycle = self.cycle("2026-10-01")
		readings.estimate_unread(cycle)
		est = self.reading("2026-10-01")
		self.assertEqual((est.status, est.consumption_type, est.units), ("Estimated", "Estimated", 250))
		self.assertEqual(readings.get_billable_units(self.conn, "2026-10-01"), 250)
		nov = self.reading("2026-11-01")
		self.assertEqual((nov.previous_reading, nov.estimated_since_units), (last, 250))
		nov = self.read("2026-11-01", last + 520)  # 520 used over Oct + Nov
		self.assertEqual((nov.raw_units, nov.units, nov.status), (520, 270, "Valid"))

	def test_exception_codes(self):
		self.build_history()
		r = self.reading("2026-10-01")
		r.exception_code = "No Access"
		r.save()
		self.assertEqual(r.status, "Pending")
		other_meter = self.new_meter("TST-RDG-M3")
		vacant = self.new_connection(other_meter, start=0, installed="2026-01-01")
		v = self.reading("2026-10-01", conn=vacant)
		v.exception_code = "Vacant"
		v.save()
		readings.estimate_unread(self.cycle("2026-10-01"))
		r.reload()
		v.reload()
		self.assertEqual((r.status, r.units), ("Estimated", 250))
		self.assertEqual((v.status, v.units), ("Estimated", 0))
		self.assertIn("billed zero", v.checks)

	def test_meter_changed_since_last_reading(self):
		self.read("2026-09-01", 3800)
		conn = frappe.get_doc("Connection", self.conn)
		new_meter = self.new_meter("TST-RDG-M4")
		conn.meters[0].removed_on = "2026-09-20"
		conn.meters[0].end_reading = 3850
		conn.append("meters", {"meter": new_meter, "installed_on": "2026-09-20", "start_reading": 0})
		conn.save()
		r = self.reading("2026-10-01")
		self.assertEqual((r.meter, r.previous_reading, r.old_meter_units), (new_meter, 0, 50))
		r = self.read("2026-10-01", 40)
		self.assertEqual(r.units, 90)  # 50 on the old meter + 40 on the new one

	def test_close_cycle(self):
		cycle = self.cycle("2026-10-01")
		with self.assertRaises(frappe.ValidationError):
			readings.close_cycle(cycle)  # one reading still pending
		self.read("2026-10-01", 3905)
		readings.close_cycle(cycle)
		c = frappe.get_doc("Reading Cycle", cycle)
		self.assertEqual((c.status, c.total_readings, c.valid_readings, c.total_units), ("Closed", 1, 1, 264))
		with self.assertRaises(frappe.ValidationError):
			readings.create_reading_sheet(cycle)  # a closed cycle takes no new readings
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		r.present_reading = 4000
		with self.assertRaises(frappe.ValidationError):
			r.save()

	def test_upload_readings(self):
		meter = self.new_meter("TST-RDG-M5")
		conn2 = self.new_connection(meter, start=100, installed="2026-01-01")
		cycle = self.cycle("2026-10-01")
		csv = (
			"Reference No,Present Reading,Reading Date,Exception Code\n"
			f"{self.conn},3905,2026-10-05,\n"
			f"{conn2},,2026-10-05,No Access\n"
			"1999999999,100,2026-10-05,\n"
			f"{conn2},,,\n"
		)
		file = frappe.get_doc({"doctype": "File", "file_name": "readings.csv", "content": csv, "is_private": 1}).insert()
		result = readings.upload_readings(cycle, file.file_url)
		self.assertEqual(result["updated"], 2)
		self.assertEqual(sorted(e["row"] for e in result["errors"]), [4, 5])
		# where the cycle stands, for the message's "what to do next": one read, one marked as not read, none flagged or left
		self.assertEqual((result["to_enter"], result["not_read"], result["flagged"]), (0, 1, 0))
		r = self.reading("2026-10-01")
		self.assertEqual((r.units, r.source), (264, "Import"))
		self.assertEqual(self.reading("2026-10-01", conn2).exception_code, "No Access")

	def test_meter_reader_enters_but_cannot_review(self):
		r = self.reading("2026-10-01")
		frappe.set_user(READER)
		r = frappe.get_doc("Meter Reading", r.name)
		r.present_reading = 3500
		r.reading_date = "2026-10-05"
		r.save()
		self.assertEqual(r.status, "Flagged")
		with self.assertRaises(frappe.PermissionError):
			readings.approve_reading(r.name, "Looks fine to me")
		frappe.set_user(BILLING_OFFICER)
		readings.correct_reading(r.name, 3905, "Typing error by the reader")
		frappe.set_user("Administrator")
		r.reload()
		self.assertEqual((r.status, r.units, r.reviewed_by), ("Valid", 264, BILLING_OFFICER))
