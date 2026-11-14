# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests of the recovery rules (FS-REC-04, 05, 07, 15). No database."""

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import recovery_engine as e

RULES = [("Reminder", 15, "Print"), ("Notice", 30, "SMS"), ("Final Notice", 45, "Print"), ("Disconnection Order", 60, "Print")]


class UnitTestRecoveryEngine(UnitTestCase):
	def test_defaulter_needs_both_the_amount_and_the_age(self):
		self.assertTrue(e.is_defaulter(23_364, 66, 10_000, 60))
		self.assertTrue(e.is_defaulter(10_000, 60, 10_000, 60))  # the limits are inclusive
		self.assertFalse(e.is_defaulter(9_999, 90, 10_000, 60))
		self.assertFalse(e.is_defaulter(50_000, 59, 10_000, 60))
		self.assertFalse(e.is_defaulter(0, 500, 0, 1))  # nothing owed is never a defaulter
		self.assertFalse(e.is_defaulter(5, 0, 0, 0))  # nothing overdue is never a defaulter, even with a zero rule

	def test_rules_are_ordered_by_stage_and_the_first_row_of_a_stage_wins(self):
		shuffled = [RULES[3], RULES[0], RULES[2], RULES[1], ("Reminder", 99, "SMS")]
		self.assertEqual([r[0] for r in e.ordered_rules(shuffled)], list(e.STAGES))
		self.assertEqual(e.ordered_rules(shuffled)[0], ("Reminder", 15, "Print"))

	def test_rule_checks(self):
		self.assertIsNone(e.check_rules(RULES))
		self.assertIsNone(e.check_rules([]))
		self.assertIn("twice", e.check_rules([RULES[0], ("Reminder", 20, "Print")]))
		self.assertIn("at least 1", e.check_rules([("Reminder", 0, "Print")]))
		self.assertIn("after the stage before", e.check_rules([("Reminder", 30, "Print"), ("Notice", 30, "Print")]))
		self.assertIn("after the stage before", e.check_rules([("Reminder", 30, "Print"), ("Final Notice", 10, "Print")]))
		self.assertIn("Unknown", e.check_rules([("Warning", 10, "Print")]))

	def test_the_next_stage_is_the_earliest_one_not_yet_issued_and_never_skips(self):
		self.assertIsNone(e.next_stage(RULES, 14, []))
		self.assertEqual(e.next_stage(RULES, 15, []), ("Reminder", "Print"))
		self.assertEqual(e.next_stage(RULES, 200, []), ("Reminder", "Print"))  # 200 days old but nothing sent: start at the beginning
		self.assertEqual(e.next_stage(RULES, 200, ["Reminder"]), ("Notice", "SMS"))
		self.assertIsNone(e.next_stage(RULES, 40, ["Reminder", "Notice"]))  # final notice isn't due yet
		self.assertEqual(e.next_stage(RULES, 61, ["Reminder", "Notice", "Final Notice"]), ("Disconnection Order", "Print"))
		self.assertIsNone(e.next_stage(RULES, 999, list(e.STAGES)))
		self.assertIsNone(e.next_stage([], 999, []))

	def test_a_legal_case_or_an_unexpired_promise_stops_recovery(self):
		self.assertEqual(e.on_hold("Legal Case", None, "2026-12-20"), "Legal case open")
		self.assertEqual(e.on_hold("Promise to Pay", "2026-12-25", "2026-12-20"), "Promise to pay not yet due")
		self.assertEqual(e.on_hold("Promise to Pay", "2026-12-20", "2026-12-20"), "Promise to pay not yet due")  # promised for today
		self.assertIsNone(e.on_hold("Promise to Pay", "2026-12-19", "2026-12-20"))  # broken: recovery resumes
		self.assertIsNone(e.on_hold("Open", None, "2026-12-20"))
		self.assertIsNone(e.on_hold("Closed", "2027-01-01", "2026-12-20"))

	def test_promise_words_for_the_screen(self):
		self.assertEqual(e.promise_state("Promise to Pay", "2026-12-25", "2026-12-20"), "Promised")
		self.assertEqual(e.promise_state("Promise to Pay", "2026-12-19", "2026-12-20"), "Broken")
		self.assertIsNone(e.promise_state("Open", "2026-12-19", "2026-12-20"))

	def test_target_progress(self):
		self.assertEqual(e.progress(100_000, 25_000), {"target": 100_000, "recovered": 25_000, "percent": 25.0, "remaining": 75_000, "reached": False})
		p = e.progress(100_000, 120_000)
		self.assertEqual((p["percent"], p["remaining"], p["reached"]), (120.0, 0, True))  # over-achievement shows
		self.assertEqual(e.progress(0, 5_000)["percent"], None)  # no target set: no percentage
		self.assertFalse(e.progress(0, 5_000)["reached"])

	def test_notice_text_replaces_plain_words_only(self):
		text = "Dear {consumer}, {owed} for {days} days. {unknown} {consumer.__class__} {0}"
		self.assertEqual(e.render_message(text, consumer="A Khan", owed="Rs 23,364", days=66),
		                 "Dear A Khan, Rs 23,364 for 66 days. {unknown} {consumer.__class__} {0}")
		self.assertEqual(e.render_message(None, consumer="x"), "")
