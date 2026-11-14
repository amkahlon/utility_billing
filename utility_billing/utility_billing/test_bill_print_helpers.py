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
