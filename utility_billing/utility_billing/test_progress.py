# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 54: one progress store for every background job, and a progress bar for a bill run's posting."""

import frappe

from utility_billing.utility_billing import bill_print, billing, progress
from utility_billing.utility_billing.doctype.bill_run import bill_run
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, CASHIER


class IntegrationTestProgress(BillingCase):
	def test_store_steps_and_a_silent_job(self):
		name = frappe.generate_hash(length=8)
		self.addCleanup(progress.clear, "test", name)
		self.assertIsNone(progress.get("test", name))
		progress.step("Nothing is reporting")  # outside a job: ignored
		self.assertIsNone(progress.get("test", name))
		with progress.reporting("test", name):
			progress.step("Posting the bills", 500, 21446)
		p = progress.get("test", name)
		self.assertEqual((p["state"], p["text"], p["done"], p["total"]), ("Running", "Posting the bills", 500, 21446))
		self.assertTrue(progress.running(p))
		self.assertIsNone(frappe.flags.get("ub_progress"))
		frappe.cache.set_value(progress.key("test", name), {**p, "at": "2026-01-01 00:00:00"})
		stopped = progress.get("test", name, stopped="It stopped.")
		self.assertEqual((stopped["state"], stopped["error"]), ("Failed", "It stopped."))
		self.assertFalse(progress.running(stopped))

	def calculated(self):
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.make_run("2026-10-01", "2026-11-01")

	def test_posting_says_how_many_bills_are_posted(self):
		run = self.calculated()
		self.addCleanup(progress.clear, bill_run.POSTING, run.name)
		with progress.reporting(bill_run.POSTING, run.name):
			billing.post_run(run)
		p = progress.get(bill_run.POSTING, run.name)
		self.assertEqual((p["text"], p["done"], p["total"]), ("Posting the bills", 1, 1))

	def test_posting_progress_as_the_screen_reads_it(self):
		run = self.post(self.calculated())
		self.addCleanup(progress.clear, bill_run.POSTING, run.name)
		frappe.set_user(BILLING_OFFICER)
		self.assertEqual(bill_run.posting_progress(run.name)["state"], "Done")  # posted at once, no news kept
		frappe.set_user("Administrator")
		frappe.db.set_value("Bill Run", run.name, "posting_status", "Posting")
		self.assertEqual(bill_run.posting_progress(run.name), {"state": "Running", "done": 1, "total": 1, "text": "Posting the bills"})
		progress.tell(bill_run.POSTING, run.name, "Done", 1, 1, "1 bills posted.")
		self.assertEqual(bill_run.posting_progress(run.name)["state"], "Running")  # the run isn't marked Posted yet
		progress.tell(bill_run.POSTING, run.name, "Running", 0, 1, "Posting the bills")
		frappe.db.set_value("Bill Run", run.name, "posting_status", "Posting failed")
		with self.assertRaises(frappe.ValidationError) as e:  # a job still at work: no second one
			bill_run.retry_posting(run.name)
		self.assertIn("still being posted", str(e.exception))
		frappe.db.set_value("Bill Run", run.name, "posting_status", "Posting")
		frappe.cache.set_value(progress.key(bill_run.POSTING, run.name), {"state": "Running", "done": 0, "total": 1, "at": "2026-01-01 00:00:00"})
		p = bill_run.posting_progress(run.name)
		self.assertEqual(p["state"], "Failed")
		self.assertIn("Post Again", p["error"])
		self.assertEqual(frappe.db.get_value("Bill Run", run.name, "posting_status"), "Posting failed")  # so Post Again is offered

	# ---- increment 55: buttons whose work runs in the background
	def job(self, job, record):
		self.addCleanup(progress.clear, progress.JOB, progress.job_key(job, record))
		return progress.job_progress(job, record)

	def test_only_listed_jobs_start_and_one_at_a_time(self):
		with self.assertRaises(frappe.ValidationError):
			progress.start("frappe.client.delete", {"doctype": "User"})
		with self.assertRaises(frappe.ValidationError):
			progress.start("reading_sheet", {})  # which cycle?
		key = progress.job_key("reading_sheet", "X-CYCLE")
		self.addCleanup(progress.clear, progress.JOB, key)
		progress.tell(progress.JOB, key, "Running", 5, 10, "Adding the readings", by=BILLING_OFFICER)
		with self.assertRaises(frappe.ValidationError) as e:
			progress.start("reading_sheet", {"cycle": "X-CYCLE"})
		self.assertIn("already running", str(e.exception))

	def test_a_job_runs_as_its_user_and_its_result_is_theirs(self):
		cycle = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-12-01"}).insert()
		frappe.set_user(BILLING_OFFICER)
		self.assertEqual(progress.start("reading_sheet", {"cycle": cycle.name}, "Creating the reading sheet"), {"job": "reading_sheet", "record": cycle.name})
		p = self.job("reading_sheet", cycle.name)
		self.assertEqual((p["state"], p["by"], p["title"], p["result"]["created"]), ("Done", BILLING_OFFICER, "Creating the reading sheet", 1))
		self.assertEqual(frappe.db.count("Meter Reading", {"reading_cycle": cycle.name}), 1)
		frappe.set_user(BILLING_MANAGER)
		self.assertNotIn("result", progress.job_progress("reading_sheet", cycle.name))  # another user sees the bar, not the result
		frappe.set_user(CASHIER)  # the function's own refusal still applies, and is what the screen is told
		with self.assertRaises(frappe.PermissionError):
			progress.start("estimate_unread", {"cycle": cycle.name})
		frappe.set_user("Administrator")
		p = self.job("estimate_unread", cycle.name)
		self.assertEqual(p["state"], "Failed")
		self.assertIn("Billing Officer or Billing Manager", p["error"])

	def test_print_bills_to_a_file_on_the_run(self):
		run = self.calculated()
		out = bill_print.print_run_bills_to_file(run.name)
		self.assertEqual((out["bills"], out["file_name"]), (1, f"{run.name}.pdf"))
		bill_print.print_run_bills_to_file(run.name)
		again = bill_print.print_run_bills_to_file(run.name)  # an earlier copy is replaced, also one given a number for its name
		files = frappe.get_all("File", filters={"attached_to_doctype": "Bill Run", "attached_to_name": run.name}, fields=["file_url", "is_private"])
		self.assertEqual(files, [{"file_url": again["file_url"], "is_private": 1}])
		self.assertTrue(frappe.get_doc("File", {"file_url": again["file_url"]}).get_content().startswith(b"%PDF"))

	def test_a_run_is_reversed_in_the_background(self):
		run = self.post(self.calculated())
		frappe.db.set_value("Bill Run", run.name, "cancellation_reason", "Run reversed: wrong readings")
		frappe.set_user(BILLING_MANAGER)
		progress.start("run_cancel", {"run": run.name}, "Reversing")
		p = self.job("run_cancel", run.name)
		frappe.set_user("Administrator")
		self.assertEqual((p["state"], p["result"]["workflow_state"]), ("Done", "Cancelled"))
		self.assertEqual(frappe.db.get_value("Bill Run", run.name, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Utility Bill", {"bill_run": run.name}, "docstatus"), 2)
