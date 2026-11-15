# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 24 part A: SoftVerx bills the societies per active account per month from a control site (user decisions 2026-11-16).

House N 2308 (spec test case 1) is one active electricity account of the test society."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_months, getdate, nowdate

from utility_billing.utility_billing import licensing, mis
from utility_billing.utility_billing.test_billing import SOC, BillingCase

LAST_MONTH = add_months(getdate(nowdate()).replace(day=1), -1)


class IntegrationTestLicensing(BillingCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Licence Settings", {"mode": licensing.SITE, "control_url": "https://licence.example.test", "invoice_day": 5,
		                                                 "seller_name": "SoftVerx", "last_sent_month": None, "last_error": None})
		s = frappe.get_doc("Licence Settings")
		s.licence_key = "will-be-replaced"
		s.save()
		frappe.clear_document_cache("Licence Settings", "Licence Settings")

	def control_mode(self):
		frappe.db.set_single_value("Licence Settings", "mode", licensing.CONTROL)
		frappe.clear_document_cache("Licence Settings", "Licence Settings")

	def site_mode(self):
		frappe.db.set_single_value("Licence Settings", "mode", licensing.SITE)
		frappe.clear_document_cache("Licence Settings", "Licence Settings")

	def client(self, **kw):
		c = frappe.get_doc({"doctype": "Licence Client", "client_name": "TST Client", "rate_per_account": 25, "monthly_minimum": 0, "currency": "PKR",
		                    "tax_percent": 16, "payment_days": 15, "start_date": "2026-01-01", **kw}).insert()
		key = licensing.new_key(c.name)["key"]
		s = frappe.get_doc("Licence Settings")
		s.licence_key = key
		s.save()
		frappe.clear_document_cache("Licence Settings", "Licence Settings")
		return c.name, key

	def fake_control(self, key):
		"""requests.post as the control site would answer it, in the same database."""
		def post(url, json=None, headers=None, timeout=None):
			self.control_mode()
			frappe.flags.licence_key = headers[licensing.KEY_HEADER]
			try:
				reply = licensing.receive_usage(**json)
				status = 200
			except frappe.AuthenticationError as e:
				reply, status = {"error": str(e)}, 401
			finally:
				frappe.flags.licence_key = None
				frappe.set_user("Administrator")
				self.site_mode()
			r = MagicMock(status_code=status, headers={"content-type": "application/json"})
			r.json.return_value = {"message": reply}
			return r
		return patch("requests.post", side_effect=post)

	def test_active_accounts_are_counted_per_society_and_utility(self):
		lines = [x for x in licensing.count_usage(LAST_MONTH) if x["society"] == SOC]
		self.assertEqual(lines, [{"society": SOC, "utility": "Electricity", "accounts": 1}])
		frappe.db.set_value("Connection", self.conn, {"status": "Disconnected", "disconnected_on": LAST_MONTH})
		self.assertEqual([x["accounts"] for x in licensing.count_usage(LAST_MONTH) if x["society"] == SOC], [1])  # active on the 1st
		frappe.db.set_value("Connection", self.conn, {"disconnected_on": add_months(LAST_MONTH, -1)})
		self.assertEqual([x for x in licensing.count_usage(LAST_MONTH) if x["society"] == SOC], [])  # gone before the month

	def test_usage_reaches_the_control_site_and_is_invoiced_and_paid(self):
		client, key = self.client(monthly_minimum=0)
		with self.fake_control(key):
			licensing.daily()
		usage = frappe.get_doc("Licence Usage", {"month": LAST_MONTH, "client": ["is", "not set"]})
		self.assertEqual(usage.status, "Accepted")
		received = frappe.get_doc("Licence Usage", {"month": LAST_MONTH, "client": client})
		self.assertEqual((received.status, received.accounts), ("Received", usage.accounts))
		self.assertEqual(getdate(frappe.db.get_single_value("Licence Settings", "last_sent_month")), LAST_MONTH)
		self.control_mode()
		self.assertEqual(licensing.raise_invoices(getdate(nowdate()).replace(day=4)), [])  # before the invoice day
		(inv,) = licensing.raise_invoices(getdate(nowdate()).replace(day=6))
		inv = frappe.get_doc("Licence Invoice", inv)
		net = usage.accounts * 25
		self.assertEqual((inv.accounts, inv.amount, inv.tax_amount, inv.total, inv.status), (usage.accounts, net, round(net * 0.16, 2), round(net * 1.16, 2), "Unpaid"))
		self.assertEqual(licensing.raise_invoices(getdate(nowdate()).replace(day=7)), [])  # once a month
		html = licensing.invoice_html(inv.name)
		for text in ("SoftVerx", "TST Client", inv.name, SOC):
			self.assertIn(text, html)
		p = frappe.get_doc({"doctype": "Licence Payment", "client": client, "amount": inv.total + 100, "reference": "BANK-1"}).insert()
		p.submit()
		inv.reload()
		self.assertEqual((inv.status, inv.outstanding, p.unallocated), ("Paid", 0, 100))
		self.assertRaises(frappe.ValidationError, inv.cancel)  # paid: the payment goes first
		p.cancel()
		inv.reload()
		self.assertEqual((inv.status, inv.outstanding), ("Unpaid", inv.total))
		licensing.mark_overdue(inv.due_date + __import__("datetime").timedelta(days=1))
		self.assertEqual(frappe.db.get_value("Licence Invoice", inv.name, "status"), "Overdue")
		_c, data, *_x = mis.run("licence_receivables", {"as_of": inv.due_date + __import__("datetime").timedelta(days=40)})
		row = next(r for r in data if r["client"] == client)
		self.assertEqual((row["outstanding"], row["d60"]), (inv.total, inv.total))

	def test_the_monthly_minimum_and_a_wrong_key(self):
		client, key = self.client(monthly_minimum=50000)
		self.control_mode()
		frappe.flags.licence_key = "ums_not-the-key"
		self.assertRaises(frappe.AuthenticationError, licensing.receive_usage, month=str(LAST_MONTH), lines=[])
		frappe.flags.licence_key = key
		self.assertRaises(frappe.ValidationError, licensing.receive_usage, month=nowdate(), lines=[])  # a month not yet over
		licensing.receive_usage(month=str(LAST_MONTH), site="tst", lines=[{"society": SOC, "utility": "Electricity", "accounts": 120}])
		frappe.flags.licence_key = None
		frappe.set_user("Administrator")
		(inv,) = licensing.raise_invoices(getdate(nowdate()).replace(day=6))
		inv = frappe.get_doc("Licence Invoice", inv)
		self.assertEqual((inv.amount, inv.minimum_applied, inv.net_amount, inv.total), (3000, 1, 50000, 58000))
		self.assertEqual(licensing.missing_usage(LAST_MONTH), [c for c in licensing.missing_usage(LAST_MONTH) if c != client])
