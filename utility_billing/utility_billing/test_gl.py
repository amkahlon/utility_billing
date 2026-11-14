# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for increment 8: account mapping, summary journals, export, receivables
reconciliation (FS-GL-01-04, FS-REC-13, 20; FS-CFG-03)."""

import csv
import io

import frappe

from utility_billing.install import create_default_export_layouts, create_default_reason_codes
from utility_billing.utility_billing import account, gl
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_OFFICER, BM_AND_GM, CASHIER, GENERAL_MANAGER, act

ACCOUNTS = [
	("T-1200", "Customer receivables", "Receivable"), ("T-1010", "Cash in hand", "Cash"), ("T-1020", "MBL current account", "Bank"),
	("T-4100", "Electricity revenue", "Income"), ("T-4110", "FPA recovered", "Income"), ("T-4120", "Service charges", "Income"),
	("T-4130", "Aquifer charges", "Income"), ("T-4900", "Surcharge income", "Income"), ("T-4990", "Other billing income", "Income"),
	("T-5100", "Bill adjustments and waivers", "Expense"),
]
CHARGE_ACCOUNTS = {"TST Energy": "T-4100", "TST FPA": "T-4110", "TST Service": "T-4120", "TST Aquifer": "T-4130"}


class IntegrationTestGL(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_export_layouts()
		create_default_reason_codes()
		for code, name, kind in ACCOUNTS:
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		for ct, acct in CHARGE_ACCOUNTS.items():
			frappe.db.set_value("Charge Type", ct, "account_code", acct)
		soc = frappe.get_doc("Society", SOC)
		soc.update({"receivables_account": "T-1200", "surcharge_income_account": "T-4900", "other_income_account": "T-4990",
		            "adjustments_account": "T-5100", "surcharge_rule": "Nothing paid by the due date"})
		soc.set("payment_accounts", [{"mode": "Cash", "account": "T-1010"}, {"mode": "Bank Transfer", "account": "T-1020"},
		                             {"mode": "Cheque", "account": "T-1020"}])
		soc.save()

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		return run, self.bill(run)

	def lines(self, journal):
		j = frappe.get_doc("GL Journal", journal)
		return {l.account: round(l.debit - l.credit, 2) for l in j.lines}

	# ----- journals (FS-GL-02) -----
	def test_bill_run_journal_made_on_approval(self):
		run, bill = self.october_posted()
		run.reload()
		self.assertTrue(run.gl_journal)
		j = frappe.get_doc("GL Journal", run.gl_journal)
		self.assertEqual((j.journal_type, str(j.posting_date), j.entry_count, j.total_debit, j.total_credit),
		                 ("Bill Run", "2026-11-01", 1, 22260, 22260))
		self.assertEqual(self.lines(j.name), {"T-1200": 22260, "T-4100": -18480, "T-4110": -1080, "T-4120": -1700, "T-4130": -1000})
		self.assertEqual(j.receivables_change, 22260)
		entry = frappe.db.get_value("Customer Ledger Entry", bill.ledger_entry, "gl_journal")
		self.assertEqual(entry, j.name)

	def test_daily_journal_collections_surcharge_fee_and_note(self):
		run, bill = self.october_posted()
		account.apply_surcharges(as_of="2026-11-16", society=SOC)  # surcharge 1,781 (October unpaid)
		fee_type = frappe.get_doc({"doctype": "Fee Type", "society": SOC, "fee_name": "Reconnection", "amount": 500,
		                           "income_account": "T-4990"}).insert()
		fee = frappe.get_doc({"doctype": "Fee Charge", "connection": self.conn, "fee_type": fee_type.name}).insert()
		fee.submit()
		frappe.set_user(BM_AND_GM)
		note = frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": self.conn,
		                       "bucket": "Surcharge", "amount": 781, "reason_code": "SUR-WAIVE", "remarks": "Partial waiver"}).insert()
		frappe.set_user("Administrator")
		act(note, "Submit for Approval", BM_AND_GM)
		act(note, "Approve", GENERAL_MANAGER)
		made = gl.make_journals_for(SOC)
		self.assertEqual(len(made), 1)  # the bill run journals were made on approval; one daily journal today
		j = frappe.get_doc("GL Journal", made[0])
		self.assertEqual(j.journal_type, "Daily Transactions")
		# Sep payment 58,366 (bank), surcharge 1,781, fee 500, credit note 781 (all posted today)
		self.assertEqual(self.lines(j.name), {"T-1020": 58366, "T-1200": -58366 + 1781 + 500 - 781, "T-4900": -1781,
		                                      "T-4990": -500, "T-5100": 781})
		self.assertEqual(j.total_debit, j.total_credit)
		self.assertEqual(gl.make_journals_for(SOC), [])  # every entry is journaled once

	def test_reversal_mirrors_the_original(self):
		run, bill = self.october_posted()
		r = self.pay(1000, "2026-11-05", mode="Bank Transfer")
		frappe.set_user(BM_AND_GM)
		account.reverse_document("Payment Receipt", r.name, "WRONG-ACC", "Belongs to another house")
		frappe.set_user("Administrator")
		reversal = frappe.db.get_value("Customer Ledger Entry", {"reverses": frappe.db.get_value("Payment Receipt", r.name, "ledger_entry")})
		self.assertEqual(sorted(gl.lines_for(reversal)), [("T-1020", 0, 1000), ("T-1200", 1000, 0)])  # bank back out, owed again

	# ----- mapping (FS-GL-01) -----
	def test_missing_account_does_not_block_billing(self):
		frappe.db.set_value("Charge Type", "TST Aquifer", "account_code", None)
		run, bill = self.october_posted()
		run.reload()
		self.assertEqual((bill.docstatus, run.gl_journal), (1, None))  # bills posted, journal waits
		with self.assertRaises(gl.MissingAccount):
			gl.make_bill_run_journal(run.name)
		r = gl.reconcile(SOC, save=False)
		self.assertEqual(r.status, "Timing differences")
		frappe.db.set_value("Charge Type", "TST Aquifer", "account_code", "T-4130")
		gl.make_journals_for(SOC)
		run.reload()
		self.assertTrue(run.gl_journal)

	def test_charge_type_account_must_exist(self):
		ct = frappe.get_doc("Charge Type", "TST Energy")
		ct.account_code = "NO-SUCH-ACCOUNT"
		with self.assertRaises(frappe.LinkValidationError):
			ct.save()

	# ----- reconciliation (FS-GL-04, REC-13) -----
	def test_reconciliation(self):
		run, bill = self.october_posted()
		# Both bill runs were journaled on approval (59,470 + 22,260); the September payment isn't yet.
		r = gl.reconcile(SOC)
		self.assertEqual((r.status, r.customer_accounts_total, r.receivables_posted), ("Timing differences", 23364, 81730))
		self.assertEqual((r.not_journaled_count, r.not_journaled_amount, r.unexplained), (1, -58366, 0))
		gl.make_journals_for(SOC)
		r = gl.reconcile(SOC)
		self.assertEqual((r.status, r.difference, r.unexplained), ("Matched", 0, 0))
		self.assertEqual(frappe.db.get_value("Receivables Reconciliation", r.name, "status"), "Matched")
		# Anything not explained by unjournaled entries is reported as a difference.
		line = frappe.get_all("GL Journal Line", filters={"account": "T-1200"}, fields=["name", "debit"], limit=1)[0]
		frappe.db.set_value("GL Journal Line", line.name, "debit", line.debit + 10, update_modified=False)
		r = gl.reconcile(SOC, save=False)
		self.assertEqual((r.status, r.unexplained), ("Difference", -10))

	# ----- journals can't be changed (FS-REC-20) -----
	def test_journals_are_immutable(self):
		run, bill = self.october_posted()
		j = frappe.get_doc("GL Journal", frappe.db.get_value("Bill Run", run.name, "gl_journal"))
		j.narration = "edited"
		with self.assertRaises(frappe.ValidationError):
			j.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("GL Journal", j.name, ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "GL Journal", "society": SOC, "posting_date": "2026-11-01"}).insert(ignore_permissions=True)

	# ----- export (FS-GL-03) -----
	def export(self, layout, **kw):
		gl.export_journals(SOC, "2026-01-01", "2026-12-31", layout, **kw)
		return frappe.local.response

	def test_export_layouts(self):
		run, bill = self.october_posted()
		gl.make_journals_for(SOC)
		resp = self.export("Standard CSV", journal_type="Bill Run")
		rows = list(csv.reader(io.StringIO(resp.filecontent.decode())))
		self.assertEqual(rows[0], ["Journal No", "Date", "Account Code", "Account Name", "Debit", "Credit", "Narration"])
		self.assertIn(["T-1200", "Customer receivables", "22260.00", ""], [r[2:6] for r in rows[1:]])  # two decimals
		self.assertTrue(any(r[1] == "01-11-2026" for r in rows[1:]))
		self.assertTrue(resp.filename.endswith(".csv"))

		rows = list(csv.reader(io.StringIO(self.export("Sage 50 / Peachtree general journal (CSV)").filecontent.decode())))
		self.assertEqual(rows[0], ["Date", "Reference", "Number of Distributions", "GL Account", "Description", "Amount"])
		self.assertEqual(round(sum(float(r[5]) for r in rows[1:]), 2), 0)  # signed amounts balance
		self.assertIn("11/01/2026", [r[0] for r in rows[1:]])

		rows = list(csv.reader(io.StringIO(self.export("QuickBooks journal (CSV)").filecontent.decode())))
		self.assertEqual(rows[0][:3], ["Journal No", "Journal Date", "Account"])
		self.assertIn("Electricity revenue", [r[2] for r in rows[1:]])

		resp = self.export("Standard Excel")
		self.assertTrue(resp.filecontent[:2] == b"PK" and resp.filename.endswith(".xlsx"))
		j = frappe.get_doc("GL Journal", frappe.db.get_value("Bill Run", run.name, "gl_journal"))
		self.assertEqual((j.export_count, j.last_exported_by), (4, "Administrator"))

	def test_export_and_journal_rights(self):
		run, bill = self.october_posted()
		for user in (CASHIER, BILLING_OFFICER):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				gl.export_journals(SOC, "2026-01-01", "2026-12-31", "Standard CSV")
			with self.assertRaises(frappe.PermissionError):
				gl.make_journals(SOC)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			gl.export_journals(SOC, "2025-01-01", "2025-01-31", "Standard CSV")  # no journals then
