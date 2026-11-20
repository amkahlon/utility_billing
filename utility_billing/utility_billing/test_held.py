# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Amounts held by court order (increment 35): from the opening balances, off the bills, released or written off."""

import frappe
from frappe.tests import IntegrationTestCase

from utility_billing.utility_billing import account, gl, held
from utility_billing.utility_billing import test_opening as ob
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, GENERAL_MANAGER, act

HELD, BAD_DEBTS = "OB-1250", "OB-6100"


class IntegrationTestHeldAmount(IntegrationTestCase):
	account = ob.IntegrationTestOpening.account
	make = ob.IntegrationTestOpening.make
	tearDown = ob.IntegrationTestOpening.tearDown

	def setUp(self):
		ob.IntegrationTestOpening.setUp(self)
		for code, name, kind in ((HELD, "Held receivables", "Receivable"), (BAD_DEBTS, "Bad debts", "Expense")):
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		frappe.db.set_value("Society", ob.SOC, {"held_receivables_account": HELD, "write_off_account": BAD_DEBTS})
		frappe.clear_document_cache("Society", ob.SOC)

	def posted(self):
		doc = self.make([{"reference_no": self.a, "arrears": 1000, "held_amount": 4000, "court_order": "WP 123/2026"},
		                 {"reference_no": self.b, "held_amount": 2500}])  # an account with nothing but a held amount
		self.assertEqual((doc.error_count, doc.total_receivable, doc.total_held), (0, 1000, 6500))
		act(doc, "Submit for Approval", BILLING_MANAGER)
		act(doc, "Approve", GENERAL_MANAGER)
		doc.reload()
		return doc, {h.connection: h for h in frappe.get_all("Held Amount", filters={"opening_import": doc.name}, fields=["*"])}

	def test_held_at_go_live_and_kept_off_the_bill(self):
		doc, h = self.posted()
		self.assertEqual({k: (v.status, v.amount) for k, v in h.items()}, {self.a: ("Held", 4000), self.b: ("Held", 2500)})
		self.assertEqual(h[self.a].court_order, "WP 123/2026")
		j = frappe.get_doc("GL Journal", doc.held_journal)  # held receivables against the opening balances account
		self.assertEqual({(x.account, x.debit, x.credit) for x in j.lines}, {(HELD, 6500, 0), ("OB-3900", 0, 6500)})
		self.assertEqual({(x.account, x.debit, x.credit) for x in frappe.get_doc("GL Journal", doc.gl_journal).lines},
		                 {("OB-1200", 1000, 0), ("OB-3900", 0, 1000)})  # the receivables journal leaves the held amounts out
		# not in the account's balance, so not on the next bill, not surcharged, not recovered
		self.assertEqual((account.balance(self.a), account.arrears_for_new_bill(self.a), account.balance(self.b)), (1000, 1000, 0))
		self.assertEqual((held.held_of(self.a), held.held_of(self.b)), (4000, 2500))
		self.assertFalse(frappe.db.exists("Customer Ledger Entry", {"connection": self.b}))
		# Consumer 360 shows it beside what is owed
		from utility_billing.utility_billing import consumer360  # noqa: PLC0415

		o = consumer360.overview(self.a)
		self.assertEqual(o["payable"]["court_held"], 4000)
		self.assertIn("Rs 4,000 held by court order (WP 123/2026): not billed", [x["text"] for x in o["alerts"]])

	def test_released_into_the_arrears_by_a_billing_manager(self):
		_doc, h = self.posted()
		ha = frappe.get_doc("Held Amount", h[self.a].name)
		frappe.set_user(BILLING_OFFICER)
		self.assertRaises(frappe.PermissionError, ha.release, "Court dismissed the petition")
		frappe.set_user(BILLING_MANAGER)
		self.assertRaises(frappe.ValidationError, ha.release, "ok")  # say why
		self.assertEqual(ha.release("Court dismissed the petition"), "Released")
		ha.reload()
		e = frappe.get_doc("Customer Ledger Entry", ha.ledger_entry)
		self.assertEqual((e.entry_type, e.amount, e.arrears_balance, e.voucher_no), ("Hold released", 4000, 5000, ha.name))
		self.assertEqual((account.balance(self.a), held.held_of(self.a)), (5000, 0))  # owed now, billed with the next bill
		self.assertEqual(gl.lines_for(e.name), [("OB-1200", 4000, 0), (HELD, 0, 4000)])  # back into the receivables
		self.assertRaises(frappe.ValidationError, ha.release, "Court dismissed the petition again")  # decided once

	def test_written_off_by_a_general_manager_only(self):
		_doc, h = self.posted()
		hb = frappe.get_doc("Held Amount", h[self.b].name)
		frappe.set_user(BILLING_MANAGER)
		self.assertRaises(frappe.PermissionError, hb.write_off, "Court quashed the demand")
		frappe.set_user(GENERAL_MANAGER)
		self.assertEqual(hb.write_off("Court quashed the demand"), "Written off")
		hb.reload()
		j = frappe.get_doc("GL Journal", hb.decision_journal)
		self.assertEqual({(x.account, x.debit, x.credit) for x in j.lines}, {(BAD_DEBTS, 2500, 0), (HELD, 0, 2500)})
		self.assertFalse(frappe.db.exists("Customer Ledger Entry", {"connection": self.b}))  # the account's ledger is untouched
		frappe.set_user("Administrator")
		hb.amount = 1
		self.assertRaises(frappe.ValidationError, hb.save)  # a decided amount isn't changed

	def test_the_held_receivables_account_is_needed_before_posting(self):
		frappe.db.set_value("Society", ob.SOC, "held_receivables_account", None)
		frappe.clear_document_cache("Society", ob.SOC)
		doc = self.make([{"reference_no": self.a, "held_amount": 100}])
		act(doc, "Submit for Approval", BILLING_MANAGER)
		self.assertRaises(frappe.ValidationError, act, doc, "Approve", GENERAL_MANAGER)
		self.assertFalse(frappe.db.exists("Held Amount", {"connection": self.a}))
