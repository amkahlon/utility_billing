# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for defaulters and recovery (increment 11; FS-REC-02, 04-08, 15, FS-RPT-24).
Spec test case 1 supplies the account: after the October bill, house N 2308 owes 22,260 + 1,104 = 23,364, and the
1,104 has been overdue since the September due date, 15-10-2026."""

from datetime import date
from unittest.mock import patch

import frappe
from frappe.utils import add_days

from utility_billing.utility_billing import mis, recovery
from utility_billing.utility_billing.test_billing import SOC, BillingCase
from utility_billing.utility_billing.testing import (
	BILLING_MANAGER, CASHIER, GENERAL_MANAGER, RECOVERY_OFFICER,
)

SEPT_DUE = date(2026, 10, 15)
STAGES = [("Reminder", 15, "Print"), ("Notice", 30, "SMS"), ("Final Notice", 45, "Print"), ("Disconnection Order", 60, "Print")]


class IntegrationTestRecovery(BillingCase):
	def setUp(self):
		super().setUp()
		# an SMS notice needs a number to go to, or it fails visibly instead of waiting in the queue
		frappe.db.set_value("Consumer", frappe.db.get_value("Connection", self.conn, "consumer"), "mobile_no", "0300-1234567")
		self.set_rules(10_000, 60)
		self.october_posted()

	def october_posted(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.post(self.make_run("2026-10-01", "2026-11-01"))

	def set_rules(self, amount, days, stages=STAGES):
		s = frappe.get_doc("Society", SOC)
		s.defaulter_min_amount, s.defaulter_min_days = amount, days
		s.set("recovery_stages", [{"stage": a, "days_overdue": b, "channel": c, "message": f"{a}: {{consumer}} owes {{owed}} for {{days}} days ({{reference}})."}
		                          for a, b, c in stages])
		s.save()

	def on(self, days_after_sept_due):
		return add_days(SEPT_DUE, days_after_sept_due)

	def row(self, as_of, **kw):
		rows = recovery.accounts(SOC, as_of, **kw)
		return rows[0] if rows else None

	def run_on(self, as_of):
		run = frappe.get_doc({"doctype": "Recovery Run", "society": SOC, "as_of": as_of})
		recovery.prepare_run(run)
		run.insert()
		return run

	def issue_on(self, as_of):
		run = self.run_on(as_of)
		run.issue()
		run.reload()
		return run

	def officer_case(self, **kw):
		return frappe.get_doc({"doctype": "Recovery Case", "society": SOC, "connection": self.conn, "assigned_to": RECOVERY_OFFICER, **kw})

	# ---- defaulters (REC-04)
	def test_defaulter_rules_come_from_the_society(self):
		r = self.row(self.on(66))
		self.assertEqual((r["owed"], r["overdue"], r["oldest"], r["defaulter"]), (23_364, 23_364, 66, True))
		self.assertEqual((r["block"], r["consumer_name"]), ("(none)", "House N 2308 Owner"))
		r = self.row(self.on(41))  # 41 days old: overdue and big enough to chase, but not yet a defaulter
		self.assertEqual((r["oldest"], r["defaulter"]), (41, False))
		self.assertIsNone(self.row(self.on(66), category="Some Other Category"))
		self.set_rules(30_000, 60)  # owes less than the defaulter amount
		self.assertIsNone(self.row(self.on(66)))

	def test_nothing_overdue_is_not_listed_and_paying_clears_the_list(self):
		self.assertIsNone(self.row("2026-10-12"))  # before September's due date: nothing overdue (and under the amount anyway)
		self.assertEqual(self.row("2026-11-10")["overdue"], 1_104)  # only the September remainder is overdue; October is due 15-11
		self.assertEqual(self.row(self.on(66))["connection"], self.conn)
		self.pay(23_364, "2026-11-12")
		self.assertIsNone(self.row(self.on(66)))

	def test_block_filter_and_report_columns(self):
		frappe.db.set_value("Property", self.prop, "block", "N Block")
		self.assertEqual(self.row(self.on(66), block="N Block")["block"], "N Block")
		self.assertIsNone(self.row(self.on(66), block="Z Block"))
		cols, data, *_ = mis.run("defaulters", {"society": SOC, "as_of": str(self.on(66)), "min_amount": 10000, "min_days": 60})
		self.assertIn("next_stage", [c["fieldname"] for c in cols])
		first = data[0]
		self.assertEqual((first["connection"], first["next_stage"], first["last_stage"]), (self.conn, "Reminder", None))

	# ---- stages and notices (REC-05, 06)
	def test_stages_are_issued_one_at_a_time_in_order_and_each_is_recorded(self):
		as_of = self.on(66)
		seen = []
		for _ in STAGES:
			run = self.issue_on(as_of)
			self.assertEqual((run.status, run.accounts_due, run.notices_issued), ("Issued", 1, 1))
			n = frappe.get_doc("Recovery Notice", run.items[0].notice)
			seen.append((n.stage, n.status, n.channel, n.issue_date.isoformat(), n.owed, n.oldest_days))
		self.assertEqual(seen, [("Reminder", "Issued", "Print", str(as_of), 23_364, 66), ("Notice", "Queued", "SMS", str(as_of), 23_364, 66),
		                        ("Final Notice", "Issued", "Print", str(as_of), 23_364, 66),
		                        ("Disconnection Order", "Awaiting Approval", "Print", str(as_of), 23_364, 66)])
		self.assertEqual(self.run_on(as_of).accounts_due, 0)  # every stage has been issued
		r = self.row(as_of)
		self.assertEqual((r["last_stage"], r["next_stage"]), ("Disconnection Order", None))

	def test_a_stage_only_comes_due_after_its_days(self):
		self.set_rules(10_000, 60, [("Reminder", 40, "Print")])
		run = self.run_on(self.on(36))  # 36 days overdue: big enough to chase, under the 40 days of the stage
		self.assertEqual((run.accounts_checked, run.accounts_due), (1, 0))
		self.assertEqual(self.run_on(self.on(40)).items[0].stage, "Reminder")

	def test_notice_text_names_the_consumer_amount_and_days(self):
		run = self.issue_on(self.on(66))
		n = frappe.get_doc("Recovery Notice", run.items[0].notice)
		ref = frappe.db.get_value("Connection", self.conn, "reference_no")
		self.assertEqual(n.message, f"Reminder: House N 2308 Owner owes Rs 23,364 for 66 days ({ref}).")

	def test_unticked_accounts_get_no_notice_and_an_issued_run_is_locked(self):
		run = self.run_on(self.on(66))
		run.items[0].include = 0
		run.save()
		with self.assertRaises(frappe.ValidationError):
			run.issue()
		run.items[0].include = 1
		run.save()
		run.issue()
		run.reload()
		with self.assertRaises(frappe.ValidationError):
			run.issue()  # already issued
		again = frappe.get_doc("Recovery Run", run.name)
		again.block = "X"
		with self.assertRaises(frappe.ValidationError):
			again.save()

	def test_a_position_that_changed_since_preparing_is_skipped_not_duplicated(self):
		first, second = self.run_on(self.on(66)), self.run_on(self.on(66))
		first.issue()
		second.reload()
		second.issue()  # the same Reminder was issued by the first run; the second must not send it again
		second.reload()
		self.assertEqual(second.notices_issued, 0)
		self.assertEqual(frappe.db.count("Recovery Notice", {"connection": self.conn, "stage": "Reminder"}), 1)

	def test_cancelling_a_notice_lets_the_stage_be_issued_again(self):
		run = self.issue_on(self.on(66))
		self.assertEqual(self.row(self.on(66))["next_stage"], "Notice")
		with self.assertRaises(frappe.ValidationError):
			recovery.cancel_notice(run.items[0].notice, "  ")
		recovery.cancel_notice(run.items[0].notice, "Sent to the wrong address")
		r = self.row(self.on(66))
		self.assertEqual((r["next_stage"], r["last_stage"]), ("Reminder", None))

	def test_stage_history_is_kept_after_the_account_is_cleared(self):
		self.issue_on(self.on(66))  # Reminder, dated 2026-12-20
		self.pay(23_364, "2026-11-12")
		self.assertIsNone(self.row(self.on(67)))  # cleared: not listed
		self.assertEqual(recovery.notice_history(SOC)[self.conn], [("Reminder", date(2026, 12, 20))])  # but the record stays

	def test_disconnection_order_needs_a_second_person(self):
		self.set_rules(10_000, 60, [("Disconnection Order", 20, "Print")])
		run = self.issue_on(self.on(66))
		name = run.items[0].notice
		self.assertEqual(frappe.db.get_value("Recovery Notice", name, "status"), "Awaiting Approval")
		with self.assertRaises(frappe.PermissionError):
			recovery.approve_disconnection(name)  # the Administrator issued it
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			recovery.approve_disconnection(name)
		frappe.set_user(GENERAL_MANAGER)
		n = recovery.approve_disconnection(name)
		frappe.set_user("Administrator")
		self.assertEqual((n.status, n.approved_by), ("Issued", GENERAL_MANAGER))
		with self.assertRaises(frappe.ValidationError):
			recovery.approve_disconnection(name)  # already approved

	def test_queued_notices_can_be_marked_sent_or_failed(self):
		self.set_rules(10_000, 60, [("Reminder", 15, "SMS")])
		n = frappe.get_doc("Recovery Notice", self.issue_on(self.on(66)).items[0].notice)
		self.assertEqual(n.status, "Queued")
		self.assertEqual(recovery.mark_notice(n.name, "Failed").status, "Failed")
		self.assertEqual(recovery.mark_notice(n.name, "Sent").status, "Sent")
		with self.assertRaises(frappe.ValidationError):
			recovery.mark_notice(n.name, "Failed")
		with self.assertRaises(frappe.ValidationError):
			recovery.cancel_notice(n.name, "too late")

	def test_the_stage_rules_must_be_sensible(self):
		s = frappe.get_doc("Society", SOC)
		s.set("recovery_stages", [{"stage": "Notice", "days_overdue": 30, "channel": "Print"}, {"stage": "Final Notice", "days_overdue": 20, "channel": "Print"}])
		with self.assertRaises(frappe.ValidationError):
			s.save()

	def test_bulk_print_covers_the_printable_notices_of_a_run(self):
		run = self.issue_on(self.on(66))
		html = recovery.notice_html([run.items[0].notice])
		for text in ("Payment Reminder", "House N 2308 Owner", "23,364", run.items[0].notice):
			self.assertIn(text, html)
		self.assertIn("Billing Test Society", html)

	# ---- cases, follow-ups, holds (REC-08, 15)
	def test_assigning_opens_one_case_per_account(self):
		frappe.set_user(RECOVERY_OFFICER)
		self.assertEqual(recovery.assign_cases(SOC, [self.conn], RECOVERY_OFFICER), {"opened": 1, "reassigned": 0})
		self.assertEqual(recovery.assign_cases(SOC, [self.conn], RECOVERY_OFFICER), {"opened": 0, "reassigned": 1})
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Recovery Case", {"connection": self.conn}), 1)
		with self.assertRaises(frappe.ValidationError):
			self.officer_case().insert()  # one open case per account
		self.assertEqual(self.row(self.on(66))["assigned_to"], RECOVERY_OFFICER)

	def test_only_a_recovery_officer_can_be_assigned_and_only_runners_assign(self):
		with self.assertRaises(frappe.ValidationError):
			recovery.assign_cases(SOC, [self.conn], BILLING_MANAGER)
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			recovery.assign_cases(SOC, [self.conn], RECOVERY_OFFICER)

	def test_a_promise_to_pay_holds_recovery_until_the_date_passes(self):
		case = self.officer_case(followups=[{"followup_date": self.on(66), "kind": "Promise to Pay", "promise_date": self.on(70),
		                                     "promise_amount": 10_000, "outcome": "Will pay after salary"}]).insert()
		self.assertEqual((case.status, str(case.promise_date), case.promise_amount), ("Promise to Pay", str(self.on(70)), 10_000))
		self.assertEqual(case.followups[0].done_by, "Administrator")
		r = self.row(self.on(66))
		self.assertEqual((r["hold"], r["next_stage"], r["promise"]), ("Promise to pay not yet due", None, "Promised"))
		self.assertEqual(self.run_on(self.on(66)).accounts_due, 0)
		r = self.row(self.on(71))  # the promised date has passed without payment
		self.assertEqual((r["hold"], r["promise"], r["next_stage"]), (None, "Broken", "Reminder"))

	def test_a_promise_needs_a_date_and_an_amount(self):
		with self.assertRaises(frappe.ValidationError):
			self.officer_case(followups=[{"followup_date": self.on(66), "kind": "Promise to Pay", "promise_amount": 5000}]).insert()
		with self.assertRaises(frappe.ValidationError):
			self.officer_case(followups=[{"followup_date": self.on(66), "kind": "Promise to Pay", "promise_date": self.on(70)}]).insert()

	def test_a_visit_or_call_does_not_hold_recovery(self):
		self.officer_case(followups=[{"followup_date": self.on(66), "kind": "Visit", "outcome": "Not at home"}]).insert()
		r = self.row(self.on(66))
		self.assertEqual((r["hold"], r["case_status"], r["next_stage"]), (None, "Open", "Reminder"))

	def test_a_legal_case_holds_recovery_until_the_case_is_closed(self):
		case = self.officer_case(status="Legal Case")
		with self.assertRaises(frappe.ValidationError):
			case.insert()  # a case number is required
		case.legal_case_no, case.court = "CS-114/2026", "Civil Court Lahore"
		case.insert()
		r = self.row(self.on(200))
		self.assertEqual((r["hold"], r["next_stage"]), ("Legal case open", None))
		case.status = "Closed"
		with self.assertRaises(frappe.ValidationError):
			case.save()  # a reason is required
		case = frappe.get_doc("Recovery Case", case.name)
		case.status = "Closed"
		case.close_reason = "Settled out of court"
		case.save()
		self.assertEqual(str(case.closed_on), frappe.utils.nowdate())
		r = self.row(self.on(200))
		self.assertEqual((r["hold"], r["case_status"], r["next_stage"]), (None, None, "Reminder"))
		self.assertEqual(frappe.db.count("Recovery Case", {"connection": self.conn}), 1)  # kept for the record
		self.officer_case().insert()  # a new case can be opened once the old one is closed

	def test_a_case_title_and_details_follow_the_account(self):
		frappe.db.set_value("Property", self.prop, "block", "N Block")
		case = self.officer_case().insert()
		self.assertEqual((case.consumer_name, case.block, case.status), ("House N 2308 Owner", "N Block", "Open"))
		self.assertIn("House N 2308 Owner", case.case_title)

	def test_an_issued_notice_updates_the_case(self):
		case = self.officer_case().insert()
		self.issue_on(self.on(66))
		case.reload()
		self.assertEqual((case.last_stage, str(case.last_stage_date)), ("Reminder", str(self.on(66))))
		self.assertEqual(frappe.db.get_value("Recovery Notice", {"connection": self.conn}, "recovery_case"), case.name)

	# ---- target (REC-07)
	def test_recovery_against_a_monthly_target(self):
		frappe.get_doc({"doctype": "Recovery Target", "society": SOC, "month": "2026-11-20", "target_amount": 2_000}).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Recovery Target", "society": SOC, "month": "2026-11-05", "target_amount": 1}).insert()  # one per month
		self.assertEqual(recovery.target_progress(SOC, "2026-11-10")["percent"], 0.0)
		self.pay(23_364, "2026-11-12")  # pays the current bill first, then the 1,104 of arrears
		p = recovery.target_progress(SOC, "2026-11-10")
		self.assertEqual((p["target"], p["recovered"], p["percent"], p["remaining"], p["reached"]), (2_000, 1_104, 55.2, 896, False))
		self.assertEqual(recovery.target_progress(SOC, "2026-12-10")["percent"], None)  # no target for December

	def test_a_reversed_payment_does_not_count_as_recovered(self):
		r = self.pay(23_364, "2026-11-12")
		self.assertEqual(recovery.recovered_in_month(SOC, "2026-11-01"), 1_104)
		from utility_billing.utility_billing import account

		frappe.set_user(BILLING_MANAGER)
		account.reverse_document("Payment Receipt", r.name, "CHQ-BOUNCE", "Cheque returned unpaid")
		frappe.set_user("Administrator")
		self.assertEqual(recovery.recovered_in_month(SOC, "2026-11-01"), 0)

	# ---- page data, reports, access
	def test_page_data(self):
		self.issue_on(self.on(66))
		d = recovery.get_recovery(society=SOC, as_of=str(self.on(66)))
		c = d["cards"]
		self.assertEqual((c["defaulters"], c["defaulter_amount"], c["notices_due"], c["due_by_stage"]), (1, 23_364, 1, {"Notice": 1}))
		self.assertEqual(c["issued_this_month"], {"Reminder": 1})
		self.assertEqual([s["stage"] for s in d["rules"]["stages"]], list(e for e, _d, _c in STAGES))
		self.assertEqual((d["rows"][0]["connection"], d["rows"][0]["last_stage"], d["rows"][0]["next_stage"]), (self.conn, "Reminder", "Notice"))
		self.assertEqual(recovery.get_recovery(society=SOC, as_of=str(self.on(41)), show="defaulters")["rows"], [])
		self.assertEqual(len(recovery.get_recovery(society=SOC, as_of=str(self.on(41)), show="all")["rows"]), 1)

	def test_reports(self):
		run = self.issue_on(self.on(66))
		cols, data, *_ = mis.run("recovery_notices", {"society": SOC, "from_date": "2026-12-01", "to_date": "2026-12-31"})
		self.assertEqual([(r["stage"], r["status"]) for r in data], [("Reminder", "Issued")])
		cols, data, *_ = mis.run("disconnection_list", {"society": SOC})
		self.assertEqual(data, [])
		for _ in range(3):
			self.issue_on(self.on(66))
		cols, data, *_ = mis.run("disconnection_list", {"society": SOC})
		self.assertEqual((data[0]["connection"], data[0]["status"], data[0]["owed"]), (self.conn, "Awaiting Approval", 23_364))
		frappe.get_doc({"doctype": "Recovery Target", "society": SOC, "month": "2026-12-01", "target_amount": 5_000}).insert()
		cols, data, msg, chart, summary = mis.run("recovery_vs_target", {"society": SOC, "year": 2026})
		self.assertEqual([r for r in data if r["month"] == "Dec 2026"][0]["target"], 5_000)
		self.assertEqual(run.status, "Issued")

	def test_the_dashboard_points_to_recovery(self):
		from utility_billing.utility_billing import dashboard

		with patch.object(dashboard, "nowdate", return_value=str(self.on(66))):
			att = dashboard.get_dashboard(society=SOC, month="2026-12-01")["attention"]
		item = next(i for i in att if "60+ days overdue" in i["title"])
		self.assertEqual(item["route"], ["ub-recovery"])
		for _ in STAGES:
			self.issue_on(self.on(66))
		with patch.object(dashboard, "nowdate", return_value=str(self.on(66))):
			att = dashboard.get_dashboard(society=SOC, month="2026-12-01")["attention"]
		order = next(i for i in att if "disconnection order" in i["title"])
		self.assertEqual((order["route"], order["options"]["status"]), (["List", "Recovery Notice"], "Awaiting Approval"))

	def test_access(self):
		for user in (CASHIER,):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				recovery.get_recovery(society=SOC)
			with self.assertRaises(frappe.PermissionError):
				recovery.new_run(SOC)
		frappe.set_user(RECOVERY_OFFICER)
		self.assertEqual(recovery.get_recovery(society=SOC, as_of=str(self.on(66)))["can_run"], True)
		name = recovery.new_run(SOC, str(self.on(66)))
		self.assertEqual(frappe.db.get_value("Recovery Run", name, "accounts_due"), 1)
		frappe.set_user("Administrator")

	def test_the_page_and_menu_are_in_place(self):
		page = frappe.get_doc("Page", "ub-recovery")
		self.assertEqual((page.standard, page.module), ("Yes", "Utility Billing"))
		self.assertIn("Recovery Officer", {r.role for r in page.roles})
		links = {i.link_to for i in frappe.get_doc("Sidebar", "Utility Billing").items}
		self.assertTrue({"ub-recovery", "Recovery Case", "Recovery Run", "Recovery Notice", "Recovery Target"} <= links)
