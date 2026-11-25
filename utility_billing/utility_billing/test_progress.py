# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 54: one progress store for every background job, and a progress bar for a bill run's posting."""

import frappe

from utility_billing.utility_billing import billing, progress
from utility_billing.utility_billing.doctype.bill_run import bill_run
from utility_billing.utility_billing.test_billing import BillingCase
from utility_billing.utility_billing.testing import BILLING_OFFICER


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
