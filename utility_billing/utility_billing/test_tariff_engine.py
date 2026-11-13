# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the tariff engine (no database)."""

from decimal import Decimal

from frappe.tests import UnitTestCase

from utility_billing.utility_billing.tariff_engine import (
	FLAT,
	PROGRESSIVE,
	WHOLE_BILL,
	calculate_energy,
	slab_errors,
)

D = Decimal
SLABS = [
	{"from_units": 0, "to_units": 100, "rate": 10},
	{"from_units": 100, "to_units": 200, "rate": 15},
	{"from_units": 200, "to_units": 0, "rate": 20},  # open-ended
]


class TestTariffEngine(UnitTestCase):
	def test_case_1_flat_rate(self):
		"""Spec test case 1, house N 2308: 3905 - 3641 = 264 units x Rs 70 = 18,480."""
		r = calculate_energy(264, FLAT, flat_rate=70)
		self.assertEqual(r.amount, D("18480.00"))
		self.assertFalse(r.minimum_applied)

	def test_progressive_slabs(self):
		# 100 x 10 + 100 x 15 + 50 x 20
		r = calculate_energy(250, PROGRESSIVE, slabs=SLABS)
		self.assertEqual(r.amount, D("3500.00"))
		self.assertEqual([line["units"] for line in r.lines], [D(100), D(100), D(50)])

	def test_progressive_inside_first_slab(self):
		self.assertEqual(calculate_energy(60, PROGRESSIVE, slabs=SLABS).amount, D("600.00"))

	def test_whole_bill_slabs(self):
		self.assertEqual(calculate_energy(250, WHOLE_BILL, slabs=SLABS).amount, D("5000.00"))  # all at 20
		self.assertEqual(calculate_energy(150, WHOLE_BILL, slabs=SLABS).amount, D("2250.00"))  # all at 15

	def test_slab_boundary_belongs_to_lower_slab(self):
		self.assertEqual(calculate_energy(100, WHOLE_BILL, slabs=SLABS).amount, D("1000.00"))
		self.assertEqual(calculate_energy(100, PROGRESSIVE, slabs=SLABS).amount, D("1000.00"))

	def test_zero_units(self):
		self.assertEqual(calculate_energy(0, PROGRESSIVE, slabs=SLABS).amount, D("0.00"))
		self.assertEqual(calculate_energy(0, WHOLE_BILL, slabs=SLABS).amount, D("0.00"))

	def test_minimum_charge(self):
		r = calculate_energy(2, FLAT, flat_rate=70, minimum_charge=500)
		self.assertEqual(r.amount, D("500.00"))
		self.assertTrue(r.minimum_applied)
		self.assertFalse(calculate_energy(10, FLAT, flat_rate=70, minimum_charge=500).minimum_applied)

	def test_fractional_units_round_to_paisa(self):
		self.assertEqual(calculate_energy(12.345, FLAT, flat_rate=67).amount, D("827.12"))

	def test_negative_units_refused(self):
		with self.assertRaises(ValueError):
			calculate_energy(-1, FLAT, flat_rate=70)

	def test_slab_errors(self):
		self.assertEqual(slab_errors(SLABS), [])
		self.assertTrue(slab_errors([]))
		self.assertIn(
			"The first slab must start at 0 units.",
			slab_errors([{"from_units": 1, "to_units": 0, "rate": 5}]),
		)
		gap = [{"from_units": 0, "to_units": 100, "rate": 5}, {"from_units": 120, "to_units": 0, "rate": 6}]
		self.assertTrue(any("must start at 100" in e for e in slab_errors(gap)))
		open_middle = [{"from_units": 0, "to_units": 0, "rate": 5}, {"from_units": 100, "to_units": 0, "rate": 6}]
		self.assertTrue(any("only the last slab" in e for e in slab_errors(open_middle)))
