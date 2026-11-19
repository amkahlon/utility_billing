# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""The app's own PDFs are drawn by headless Chromium (the Chromium move, 2026-11-18); wkhtmltopdf, no longer maintained, is only
the way back. These tests hold: Chromium is what a normal site uses; every kind of document comes out with the pages and paper
size it had; a second-language bill fits its page; and a bill still prints when Chromium can't."""

import io
import json
from unittest.mock import patch

import frappe
from pypdf import PdfReader

from utility_billing import pdf as ubpdf
from utility_billing.utility_billing import account_print, bill_designer, bill_print
from utility_billing.utility_billing.test_billing import SOC, BillingCase

URDU_NOTICE = "بل مقررہ تاریخ 19-10-2026 تک ادا کریں۔ تاخیر پر 10% سرچارج لاگو ہوگا۔"


def shape(data):
	r = PdfReader(io.BytesIO(data))
	box = r.pages[0].mediabox
	return len(r.pages), round(float(box.width) / 72 * 25.4), round(float(box.height) / 72 * 25.4)


class IntegrationTestPdfEngine(BillingCase):
	def posted_bill(self):
		return self.bill(self.september_posted_with_arrears()).name

	def test_chromium_is_the_engine_and_it_starts(self):
		# a server where this fails makes its PDFs with the old engine: install Chromium's libraries and run `bench setup-chrome`
		self.assertTrue(ubpdf.chrome_works(), "headless Chromium doesn't start on this machine")
		self.assertEqual(ubpdf.engine(), "chrome")

	def test_a_bill_is_one_a4_page_in_both_engines_with_every_part(self):
		name = self.posted_bill()
		html, options = bill_print.document_html([name], False), bill_print.pdf_options_for([name])
		for engine in ubpdf.ENGINES:
			data = ubpdf.render(html, options, engine_name=engine)
			self.assertEqual(shape(data), (1, 210, 297), engine)
			text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
			for part in ("N 2308", "Payable within due date", "Energy Charges", name):
				self.assertIn(part, text, f"{engine}: {part}")

	def test_bills_printed_together_keep_one_page_each(self):
		first = self.posted_bill()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		second = self.bill(self.post(self.make_run("2026-10-01", "2026-11-01"))).name
		self.assertEqual(shape(bill_print.pdf_of([first, second])), (2, 210, 297))

	def test_receipt_and_statement_keep_their_paper(self):
		self.posted_bill()
		receipt = frappe.db.get_value("Payment Receipt", {"connection": self.conn, "docstatus": 1}, "name")
		for label, html, options, pages, size in (
			("receipt", account_print.wrap(account_print.receipt_html(receipt), receipt), account_print.RECEIPT_PDF, 1, (148, 210)),
			("statement", account_print.wrap(account_print.statement_html(self.conn), self.conn), account_print.STATEMENT_PDF, 1, (210, 297)),
		):
			for engine in ubpdf.ENGINES:
				self.assertEqual(shape(ubpdf.render(html, dict(options), engine_name=engine)), (pages, *size), f"{label} {engine}")

	def test_every_ready_made_layout_fits_its_long_test_bill_on_one_page(self):
		frappe.set_user("Administrator")
		for s in bill_designer.designer_data()["starters"]:
			st = bill_designer.starter(s["key"])
			settings = bill_designer._settings_from(json.dumps(st["page"]), json.dumps(st["layout"]))
			self.assertEqual(bill_designer.pages_of(settings, bill_designer.long_bill(SOC)), 1, s["key"])

	def test_an_urdu_bill_fits_one_page(self):
		"""A second-language bill with its notice, barcode and QR code stays on one page. (That the Urdu words are in the right order
		was checked by eye on a picture of the page, 2026-11-18: text taken back out of a PDF doesn't show the order reliably.)"""
		name = self.posted_bill()
		real = bill_print.snapshot_of

		def urdu(bill):
			snap = real(bill)
			snap["society"].update({"second_language": "Urdu", "notices_second": [URDU_NOTICE], "show_barcode": True, "show_qr": True})
			return snap

		with patch.object(bill_print, "snapshot_of", urdu):
			html = bill_print.document_html([name], False)
		data = ubpdf.render(html, bill_print.pdf_options_for([name]), engine_name="chrome")
		self.assertEqual(shape(data), (1, 210, 297))
		self.assertIn('dir="rtl"', html)
		self.assertIn(URDU_NOTICE.split()[0], html)

	def test_a_bill_still_prints_when_chromium_cannot(self):
		name = self.posted_bill()
		html, options = bill_print.document_html([name], False), bill_print.pdf_options_for([name])
		with patch.object(ubpdf, "chrome_works", return_value=False):
			self.assertEqual(ubpdf.engine(), "wkhtmltopdf")  # a server without Chromium's libraries
			self.assertEqual(shape(ubpdf.render(html, options)), (1, 210, 297))
		with patch.object(ubpdf, "_chrome", side_effect=RuntimeError("Chromium crashed")):
			before = frappe.db.count("Error Log")
			self.assertEqual(shape(ubpdf.render(html, options)), (1, 210, 297))  # falls back, and leaves a trace
			self.assertEqual(frappe.db.count("Error Log"), before + 1)

	def test_the_site_can_choose_the_old_engine(self):
		with patch.dict(frappe.local.conf, {"ub_pdf_engine": "wkhtmltopdf"}):
			self.assertEqual(ubpdf.engine(), "wkhtmltopdf")
		with patch.dict(frappe.local.conf, {"ub_pdf_engine": "something else"}):
			self.assertEqual(ubpdf.engine(), "chrome")
