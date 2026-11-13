# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for consumer reference numbers (no database)."""

from frappe.tests import UnitTestCase

from utility_billing.utility_billing.reference import is_valid_reference, luhn_check_digit, make_reference


class TestReference(UnitTestCase):
	def test_luhn_known_value(self):
		# Standard Luhn example: 7992739871 -> check digit 3.
		self.assertEqual(luhn_check_digit("7992739871"), 3)

	def test_make_reference(self):
		self.assertEqual(make_reference(1, 1), "1000000016")
		self.assertEqual(len(make_reference(2, 12345678)), 10)
		self.assertTrue(make_reference(3, 42).startswith("3"))

	def test_valid_and_typos(self):
		ref = make_reference(1, 2308)
		self.assertTrue(is_valid_reference(ref))
		# Every single-digit typo is caught.
		for pos in range(len(ref)):
			for d in "0123456789":
				if d == ref[pos]:
					continue
				typo = ref[:pos] + d + ref[pos + 1 :]
				self.assertFalse(is_valid_reference(typo), typo)

	def test_rejects_bad_shapes(self):
		for bad in ("", "123", "abcdefghij", "0000000016", "10000000160"):
			self.assertFalse(is_valid_reference(bad), bad)

	def test_out_of_range(self):
		with self.assertRaises(ValueError):
			make_reference(0, 1)
		with self.assertRaises(ValueError):
			make_reference(1, 0)
		with self.assertRaises(ValueError):
			make_reference(1, 10**8)
