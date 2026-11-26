# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for bill print (increment 6): FS-BIL-08, 16, 17; FS-RUN-08, 09; FS-CLT-03; FS-CFG-19."""

import io

import frappe
from frappe.utils import formatdate

from utility_billing.utility_billing import bill_print, readings
from utility_billing.utility_billing.test_billing import SOC, BillingCase


class IntegrationTestBillPrint(BillingCase):
	def setUp(self):
		super().setUp()
		soc = frappe.get_doc("Society", SOC)
		soc.update({"society_name": "Billing Test Society", "bill_history_months": 12, "print_office_copy": 1,
		            "show_qr_code": 1, "bill_notices": "Pay on time.\nReport faults on 042-111.",
		            "payment_instructions": "Pay at any MBL branch.", "address": "Lahore"})
		soc.set("bank_accounts", [{"bank_name": "MBL", "account_title": "Test", "account_number": "123",
		                           "iban": "PK90MUCB0000000987654321", "biller_channel": "1Link", "biller_code": "99999",
		                           "print_on_bill": 1},
		                          {"bank_name": "HBL", "account_title": "Hidden", "account_number": "999", "print_on_bill": 0}])
		soc.save()
		frappe.db.set_value("Property", self.prop, "block", "N Block")

	def october_posted(self):
		"""Spec test case 1: October bill (22,260 / 23,364 / 25,145) posted after September."""
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		return run, self.bill(run)

	def test_case_1_bill_prints_every_required_part(self):
		run, bill = self.october_posted()
		html = bill_print.bill_html(bill.name)
		ref = frappe.db.get_value("Connection", self.conn, "reference_no")
		for text in (
			"Billing Test Society", "Electricity Bill", "October 2026", bill.name, ref, "House N 2308 Owner", "N 2308, N Block",
			"Energy Charges", "18,480", "FPA Charges", "August 2026 units 831", "1,080", "Service Charges", "1,700",
			"22,260", "1,104", "23,364", "1,781", "25,145", formatdate("2026-11-15"),  # FS-BIL-08 amounts and due date
			"Consumer copy", "Office copy", "Bank / cashier stamp",  # both copies
			"Sep 2026", "811",  # 12-month history
			"Pay on time.", "Report faults on 042-111.", "Pay at any MBL branch.", "PK90MUCB0000000987654321", "99999",
			"data:image/png;base64,",  # QR code of the reference number
			"TST-BIL-M1", "3,905",  # meter and present reading
		):
			self.assertIn(text, html, text)
		self.assertNotIn("Hidden", html)  # bank accounts not marked Print on Bill
		self.assertNotIn("DUPLICATE", html)
		self.assertNotIn("DRAFT", html)

	def test_reprint_is_exact_after_society_and_consumer_change(self):
		run, bill = self.october_posted()
		before = bill_print.bill_html(bill.name)
		soc = frappe.get_doc("Society", SOC)
		soc.update({"society_name": "Renamed Society", "bill_notices": "New notice", "print_office_copy": 0})
		soc.bank_accounts[0].iban = "PK00CHANGED"
		soc.save()
		frappe.db.set_value("Consumer", frappe.db.get_value("Connection", self.conn, "consumer"), "consumer_name", "New Owner")
		frappe.db.set_value("Property", self.prop, "block", "Z Block")
		self.assertEqual(bill_print.bill_html(bill.name), before)  # FS-RUN-09

	def test_draft_bill_is_marked_and_cannot_have_duplicate(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		bill = self.bill(self.make_run("2026-10-01", "2026-11-01"))
		self.assertIn("DRAFT", bill_print.bill_html(bill.name))
		self.assertNotIn("DUPLICATE", bill_print.bill_html(bill.name, duplicate=1))
		with self.assertRaises(frappe.ValidationError):
			bill_print.issue_duplicate(bill.name)

	def test_duplicate_is_issued_recorded_and_marked(self):
		run, bill = self.october_posted()
		with self.assertRaises(frappe.ValidationError):  # must be issued (and recorded) first
			bill_print.download_bill(bill.name, duplicate=1)
		self.assertEqual(bill_print.issue_duplicate(bill.name), 1)
		self.assertEqual(bill_print.issue_duplicate(bill.name), 2)
		bill.reload()
		self.assertEqual(bill.duplicate_count, 2)
		self.assertTrue(bill.last_duplicate_on)
		self.assertEqual(frappe.db.count("Comment", {"reference_name": bill.name, "comment_type": "Info",
		                                             "content": ["like", "Duplicate bill%"]}), 2)
		html = bill_print.bill_html(bill.name, duplicate=2)
		self.assertIn("DUPLICATE 2", html)
		self.assertNotIn("DUPLICATE", bill_print.bill_html(bill.name))  # a plain reprint isn't marked
		bill_print.download_bill(bill.name, duplicate=2)
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		self.assertEqual(frappe.local.response.filename, f"{bill.name}-duplicate.pdf")

	def test_cancelled_bill_is_marked(self):
		run, bill = self.october_posted()
		frappe.set_user("Administrator")
		run.reload()
		run.flags.ignore_permissions = True
		run.cancellation_reason = "Run reversed: wrong readings"  # FS-RUN-06 needs a reason
		run.cancel()
		self.assertIn("CANCELLED", bill_print.bill_html(bill.name))

	def test_layout_settings(self):
		soc = frappe.get_doc("Society", SOC)
		soc.update({"bill_history_months": 1, "print_office_copy": 0, "show_qr_code": 0, "bill_notices": ""})
		soc.save()
		# August, September and October posted: October's history holds only September (1 month).
		self.month_reading("2026-08-01", 2830)
		self.fpa("2026-08-01", 0)
		self.post(self.make_run("2026-08-01", "2026-09-01"))
		self.month_reading("2026-09-01", 3641)
		self.fpa("2026-09-01", 0)
		self.post(self.make_run("2026-09-01", "2026-10-01"))
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		html = bill_print.bill_html(self.bill(run).name)
		self.assertIn("Sep 2026", html)
		self.assertNotIn("Aug 2026", html)
		self.assertNotIn("Office copy", html)
		self.assertNotIn("data:image/png;base64,", html)
		self.assertNotIn("Notices", html)

	def test_barcode_and_qr_are_chosen_separately_and_a_second_language_is_printed(self):
		"""Gap 12 and FS-LOC (increment 21): a society may print the barcode, the QR code, both or neither; the QR may hold a payment
		link; labels, notices and payment instructions can also be printed in Urdu or Arabic."""
		from utility_billing.utility_billing import barcode

		soc = frappe.get_doc("Society", SOC)
		soc.update({"show_qr_code": 0, "show_barcode": 1, "second_language": "Urdu", "bill_notices_second": "بل وقت پر ادا کریں",
		            "payment_instructions_second": "کسی بھی ایم بی ایل برانچ میں ادائیگی کریں"})
		soc.save()
		run, bill = self.october_posted()
		ref = frappe.db.get_value("Connection", self.conn, "reference_no")
		html = bill_print.bill_html(bill.name)
		self.assertEqual(html.count(barcode.png_data_uri(ref)), 2)  # consumer copy and the bank's office copy
		self.assertNotIn(bill_print.qr_data_uri(ref), html)
		for text in ("آخری تاریخ", "صارف کی کاپی", 'dir="rtl" lang="ur"', "بل وقت پر ادا کریں", "کسی بھی ایم بی ایل برانچ میں ادائیگی کریں",
		             "Due date", "Consumer copy"):
			self.assertIn(text, html, text)
		self.assertTrue(bill_print.pdf_of([bill.name]).startswith(b"%PDF"))
		# the posted bill keeps its look (FS-RUN-09): later settings change new bills only
		soc.reload()
		soc.update({"show_barcode": 0, "second_language": "", "show_qr_code": 1, "qr_content": "Payment link",
		            "qr_link_template": "https://pay.example.pk/?ref={reference_no}&amount={amount}"})
		soc.save()
		self.assertEqual(bill_print.bill_html(bill.name), html)
		fresh = frappe._dict(bill.as_dict(), print_snapshot=None)
		ctx = bill_print.bill_context(frappe.get_doc(dict(fresh, doctype="Utility Bill")))
		self.assertIsNone(ctx["barcode"])
		snap = bill_print.build_snapshot(bill)
		self.assertEqual(bill_print.qr_text(snap["society"], bill, ref), f"https://pay.example.pk/?ref={ref}&amount=23364")

	def test_the_meter_photo_is_printed_beside_the_readings(self):
		import base64

		from PIL import Image

		buf = io.BytesIO()
		Image.new("RGB", (1600, 1200), (40, 90, 160)).save(buf, "JPEG")
		f = frappe.get_doc({"doctype": "File", "file_name": "meter.jpg", "content": buf.getvalue(), "is_private": 1}).insert()
		self.september_posted_with_arrears()
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		frappe.db.set_value("Meter Reading", {"reading_cycle": cycle, "connection": self.conn}, "photo", f.file_url)
		readings.close_cycle(cycle)
		self.fpa("2026-10-01", 1.30)
		bill = self.bill(self.post(self.make_run("2026-10-01", "2026-11-01")))
		html = bill_print.bill_html(bill.name)
		self.assertIn('class="meter-photo"', html)
		uri = html.split('class="meter-photo" src="')[1].split('"')[0]
		self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
		small = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
		self.assertLessEqual(max(small.size), 480)  # scaled down, not the phone's full photo
		frappe.db.set_value("Society", SOC, "show_meter_photo", 0)
		bill2 = frappe.get_doc("Utility Bill", bill.name)
		bill2.db_set("print_snapshot", None)  # the society setting is taken into the snapshot
		self.assertNotIn('class="meter-photo"', bill_print.bill_html(bill.name))

	def test_history_months_must_be_1_to_24(self):
		soc = frappe.get_doc("Society", SOC)
		for bad in (0, 25):
			soc.bill_history_months = bad
			with self.assertRaises(frappe.ValidationError):
				soc.save()

	def test_bulk_print_in_delivery_order_one_page_each(self):
		from pypdf import PdfReader

		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": "A Block Owner"}).insert().name
		prop = frappe.get_doc({"doctype": "Property", "society": SOC, "property_no": "A 5", "block": "A Block"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M2", "society": SOC, "utility": "Electricity"}).insert().name
		# a connection's route is one of the society's reading routes (bug report 2026-11-16)
		frappe.get_doc({"doctype": "Reading Route", "society": SOC, "utility": "Electricity", "route_code": "R1"}).insert()
		conn2 = frappe.get_doc({
			"doctype": "Connection", "society": SOC, "property": prop, "consumer": consumer, "utility": "Electricity",
			"consumer_category": self.cat, "status": "Active", "route": "R1",
			"meters": [{"meter": meter, "installed_on": "2026-07-01", "start_reading": 100}],
		}).insert().name
		cycle = self.month_reading("2026-10-01", 3905, close=False)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": conn2})
		r.present_reading, r.reading_date = 400, "2026-10-05"
		r.save()
		if r.status == "Flagged":
			readings.approve_reading(r.name, "Checked")
		readings.close_cycle(cycle)
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		self.assertEqual(run.bill_count, 2)

		order = [b.block for b in bill_print.run_bills(run.name)]
		self.assertEqual(order, ["A Block", "N Block"])  # FS-RUN-08: delivery order
		self.assertEqual([b.block for b in bill_print.run_bills(run.name, block="N Block")], ["N Block"])
		self.assertEqual([b.route for b in bill_print.run_bills(run.name, route="R1")], ["R1"])
		self.assertEqual(len(bill_print.run_bills(run.name, route="(none)")), 1)
		groups = bill_print.run_print_groups(run.name)
		self.assertEqual((groups["total"], groups["blocks"]), (2, {"A Block": 1, "N Block": 1}))

		bill_print.print_run_bills(run.name)
		pdf = frappe.local.response.filecontent
		self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 2)  # each bill fits on one A4 page
		with self.assertRaises(frappe.ValidationError):
			bill_print.print_run_bills(run.name, block="No Such Block")
		limit = bill_print.MAX_BILLS_PER_PDF
		try:
			bill_print.MAX_BILLS_PER_PDF = 1
			with self.assertRaises(frappe.ValidationError):
				bill_print.print_run_bills(run.name)  # the direct one-PDF address keeps its limit
			# increment 56: more bills than one PDF holds are printed in parts, in delivery order (the demo society has one block, no routes)
			out = bill_print.print_run_bills_to_file(run.name, covers=0)  # increment 60: no route cover sheet, one page per bill
			self.assertEqual((out["parts"], out["bills"]), (2, 2))
			self.assertEqual([(f["part"], f["first"], f["last"], f["file_name"]) for f in out["files"]],
			                 [(1, 1, 1, f"{run.name}-part-01-of-02.pdf"), (2, 2, 2, f"{run.name}-part-02-of-02.pdf")])
			first = frappe.get_doc("File", {"file_url": out["files"][0]["file_url"]}).get_content()
			self.assertEqual(len(PdfReader(io.BytesIO(first)).pages), 1)
			again = bill_print.print_run_bills_to_file(run.name, part=2)  # one part, replacing its earlier copy
			self.assertEqual([(f["part"], f["first"]) for f in again["files"]], [(2, 2)])
			names = frappe.get_all("File", filters={"attached_to_doctype": "Bill Run", "attached_to_name": run.name}, pluck="file_url")
			self.assertEqual(sorted(names), sorted([out["files"][0]["file_url"], again["files"][0]["file_url"]]))
			with self.assertRaises(frappe.ValidationError):
				bill_print.print_run_bills_to_file(run.name, part=3)
		finally:
			bill_print.MAX_BILLS_PER_PDF = limit

	def test_guest_cannot_print(self):
		run, bill = self.october_posted()
		frappe.set_user("Guest")
		for call in (lambda: bill_print.download_bill(bill.name), lambda: bill_print.issue_duplicate(bill.name),
		             lambda: bill_print.print_run_bills(run.name)):
			with self.assertRaises(frappe.PermissionError):
				call()
