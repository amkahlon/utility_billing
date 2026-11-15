# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the bill print number formats and QR code (no database)."""

import base64

from frappe.tests import UnitTestCase

from utility_billing.utility_billing.bill_print import money, qr_data_uri, qty, rate


class UnitTestBillPrintHelpers(UnitTestCase):
	def test_money(self):
		self.assertEqual([money(x) for x in (22260, 1104.0, 0, -350, 1234567.4)], ["22,260", "1,104", "0", "-350", "1,234,567"])

	def test_quantity(self):
		self.assertEqual([qty(x) for x in (264, 831.0, 12.5, 0)], ["264", "831", "12.50", "0"])

	def test_rate(self):
		self.assertEqual([rate(x) for x in (70, 1.3, 1.305, -0.5, 0)], ["70", "1.30", "1.305", "-0.50", ""])

	def test_qr_is_a_png(self):
		uri = qr_data_uri("1000000016")
		self.assertTrue(uri.startswith("data:image/png;base64,"))
		self.assertEqual(base64.b64decode(uri.split(",", 1)[1])[:8], b"\x89PNG\r\n\x1a\n")

	def test_code_128(self):
		from utility_billing.utility_billing import barcode

		self.assertEqual(len(barcode.PATTERNS), 107)
		self.assertTrue(all(sum(map(int, p)) == 11 for p in barcode.PATTERNS[:106]))  # every symbol is 11 modules, the stop 13
		self.assertEqual(sum(map(int, barcode.PATTERNS[106])), 13)
		self.assertEqual(barcode.symbols("1234"), [105, 12, 34, 82])  # code set C; check (105 + 12x1 + 34x2) mod 103 = 82
		self.assertEqual(barcode.symbols("AB1"), [104, 33, 34, 17, (104 + 33 + 68 + 51) % 103])  # code set B for letters
		self.assertEqual(barcode.symbols("123")[0], 104)  # an odd number of digits: code set B
		w = barcode.widths("1000000016")
		self.assertEqual((len(w) % 6, sum(w)), (1, 11 * 7 + 13))  # start + 5 pairs + check, then stop
		self.assertRaises(ValueError, barcode.symbols, "")
		self.assertEqual(base64.b64decode(barcode.png_data_uri("1000000016").split(",", 1)[1])[:8], b"\x89PNG\r\n\x1a\n")

	def test_labels_in_a_second_language(self):
		from utility_billing.utility_billing.bill_print import labeller

		self.assertEqual(str(labeller(None)("Due date")), "Due date")
		urdu = str(labeller("Urdu")("Due date"))
		self.assertIn('dir="rtl" lang="ur"', urdu)
		self.assertIn("آخری تاریخ", urdu)
		self.assertEqual(str(labeller("Urdu")("Something new")), "Something new")  # no translation: English only

	def test_digits_in_right_to_left_text_are_kept_left_to_right(self):
		from utility_billing.utility_billing.bill_print import rtl_text

		self.assertEqual(str(rtl_text("یا 1لنک سے 042-111 پر")), 'یا <span dir="ltr">1</span>لنک سے <span dir="ltr">042-111</span> پر')
		self.assertEqual(str(rtl_text("<b>x</b>")), '&lt;<span dir="ltr">b</span>&gt;<span dir="ltr">x</span>&lt;/<span dir="ltr">b</span>&gt;')  # escaped, entities whole
