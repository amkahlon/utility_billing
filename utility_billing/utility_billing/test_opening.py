# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Opening balances at go-live (gap 9): loading, checking, maker-checker approval and posting."""

import frappe
from frappe.model.workflow import WorkflowTransitionError
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, getdate

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing import account, opening, periods
from utility_billing.utility_billing.testing import BILLING_MANAGER, GENERAL_MANAGER, act, make_users

SOC, OTHER = "TST-OB", "TST-OB2"
AS_OF = "2026-09-30"
ACCOUNTS = [("OB-1200", "Receivables", "Receivable"), ("OB-3900", "Opening balance equity", "Equity"), ("OB-2200", "Security deposits held", "Liability")]


class UnitTestOpeningFile(UnitTestCase):
	def test_columns_and_values_are_read_forgivingly(self):
		rows = opening.rows_from_table([["Reference Number", "Current Bill", "arrears", "Security Deposit", "Installments", "notes"],
		                                ["1000000016.0", "22,260", "", "5000", "3", "x"], ["", "", "", "", "", ""]])
		self.assertEqual(len(rows), 1)
		r = rows[0]
		self.assertEqual((r["reference_no"], r["current_bill"], r["arrears"], r["security_deposit"], r["plan_installments"]), ("1000000016", 22260, 0, 5000, 3))
		self.assertRaises(frappe.ValidationError, opening.rows_from_table, [["amount"], ["5"]])
		self.assertEqual(opening.rows_from_table([["reference_no", "fees"], ["1", "abc"]])[0]["fees"], "abc")  # reported by the check


