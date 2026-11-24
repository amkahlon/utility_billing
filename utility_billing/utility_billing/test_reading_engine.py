# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for meter reading calculations (no database)."""

from decimal import Decimal

from frappe.tests import UnitTestCase

from utility_billing.utility_billing.reading_engine import (
	AVG_3,
	AVG_6,
	FLAG_HIGH,
	FLAG_LOW,
	FLAG_LOWER,
	FLAG_NEGATIVE_TRUE_UP,
	FLAG_ROLLOVER,
	FLAG_ZERO,
	SAME_MONTH,
	estimate,
	evaluate,
	expected_range,
	raw_units,
)

D = Decimal


class TestReadingEngine(UnitTestCase):
	def test_case_1_units(self):
		"""Spec test case 1, house N 2308: 3,905 - 3,641 = 264 units."""
		r = evaluate(3641, 3905)
		self.assertEqual(r.units, D(264))
		self.assertEqual(r.flags, [])
		self.assertFalse(r.needs_review)

	def test_multiplier(self):
		self.assertEqual(evaluate(1100, 1200, multiplier=40).units, D(4000))

	def test_rollover_is_normal_use(self):
		r = evaluate(99950, 123, digits=5)
		self.assertEqual(r.units, D(173))
		self.assertTrue(r.rolled_over)
		self.assertIn(FLAG_ROLLOVER, r.flags)
		self.assertFalse(r.needs_review)  # roll-over is information only

	def test_lower_reading_is_flagged_not_rollover(self):
		units, rolled, lower = raw_units(5000, 4000, 5, 1)
		self.assertEqual((units, rolled, lower), (D(-1000), False, True))
		r = evaluate(5000, 4000)
		self.assertIn(FLAG_LOWER, r.flags)
		self.assertTrue(r.needs_review)

	def test_zero_use(self):
		r = evaluate(3905, 3905)
		self.assertIn(FLAG_ZERO, r.flags)
		self.assertTrue(r.needs_review)

	def test_expected_range(self):
		self.assertEqual(expected_range([250, 270, 260], tolerance_percent=50), (D(260), D(130), D(390)))
		self.assertEqual(expected_range([250, 270, 260], same_month_last_year=300)[0], D(270))
		self.assertIsNone(expected_range([]))

	def test_the_range_allows_for_the_season(self):
		# increment 49: 330 units a month in summer, 90 last November. A November reading of 90 is expected; so is one like the
		# recent months; one far above both, or far below both, isn't.
		summer, last_november = [180, 330, 340], 90
		avg, low, high = expected_range(summer, same_month_last_year=last_november, tolerance_percent=50)
		self.assertEqual((avg, low, high), (D(235), D(45), D(425)))  # from half of last November to half as much again as the recent average
		for units, flag in ((90, None), (300, None), (46, None), (720, "Above expected range"), (40, "Below expected range")):
			r = evaluate(1000, 1000 + units, history=summer, same_month_last_year=last_november)
			self.assertEqual([f for f in r.flags if "range" in f], [flag] if flag else [], units)
		# with only one of the two known, the range is round what is known, as before
		self.assertEqual(expected_range([], same_month_last_year=90), (D(90), D(45), D(135)))
		self.assertEqual(expected_range([100, 100, 100]), (D(100), D(50), D(150)))

	def test_out_of_range_flags(self):
		hist = [250, 270, 260]
		self.assertIn(FLAG_HIGH, evaluate(1000, 1400, history=hist).flags)
		self.assertIn(FLAG_LOW, evaluate(1000, 1100, history=hist).flags)
		self.assertEqual(evaluate(1000, 1264, history=hist).flags, [])

	def test_no_history_no_range_check(self):
		self.assertEqual(evaluate(0, 5000).flags, [])

	def test_estimates(self):
		hist = [300, 200, 250, 100, 100, 100]
		self.assertEqual(estimate(AVG_3, hist), D(250))
		self.assertEqual(estimate(AVG_6, hist), D(175))
		self.assertEqual(estimate(SAME_MONTH, hist, same_month_last_year=320), D(320))
		self.assertEqual(estimate(SAME_MONTH, hist), D(250))  # falls back to 3-month average
		self.assertIsNone(estimate(AVG_3, []))
		with self.assertRaises(ValueError):
			estimate("Guess", hist)

	def test_true_up_after_estimates(self):
		# Last actual 1,000; two months estimated at 200 each; actual now 1,500 -> 500 used, 100 still to bill.
		r = evaluate(1000, 1500, estimated_since=400)
		self.assertEqual((r.raw_units, r.units), (D(500), D(100)))
		over = evaluate(1000, 1300, estimated_since=400)
		self.assertEqual(over.units, D(-100))
		self.assertIn(FLAG_NEGATIVE_TRUE_UP, over.flags)

	def test_meter_changed_in_cycle(self):
		# Old meter used 150 up to its removal; new meter 0 -> 50.
		self.assertEqual(evaluate(0, 50, old_meter_units=150).units, D(200))
