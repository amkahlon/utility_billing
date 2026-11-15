# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the revenue assurance rules (FS-RA-01, 05, 06, 08, 10)."""

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import revenue_assurance as ra


class UnitTestRevenueAssuranceRules(UnitTestCase):
	def test_a_drop_needs_history_and_must_be_below_both_own_and_peers(self):
		hist = [300, 310, 290, 305, 300, 295]
		self.assertTrue(ra.is_drop(100, hist, 300))
		self.assertFalse(ra.is_drop(200, hist, 300))  # 200 is above 50% of 300
		self.assertFalse(ra.is_drop(100, hist, 150))  # similar accounts also use little: not a drop of this account
		self.assertTrue(ra.is_drop(100, hist, None))  # no similar accounts to compare with: own history decides
		self.assertFalse(ra.is_drop(10, [300, 300], 300))  # too little history
		self.assertTrue(ra.is_drop(200, hist, 300, own_pct=70, peer_pct=70))

	def test_drop_score(self):
		self.assertEqual((ra.drop_score(0, 300), ra.drop_score(150, 300), ra.drop_score(400, 300), ra.drop_score(10, 0)), (100, 50, 0, 0))

	def test_a_multiplier_step_is_recognised(self):
		self.assertEqual(ra.multiplier_factor(3000, 300), 10)
		self.assertEqual(ra.multiplier_factor(30, 300), -10)
		self.assertEqual(ra.multiplier_factor(620, 300), 2)
		self.assertIsNone(ra.multiplier_factor(450, 300))  # 1.5 times: not a multiplier step
		self.assertIsNone(ra.multiplier_factor(0, 300))
		self.assertIsNone(ra.multiplier_factor(300, None))

	def test_estimates_in_a_row(self):
		self.assertEqual(ra.consecutive_estimates(["Estimated", "Estimated", "Actual", "Estimated"]), 2)
		self.assertEqual(ra.consecutive_estimates(["Actual", "Estimated"]), 0)
		self.assertEqual(ra.consecutive_estimates([]), 0)

	def test_outliers_compare_each_user_with_the_others(self):
		self.assertEqual(ra.outliers({"a": 1000, "b": 1200, "c": 900, "d": 15000}), {"d": 15000})
		self.assertEqual(ra.outliers({"a": 1000, "b": 1100, "c": 1050}), {})
		self.assertEqual(ra.outliers({"a": 1000, "b": 90000}), {})  # too few users to compare

	def test_zscore(self):
		self.assertIsNone(ra.zscore(500, [300, 300, 300]))
		self.assertGreater(ra.zscore(900, [300, 310, 290, 305, 300]), 3)
		self.assertAlmostEqual(ra.zscore(300, [300, 310, 290, 305, 300]), 0, delta=0.5)

	def test_residential_and_size_band(self):
		self.assertTrue(ra.is_residential("Electricity-Residential"))
		self.assertTrue(ra.is_residential("Electricity-Domestic A"))
		self.assertFalse(ra.is_residential("Electricity-Commercial"))
		self.assertEqual((ra.size_band(10), ra.size_band(11), ra.size_band(0), ra.size_band(10, "1 Kanal")), ("~10", "~10", "?", "1 Kanal"))