class IntegrationTestOpening(IntegrationTestCase):
	def setUp(self):
		create_default_utilities()
		make_users()
		for code, name, kind in ACCOUNTS:
			if not frappe.db.exists("Ledger Account", code):
				frappe.get_doc({"doctype": "Ledger Account", "account_code": code, "account_name": name, "account_type": kind}).insert()
		for s in (SOC, OTHER):
			if not frappe.db.exists("Society", s):
				frappe.get_doc({"doctype": "Society", "society_name": f"Opening Test {s}", "short_name": s}).insert()
		frappe.db.set_value("Society", SOC, {"receivables_account": "OB-1200", "opening_balance_account": "OB-3900",
		                                     "deposit_liability_account": "OB-2200", "go_live_date": None, "max_installments": 6})
		frappe.clear_document_cache("Society", SOC)
		periods.clear_cache()
		self.cat = "Electricity-TST OB"
		if not frappe.db.exists("Consumer Category", self.cat):
			frappe.get_doc({"doctype": "Consumer Category", "category_name": "TST OB", "utility": "Electricity"}).insert()
		self.n = 0
		self.a, self.b, self.c = self.account(), self.account(), self.account()
		self.foreign = self.account(OTHER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		periods.clear_cache()

	def account(self, society=SOC):
		self.n += 1
		consumer = frappe.get_doc({"doctype": "Consumer", "consumer_name": f"OB Owner {society} {self.n}"}).insert().name
		prop = frappe.get_doc({"doctype": "Property", "society": society, "property_no": f"OB-{self.n}"}).insert().name
		meter = frappe.get_doc({"doctype": "Meter", "serial_no": f"TST-OB-{society}-{self.n}", "society": society, "utility": "Electricity"}).insert().name
		return frappe.get_doc({"doctype": "Connection", "society": society, "property": prop, "consumer": consumer, "utility": "Electricity",
		                       "consumer_category": self.cat, "status": "Active",
		                       "meters": [{"meter": meter, "installed_on": "2026-01-01", "start_reading": 0}]}).insert().name

	def make(self, rows):
		return frappe.get_doc({"doctype": "Opening Balance Import", "society": SOC, "as_of_date": AS_OF, "rows": rows}).insert()

	def good_rows(self):
		return [{"reference_no": self.a, "current_bill": 5000, "arrears": 12000, "surcharge": 600, "fees": 100, "security_deposit": 5000,
		         "plan_remaining": 9000, "plan_installments": 3},
		        {"reference_no": self.b, "credit": 750},
		        {"reference_no": self.c, "current_bill": 3200, "security_deposit": 2500}]

	# ---- checks
	def test_every_problem_is_shown_against_its_row(self):
		doc = self.make([
			{"reference_no": "9999999999", "arrears": 10},
			{"reference_no": self.foreign, "arrears": 10},
			{"reference_no": self.a, "arrears": -5},
			{"reference_no": self.a, "arrears": 10},
			{"reference_no": self.b, "arrears": 10, "credit": 5},
			{"reference_no": self.c, "arrears": 100, "plan_remaining": 500, "plan_installments": 3},
			{"reference_no": self.c},
		])
		problems = [(r.row_status, r.error) for r in doc.rows]
		self.assertEqual(doc.error_count, 7)
		for (status, text), expected in zip(problems, ["no account", "belongs to TST-OB2", "can't be negative", "listed twice", "either owes or is in credit",
		                                              "more than the arrears", "listed twice"], strict=True):
			self.assertEqual(status, "Error")
			self.assertIn(expected, text)
		doc.rows[-1].reference_no = self.foreign
		doc.set("rows", [{"reference_no": self.c, "plan_installments": 9}, {"reference_no": self.b}])
		doc.save()
		self.assertIn("installments left without an amount", doc.rows[0].error)
		self.assertEqual(doc.rows[1].error, "nothing to bring over")

	def test_it_cant_be_submitted_with_problems(self):
		doc = self.make([{"reference_no": "9999999999", "arrears": 10}])
		self.assertRaises(frappe.ValidationError, act, doc, "Submit for Approval", BILLING_MANAGER)

	# ---- posting
	def test_approval_posts_balances_deposits_plans_journals_and_go_live(self):
		doc = self.make(self.good_rows())
		self.assertEqual((doc.error_count, doc.row_count, doc.total_receivable, doc.total_deposits, doc.total_plans), (0, 3, 20150, 7500, 9000))
		act(doc, "Submit for Approval", BILLING_MANAGER)
		self.assertRaises((frappe.PermissionError, WorkflowTransitionError), act, doc, "Approve", BILLING_MANAGER)  # only a General Manager approves
		act(doc, "Approve", GENERAL_MANAGER)
		doc.reload()
		self.assertEqual((doc.docstatus, doc.workflow_state), (1, "Approved"))
		# the accounts
		b = account.balances(self.a)
		self.assertEqual({k: float(v) for k, v in b.items()}, {"current": 5000, "arrears": 12000, "surcharge": 600, "fees": 100, "credit": 0})
		self.assertEqual((account.balance(self.b), account.balance(self.c)), (-750, 3200))
		e = frappe.get_doc("Customer Ledger Entry", doc.rows[0].ledger_entry)
		self.assertEqual((e.entry_type, str(e.posting_date), e.amount, e.voucher_no), ("Opening", AS_OF, 17700, doc.name))
		# the journals: receivables 20,150 against opening balances; deposits 7,500 held against opening balances
		j = frappe.get_doc("GL Journal", doc.gl_journal)
		self.assertEqual({(l.account, l.debit, l.credit) for l in j.lines}, {("OB-1200", 20150, 0), ("OB-3900", 0, 20150)})
		dj = frappe.get_doc("GL Journal", doc.deposit_journal)
		self.assertEqual({(l.account, l.debit, l.credit) for l in dj.lines}, {("OB-3900", 7500, 0), ("OB-2200", 0, 7500)})
		# deposits held and the plan
		from utility_billing.utility_billing.doctype.security_deposit.security_deposit import held

		self.assertEqual((held(self.a), held(self.c), frappe.db.get_value("Connection", self.a, "security_deposit")), (5000, 2500, 5000))
		plan = frappe.get_doc("Installment Plan", {"connection": self.a, "docstatus": 1})
		self.assertEqual((plan.plan_status, plan.amount_to_split, [(str(r.billing_month), r.amount) for r in plan.schedule]),
		                 ("Active", 9000, [("2026-10-01", 3000), ("2026-11-01", 3000), ("2026-12-01", 3000)]))
		# go-live: nothing can be dated on or before the as-at day
		self.assertEqual(getdate(frappe.db.get_value("Society", SOC, "go_live_date")), getdate(add_days(AS_OF, 1)))
		account.lock(self.c)
		self.assertRaises(frappe.ValidationError, account.post, self.c, "Fee", {"fees": 100}, "Opening Balance Import", doc.name, posting_date=AS_OF)
		account.post(self.c, "Fee", {"fees": 100}, "Opening Balance Import", doc.name, posting_date=add_days(AS_OF, 1))
		self.assertEqual(periods.next_open_date(SOC, "2026-09-15"), getdate("2026-10-01"))
		# once live: no second import, and the posted one isn't cancelled
		self.assertRaises(frappe.ValidationError, self.make, [{"reference_no": self.b, "arrears": 10}])
		self.assertRaises(frappe.ValidationError, doc.cancel)

	def test_an_account_already_in_use_or_unmapped_accounts_stop_the_posting(self):
		doc = self.make(self.good_rows())
		act(doc, "Submit for Approval", BILLING_MANAGER)
		frappe.db.set_value("Society", SOC, "opening_balance_account", None)
		frappe.clear_document_cache("Society", SOC)
		self.assertRaises(frappe.ValidationError, act, doc, "Approve", GENERAL_MANAGER)
		self.assertFalse(frappe.db.exists("Customer Ledger Entry", {"connection": self.a}))  # nothing half-posted
		frappe.db.set_value("Society", SOC, "opening_balance_account", "OB-3900")
		frappe.clear_document_cache("Society", SOC)
		account.lock(self.b)
		account.post(self.b, "Fee", {"fees": 50}, "Opening Balance Import", doc.name, posting_date="2026-10-02")  # the account got going meanwhile
		self.assertRaises(frappe.ValidationError, act, doc, "Approve", GENERAL_MANAGER)
		self.assertFalse(frappe.db.exists("Customer Ledger Entry", {"connection": self.a}))

	def test_load_a_csv_file(self):
		content = "reference_no,current_bill,arrears,credit,security_deposit\n" + f"{self.a},5000,1000,0,0\n{self.b},0,0,250,0\n9999999999,10,0,0,0\n"
		f = frappe.get_doc({"doctype": "File", "file_name": "opening.csv", "content": content, "is_private": 1}).insert()
		doc = self.make([])
		doc.import_file = f.file_url
		doc.save()
		frappe.set_user(BILLING_MANAGER)
		out = opening.load_file(doc.name)
		frappe.set_user("Administrator")
		self.assertEqual(out, {"rows": 3, "errors": 1})
		doc.reload()
		self.assertEqual([r.row_status for r in doc.rows], ["OK", "OK", "Error"])

	# ---- increment 48: a large import. The pilot's 20,991 rows greyed the screen for two minutes on Submit for Approval, then the
	# form was refused for sending over 25 MB; the user, 2026-11-24: "instead of greying the screen, system should display the
	# progress bar so that user may know whats going on". Here "large" is over 2 rows, and the form shows 1.
	def large(self):
		from unittest.mock import patch  # noqa: PLC0415

		for name, value in (("BIG", 2), ("SHOWN", 1)):
			p = patch.object(opening, name, value)
			p.start()
			self.addCleanup(p.stop)
		doc = self.make(self.good_rows())
		self.addCleanup(frappe.cache.delete_value, opening._key(doc.name))
		return doc

	def step(self, doc, action, user):
		frappe.set_user(user)
		try:
			return opening.act(doc.name, action)
		finally:
			frappe.set_user("Administrator")

	def test_a_large_imports_form_carries_its_first_rows_and_cant_be_saved_from_it(self):
		doc = self.large()
		form = frappe.get_doc("Opening Balance Import", doc.name)
		form.run_method("onload")
		self.assertEqual((len(form.rows), form.get("__onload").rows_shown, form.get("__onload").rows_total, form.row_count, form.total_receivable),
		                 (1, 1, 3, 3, 20150))
		self.assertRaisesRegex(frappe.ValidationError, "can't be saved from the form", form.save)  # saving the first rows would delete the others
		self.assertEqual(frappe.db.count("Opening Balance Row", {"parent": doc.name}), 3)
		# a small import's form carries every row, and is saved as before
		with __import__("unittest.mock").mock.patch.object(opening, "BIG", 500):
			small = frappe.get_doc("Opening Balance Import", doc.name)
			small.run_method("onload")
			self.assertEqual((len(small.rows), small.get("__onload").get("rows_total")), (3, None))
			small.save()

	def test_a_large_imports_steps_run_as_a_job_that_says_how_far_it_is(self):
		doc = self.large()
		said = []
		write = opening._write
		with __import__("unittest.mock").mock.patch.object(opening, "_write", side_effect=lambda name, values: (said.append(dict(values)), write(name, values))):
			self.assertEqual(self.step(doc, "Submit for Approval", BILLING_MANAGER), {"queued": True})
			self.assertRaises(frappe.ValidationError, self.step, doc, "Approve", BILLING_MANAGER)  # not a Billing Manager's step
			self.assertEqual(self.step(doc, "Approve", GENERAL_MANAGER), {"queued": True})
		doc.reload()
		self.assertEqual((doc.docstatus, doc.workflow_state, doc.requested_by, doc.approved_by), (1, "Approved", BILLING_MANAGER, GENERAL_MANAGER))
		self.assertEqual((account.balance(self.a), account.balance(self.b), account.balance(self.c)), (17700, -750, 3200))
		self.assertTrue(doc.gl_journal and doc.deposit_journal)
		states = [(v.get("state"), v.get("action")) for v in said]
		self.assertEqual(states[0], ("Queued", "Submit for Approval"))
		self.assertIn(("Done", "Submit for Approval"), states)
		self.assertEqual(states[-1], ("Done", "Approve"))
		texts = {v.get("text") for v in said}
		self.assertTrue({"Checking the accounts", "Saving", "Posting the balances", "Recording the security deposits", "Making the journal"} <= texts, texts)
		now = opening.progress(doc.name)
		self.assertEqual((now["state"], now["action"], now["workflow_state"], now["by"]), ("Done", "Approve", "Approved", GENERAL_MANAGER))

	def test_a_step_that_fails_changes_nothing_and_says_why(self):
		doc = self.large()
		self.step(doc, "Submit for Approval", BILLING_MANAGER)
		frappe.db.set_value("Society", SOC, "opening_balance_account", None)
		frappe.clear_document_cache("Society", SOC)
		self.assertRaises(frappe.ValidationError, self.step, doc, "Approve", GENERAL_MANAGER)
		now = opening.progress_of(doc.name)
		self.assertEqual((now["state"], now["action"]), ("Failed", "Approve"))
		self.assertIn("opening balances", now["error"].lower())
		self.assertEqual((frappe.db.get_value("Opening Balance Import", doc.name, "workflow_state"), frappe.db.exists("Customer Ledger Entry", {"connection": self.a})),
		                 ("Pending Approval", None))
		# a second step isn't started while one runs; one that went quiet is taken as stopped
		opening._write(doc.name, {"state": "Running", "action": "Approve", "text": "Posting the balances"})
		self.assertRaisesRegex(frappe.ValidationError, "already running", self.step, doc, "Approve", GENERAL_MANAGER)
		frappe.cache.set_value(opening._key(doc.name), {"state": "Running", "action": "Approve", "at": "2026-01-01 00:00:00"})
		stopped = opening.progress_of(doc.name)
		self.assertEqual((stopped["state"], "stopped" in stopped["error"]), ("Failed", True))
		self.assertRaisesRegex(frappe.ValidationError, "isn't available", self.step, doc, "Submit for Approval", BILLING_MANAGER)

	def test_a_small_imports_step_runs_at_once(self):
		doc = self.make(self.good_rows())
		self.assertEqual(self.step(doc, "Submit for Approval", BILLING_MANAGER), {"queued": False, "workflow_state": "Pending Approval"})
		self.assertIsNone(opening.progress_of(doc.name))

	def test_the_checks_find_an_account_by_its_old_number_too(self):
		frappe.db.set_value("Connection", self.c, "legacy_account_no", "OLD-OB-77")
		frappe.db.set_value("Society", SOC, "old_numbers_until", "2027-12-31")
		frappe.clear_document_cache("Society", SOC)
		doc = self.make([{"reference_no": self.a, "arrears": 100}, {"reference_no": "OLD-OB-77", "arrears": 50}, {"reference_no": "NO-SUCH", "arrears": 5}])
		self.assertEqual([(r.connection, r.row_status) for r in doc.rows], [(self.a, "OK"), (self.c, "OK"), (None, "Error")])
		account.lock(self.a)
		account.post(self.a, "Fee", {"fees": 50}, "Opening Balance Import", doc.name, posting_date="2026-10-02")
		doc.reload()
		doc.save()
		self.assertIn("already has entries", doc.rows[0].error)
