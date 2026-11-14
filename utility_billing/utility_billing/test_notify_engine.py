# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests of the notification rules, the settlement file parsing and matching, and the refund rule (increment 12).
No database."""

from datetime import date, datetime, time

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import account_engine as acc
from utility_billing.utility_billing import notify_engine as e
from utility_billing.utility_billing import settlement as st

ALL_IN = {"mobile_no": "0300-1234567", "email": "a@example.com", "notify_sms": 1, "notify_whatsapp": 1, "notify_email": 1, "notify_app": 1}


class UnitTestNotifyEngine(UnitTestCase):
	def test_a_channel_needs_the_opt_in_and_somewhere_to_send(self):
		self.assertTrue(e.accepts("SMS", ALL_IN))
		self.assertFalse(e.accepts("SMS", {**ALL_IN, "notify_sms": 0}))
		self.assertFalse(e.accepts("SMS", {**ALL_IN, "mobile_no": " "}))
		self.assertFalse(e.accepts("Email", {**ALL_IN, "email": None}))
		self.assertFalse(e.accepts("Email", {**ALL_IN, "notify_email": 0}))
		self.assertFalse(e.accepts("App", ALL_IN))  # the customer app isn't built: nobody has it installed
		self.assertTrue(e.accepts("App", {**ALL_IN, "app_installed": 1}))
		self.assertFalse(e.accepts("Pigeon", ALL_IN))

	def test_usable_channels_follow_the_rule_order_and_the_society_switches(self):
		order = ("SMS", "WhatsApp", "Email")
		self.assertEqual(e.usable_channels(order, ALL_IN), ["SMS", "Email"])  # WhatsApp is off unless the society turns it on
		self.assertEqual(e.usable_channels(order, ALL_IN, whatsapp_enabled=True), ["SMS", "WhatsApp", "Email"])
		self.assertEqual(e.usable_channels(order, {**ALL_IN, "notify_sms": 0}, True), ["WhatsApp", "Email"])
		self.assertEqual(e.usable_channels(order, ALL_IN, True, switched_off=("SMS",)), ["WhatsApp", "Email"])
		self.assertEqual(e.usable_channels(("SMS", "SMS", None), ALL_IN), ["SMS"])
		self.assertEqual(e.usable_channels(order, {**ALL_IN, "notify_sms": 0, "notify_email": 0}), [])

	def test_the_preferred_channel_goes_first_when_usable(self):
		self.assertEqual(e.usable_channels(("SMS", "Email"), {**ALL_IN, "preferred_channel": "Email"}), ["Email", "SMS"])
		self.assertEqual(e.usable_channels(("SMS", "Email"), {**ALL_IN, "preferred_channel": "WhatsApp"}), ["SMS", "Email"])  # not in the rule
		self.assertEqual(e.usable_channels(("SMS", "Email"), {**ALL_IN, "preferred_channel": "Email", "notify_email": 0}), ["SMS"])

	def test_next_channel_after_a_failure(self):
		self.assertEqual(e.next_channel(["SMS", "Email"], "SMS"), "Email")
		self.assertIsNone(e.next_channel(["SMS"], "SMS"))
		self.assertIsNone(e.next_channel([], "SMS"))

	def test_quiet_hours_across_midnight_and_within_a_day(self):
		start, end = time(21, 0), time(8, 0)
		at = lambda h, m=0, d=14: datetime(2026, 11, d, h, m)  # noqa: E731
		self.assertTrue(e.in_quiet_hours(at(23), start, end))
		self.assertTrue(e.in_quiet_hours(at(3), start, end))
		self.assertTrue(e.in_quiet_hours(at(21), start, end))  # inclusive start
		self.assertFalse(e.in_quiet_hours(at(8), start, end))  # exclusive end
		self.assertFalse(e.in_quiet_hours(at(12), start, end))
		self.assertTrue(e.in_quiet_hours(at(13), time(12), time(14)))
		self.assertFalse(e.in_quiet_hours(at(15), time(12), time(14)))
		self.assertFalse(e.in_quiet_hours(at(23), time(0), time(0)))  # equal times: no quiet hours
		self.assertFalse(e.in_quiet_hours(at(23), None, end))

	def test_a_held_message_goes_out_when_quiet_hours_end(self):
		start, end = time(21, 0), time(8, 0)
		self.assertEqual(e.next_allowed(datetime(2026, 11, 14, 23, 30), start, end), datetime(2026, 11, 15, 8, 0))
		self.assertEqual(e.next_allowed(datetime(2026, 11, 14, 3, 30), start, end), datetime(2026, 11, 14, 8, 0))
		self.assertEqual(e.next_allowed(datetime(2026, 11, 14, 12, 0), start, end), datetime(2026, 11, 14, 12, 0))

	def test_retries_wait_longer_each_time_and_stop(self):
		now = datetime(2026, 11, 14, 12, 0)
		self.assertEqual(e.retry_at(now, 1), datetime(2026, 11, 14, 12, 5))
		self.assertEqual(e.retry_at(now, 2), datetime(2026, 11, 14, 12, 15))
		self.assertIsNone(e.retry_at(now, 3))
		self.assertIsNone(e.retry_at(now, 9))

	def test_sms_parts(self):
		self.assertEqual([e.sms_parts(t) for t in ("", "a" * 160, "a" * 161, "a" * 307, "a" * 308)], [0, 1, 2, 3, 3])
		self.assertEqual([e.sms_parts("ا" * 70), e.sms_parts("ا" * 71), e.sms_parts("ا" * 135)], [1, 2, 3])  # Urdu: 70 then 67 per part

	def test_every_event_has_a_default_rule_and_the_critical_ones_are_marked(self):
		self.assertEqual(set(e.EVENTS), set(e.DEFAULT_RULES))
		self.assertTrue(all(e.EVENTS[k][1] for k in ("payment.reversed", "disconnection.notice", "outage.notice", "recovery.notice")))
		self.assertFalse(any(e.EVENTS[k][1] for k in ("bill.issued", "payment.received", "reminder.due")))


class UnitTestSettlementParsing(UnitTestCase):
	def test_dates_in_the_formats_banks_use(self):
		for text in ("2026-11-12", "12-11-2026", "12/11/2026", "12.11.2026", "20261112", "2026-11-12 14:30:00"):
			self.assertEqual(st.parse_date(text), date(2026, 11, 12), text)
		for bad in ("", "yesterday", "32-13-2026", "2026-13-01"):
			with self.assertRaises(ValueError):
				st.parse_date(bad)

	def test_amounts_with_commas_and_currency(self):
		self.assertEqual([st.parse_amount(x) for x in ("1,250", "Rs 1,250.50", " 300 ", "0")], [1250.0, 1250.5, 300.0, 0.0])
		with self.assertRaises(ValueError):
			st.parse_amount("")
		with self.assertRaises(ValueError):
			st.parse_amount("abc")

	def test_csv_with_other_column_names_and_a_fee(self):
		rows = st.parse_csv("﻿TRX_ID,Consumer Reference,Paid Amount,Transaction Date,Commission\nA1,100,\"1,000\",12-11-2026,5\nB2,,250,2026-11-12,\n\n")
		self.assertEqual(rows, [{"txn_id": "A1", "reference_no": "100", "amount": 1000.0, "paid_on": date(2026, 11, 12), "fee": 5.0},
		                        {"txn_id": "B2", "reference_no": "", "amount": 250.0, "paid_on": date(2026, 11, 12), "fee": 0.0}])

	def test_unusable_files_say_why(self):
		for text, word in (("", "empty"), ("a,b\n1,2", "Missing column"), ("transaction_id,amount,date\n", "no rows"),
		                   ("transaction_id,amount,date\nX,10,not-a-date", "Line 2"), ("transaction_id,amount,date\n,10,2026-11-12", "transaction ID is empty"),
		                   ("transaction_id,amount,date\nX,ten,2026-11-12", "Line 2")):
			with self.assertRaises(ValueError) as cm:
				st.parse_csv(text)
			self.assertIn(word, str(cm.exception), text)

	def test_matching_results(self):
		rows = [{"txn_id": t, "amount": a} for t, a in (("A", 100), ("B", 100), ("C", 100), ("D", 100), ("A", 100), ("E", 100.004))]
		recorded = {"A": ("R1", 100, 1), "B": ("R2", 90, 1), "D": ("R3", 100, 2), "E": ("R5", 100, 1)}
		self.assertEqual(st.classify(rows, recorded), ["Matched", "Amount differs", "Not recorded", "Recorded but reversed", "Duplicate in file", "Matched"])

	def test_receipts_missing_from_the_file(self):
		receipts = [{"name": "R1", "channel_txn_id": "A", "payment_date": date(2026, 11, 12), "amount": 1},
		            {"name": "R2", "channel_txn_id": "Z", "payment_date": date(2026, 11, 12), "amount": 1},
		            {"name": "R3", "channel_txn_id": "Y", "payment_date": date(2026, 11, 20), "amount": 1}]
		out = st.missing_from_file(receipts, {"A"}, date(2026, 11, 10), date(2026, 11, 14))
		self.assertEqual([r["name"] for r in out], ["R2"])  # R3 is outside the file's period


class UnitTestRefundRule(UnitTestCase):
	def test_a_refund_takes_credit_down_and_never_below_zero(self):
		bal = {**acc.empty(), "credit": acc.D(6636)}
		self.assertEqual(acc.refund(bal, 6636)["credit"], acc.D(-6636))
		self.assertEqual(acc.refund(bal, 1000)["credit"], acc.D(-1000))
		self.assertEqual(acc.total(acc.apply(bal, acc.refund(bal, 6636))), 0)
		with self.assertRaises(ValueError):
			acc.refund(bal, 6636.01)
		with self.assertRaises(ValueError):
			acc.refund(bal, 0)
		with self.assertRaises(ValueError):
			acc.refund(acc.empty(), 1)

	def test_reversing_a_refund_gives_the_credit_back(self):
		bal = {**acc.empty(), "credit": acc.D(5000)}
		deltas = acc.refund(bal, 2000)
		after = acc.apply(bal, deltas)
		self.assertEqual(acc.apply(after, acc.reverse(after, deltas))["credit"], acc.D(5000))
