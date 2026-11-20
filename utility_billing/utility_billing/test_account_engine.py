# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Unit tests for the customer account rules (FS-PAY-07, FS-REC-11, FS-CFG-17), including spec SC-19."""

from frappe.tests import UnitTestCase

from utility_billing.utility_billing import account_engine as e


def bal(current=0, arrears=0, surcharge=0, fees=0, credit=0):
	return {k: e.D(v) for k, v in dict(current=current, arrears=arrears, surcharge=surcharge, fees=fees, credit=credit).items()}


def plain(d):
	return {k: float(v) for k, v in d.items() if v}


class UnitTestAccountEngine(UnitTestCase):
	def test_sc19_current_then_arrears_then_surcharge(self):
		"""SC-19: owes 22,260 current, 1,104 arrears, 1,781 surcharge; pays 23,000."""
		before = bal(current=22260, arrears=1104, surcharge=1781)
		d = e.payment(before, 23000)
		self.assertEqual(plain(d), {"current": -22260, "arrears": -740})
		after = e.apply(before, d)
		self.assertEqual((after["current"], after["arrears"], after["surcharge"]), (0, 364, 1781))
		self.assertEqual(e.total(after), 2145)

	def test_other_posting_orders(self):
		before = bal(current=22260, arrears=1104, surcharge=1781)
		self.assertEqual(plain(e.payment(before, 2000, e.order_of("Arrears, Current bill, Surcharge"))),
		                 {"arrears": -1104, "current": -896})
		self.assertEqual(plain(e.payment(before, 2000, e.order_of("Oldest first"))), {"arrears": -1104, "surcharge": -896})
		self.assertEqual(e.order_of(None), e.DEFAULT_ORDER)

	def test_fees_paid_last_and_overpayment_becomes_credit(self):
		before = bal(current=100, fees=50)
		self.assertEqual(plain(e.payment(before, 120)), {"current": -100, "fees": -20})
		self.assertEqual(plain(e.payment(before, 200)), {"current": -100, "fees": -50, "credit": 50})

	def test_new_bill_moves_unpaid_current_to_arrears(self):
		d = e.bill(bal(current=1104), 22260)
		self.assertEqual(plain(d), {"current": 22260 - 1104, "arrears": 1104})

	def test_new_bill_uses_credit(self):
		after = e.apply(bal(credit=500), e.bill(bal(credit=500), 22260))
		self.assertEqual((after["current"], after["credit"]), (21760, 0))
		after = e.apply(bal(credit=30000), e.bill(bal(credit=30000), 22260))
		self.assertEqual((after["current"], after["credit"]), (0, 7740))

	def test_charge_uses_credit(self):
		after = e.apply(bal(credit=300), e.charge(bal(credit=300), "fees", 500))
		self.assertEqual((after["fees"], after["credit"]), (200, 0))

	def test_credit_note_limited_to_balance(self):
		self.assertEqual(plain(e.credit_note(bal(surcharge=1781), "surcharge", 1781)), {"surcharge": -1781})
		with self.assertRaises(ValueError):
			e.credit_note(bal(surcharge=100), "surcharge", 101)

	def test_reversing_a_payment_restores_balances(self):
		before = bal(current=22260, arrears=1104, surcharge=1781)
		paid = e.payment(before, 23000)
		after = e.apply(before, paid)
		self.assertEqual(e.apply(after, e.reverse(after, paid)), before)

	def test_reversing_a_paid_fee_becomes_credit(self):
		start = bal(fees=500)
		after_pay = e.apply(start, e.payment(start, 500))
		fee = {"fees": e.D(500)}
		after = e.apply(after_pay, e.reverse(after_pay, fee))
		self.assertEqual((after["fees"], after["credit"]), (0, 500))

	def test_reversing_used_credit_moves_to_arrears(self):
		# Overpaid 1,000 (credit), the next bill used it, then the payment bounced.
		start = bal(current=0)
		pay = e.payment(start, 1000)
		s1 = e.apply(start, pay)
		s2 = e.apply(s1, e.bill(s1, 3000))
		self.assertEqual((s2["current"], s2["credit"]), (2000, 0))
		s3 = e.apply(s2, e.reverse(s2, pay))
		self.assertEqual((s3["current"], s3["arrears"], s3["credit"], e.total(s3)), (2000, 1000, 0, 3000))

	def test_rounding_to_paise(self):
		self.assertEqual(e.D("1080.305"), e.D("1080.31"))
		self.assertEqual(plain(e.payment(bal(current="10.10"), "10.1")), {"current": -10.1})

	def test_a_court_hold_takes_the_oldest_first_and_no_more_than_is_owed(self):
		"""Increment 36: arrears, surcharge, fees, then the current bill; capped at what the account owes."""
		before = bal(current=22260, arrears=1104, surcharge=1781, fees=500)
		self.assertEqual(plain(e.hold(before, 1000)), {"arrears": -1000})
		self.assertEqual(plain(e.hold(before, 4000)), {"arrears": -1104, "surcharge": -1781, "fees": -500, "current": -615})
		self.assertEqual(e.total(e.apply(before, e.hold(before, 25645))), 0)
		self.assertRaises(ValueError, e.hold, before, 25646)
		self.assertRaises(ValueError, e.hold, before, 0)
		self.assertRaises(ValueError, e.hold, bal(credit=100), 1)  # nothing owed
