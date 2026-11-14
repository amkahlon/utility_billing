# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Integration tests for the core MIS reports (increment 9). They run on spec test case 1 (house N 2308):
September bill 59,470 paid 58,366 on 10-10-2026, October bill 22,260 + arrears 1,104 = 23,364."""

import json
from pathlib import Path

import frappe

from utility_billing.install import create_default_kpis, create_default_reason_codes
from utility_billing.utility_billing import account, mis, readings
from utility_billing.utility_billing.doctype.cashier_day.cashier_day import close_day
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import (
	AUDITOR,
	BILLING_MANAGER,
	BM_AND_GM,
	CASHIER,
	GENERAL_MANAGER,
	MANAGEMENT,
	act,
)

WIDE = {"from_date": "2026-01-01", "to_date": "2026-12-31"}


class IntegrationTestMIS(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_reason_codes()
		frappe.db.set_value("Property", self.prop, "block", "N Block")
		if not frappe.db.exists("Cash Counter", "TB1"):
			frappe.get_doc({"doctype": "Cash Counter", "counter_code": "TB1", "counter_name": "Test counter", "society": SOC}).insert()

	# ----- helpers -----
	def october_posted(self, exempt=()):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30, exempt=exempt)
		run = self.post(self.make_run("2026-10-01", "2026-11-01"))
		return run, self.bill(run)

	def report(self, key, **filters):
		return mis.REPORTS[key]["fn"](frappe._dict({"society": SOC, **filters}))

	def rows(self, key, **filters):
		return self.report(key, **filters)["data"]

	def open_day(self, opening=1000):
		frappe.set_user(CASHIER)
		try:
			return frappe.get_doc({"doctype": "Cashier Day", "cash_counter": "TB1", "opening_float": opening}).insert()
		finally:
			frappe.set_user("Administrator")

	def counter_receipt(self, amount, mode="Cash", **kw):
		frappe.set_user(CASHIER)
		try:
			r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Counter",
			                    "amount": amount, "mode": mode, **kw}).insert()
			r.submit()
			return r
		finally:
			frappe.set_user("Administrator")

	def waive_surcharge(self, amount):
		frappe.set_user(BM_AND_GM)
		note = frappe.get_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": self.conn, "bucket": "Surcharge",
		                       "amount": amount, "reason_code": "SUR-WAIVE", "remarks": "Partial waiver"}).insert()
		frappe.set_user("Administrator")
		act(note, "Submit for Approval", BM_AND_GM)
		act(note, "Approve", GENERAL_MANAGER)
		return note

	# ----- 1. dashboard -----
	def test_dashboard(self):
		self.october_posted()
		out = self.report("dashboard", month="2026-10-15")
		sep, octo = out["data"][-2], out["data"][-1]
		self.assertEqual(len(out["data"]), 13)
		self.assertEqual((sep["month"], sep["billed"], sep["payable"], sep["collected"], sep["rate"]), ("Sep 2026", 59470, 59470, 0, 0))
		self.assertEqual((octo["month"], octo["bills"], octo["units"], octo["billed"], octo["payable"], octo["collected"]),
		                 ("Oct 2026", 1, 264, 22260, 23364, 58366))
		self.assertEqual(octo["rate"], 249.8)  # arrears of September collected in October
		cards = {c["label"]: c["value"] for c in out["summary"]}
		self.assertEqual(cards["Billed this month"], 22260)
		self.assertEqual(cards["Total owed by consumers"], 23364)
		self.assertEqual(cards["Of which arrears, surcharge and fees"], 1104)
		self.assertEqual((cards["Accounts in arrears"], cards["Credit held for consumers"]), (1, 0))
		self.assertEqual(out["chart"]["data"]["datasets"][0]["values"][-1], 22260)
		self.assertEqual(len(out["chart"]["data"]["labels"]), 13)

	# ----- 2. collection vs billing -----
	def test_collection_vs_billing(self):
		self.october_posted()
		r = self.rows("collection_vs_billing", from_date="2026-10-01", to_date="2026-10-31")
		self.assertEqual((r[0]["category"], r[0]["block"], r[0]["bills"], r[0]["billed"], r[0]["collected"], r[0]["rate"]),
		                 ("TST Billing", "N Block", 1, 59470, 58366, 98.1))
		self.assertEqual((r[-1]["category"], r[-1]["billed"], r[-1]["collected"]), ("Total", 59470, 58366))
		r = self.rows("collection_vs_billing", from_date="2026-11-01", to_date="2026-11-30")
		self.assertEqual((r[0]["billed"], r[0]["payable"], r[0]["collected"], r[0]["rate"]), (22260, 23364, 0, 0))

	# ----- 3. billing summary -----
	def test_billing_summary(self):
		self.october_posted()
		r = self.rows("billing_summary", from_date="2026-10-01", to_date="2026-10-31")
		self.assertEqual([(x["item"], x["amount"]) for x in r],
		                 [("Energy Charges", 18480), ("FPA Charges", 1080), ("Service Charges", 1700), ("Aquifer Charges", 1000), ("Total", 22260)])
		self.assertEqual((r[0]["bills"], r[0]["units"], r[1]["units"]), (1, 264, 831))  # FPA is charged on August's 831 units
		c = self.rows("billing_summary", from_date="2026-10-01", to_date="2026-10-31", group_by="Category")
		self.assertEqual([(x["item"], x["bills"], x["units"], x["amount"]) for x in c], [("TST Billing", 1, 264, 22260), ("Total", 1, 264, 22260)])
		b = self.rows("billing_summary", from_date="2026-09-01", to_date="2026-10-31", group_by="Block")
		self.assertEqual([(x["item"], x["bills"], x["amount"]) for x in b], [("N Block", 2, 81730), ("Total", 2, 81730)])

	# ----- 4. bill register -----
	def test_bill_register(self):
		run, bill = self.october_posted()
		r = self.rows("bill_register", bill_run=run.name)
		self.assertEqual((r[0]["bill"], r[0]["consumer_name"], r[0]["block"], r[0]["units"]), (bill.name, "House N 2308 Owner", "N Block", 264))
		self.assertEqual((r[-1]["current_amount"], r[-1]["arrears"], r[-1]["payable_within_due"], r[-1]["surcharge_amount"],
		                  r[-1]["payable_after_due"]), (22260, 1104, 23364, 1781, 25145))
		lines = self.rows("bill_register", bill_run=run.name, show_lines=1)
		self.assertEqual([(l["label"], l["amount"]) for l in lines],
		                 [("Energy Charges", 18480), ("FPA Charges", 1080), ("Service Charges", 1700), ("Aquifer Charges", 1000)])
		self.assertIn("August 2026 units 831", lines[1]["note"])

	# ----- 5. sales -----
	def test_sales_by_category_tariff_and_slab(self):
		self.october_posted()
		frappe.db.set_value("Society", SOC, "energy_charge_type", "TST Energy")
		r = self.rows("sales", from_date="2026-10-01", to_date="2026-10-31")
		self.assertEqual((r[0]["category"], r[0]["bills"], r[0]["units"], r[0]["energy"], r[0]["amount"], r[0]["avg_rate"]),
		                 ("TST Billing", 1, 264, 18480, 22260, 70))
		s = self.rows("sales", from_date="2026-10-01", to_date="2026-10-31", show="Units by Slab")
		self.assertEqual((s[0]["slab"], s[0]["rate"], s[0]["units"], s[0]["amount"]), ("Flat rate", 70, 264, 18480))
		self.assertEqual(s[-1]["units"], 264)

	# ----- 6. FPA / QTA register -----
	def test_fpa_register(self):
		self.october_posted()
		r = self.rows("fpa_qta_register", adjustment_type="FPA", **WIDE)
		octo = r[-1]
		self.assertEqual((octo["type"], str(octo["billing_month"]), str(octo["reference_month"]), octo["rate"], octo["category"]),
		                 ("FPA", "2026-10-01", "2026-08-01", 1.3, "TST Billing"))
		self.assertEqual((octo["bills"], octo["units"], octo["amount"], octo["exempt"], octo["approved_by"]), (1, 831, 1080, 0, GENERAL_MANAGER))
		self.assertEqual((r[0]["rate"], r[0]["amount"]), (0, 0))  # September's FPA at Rs 0

	def test_fpa_register_counts_exemptions(self):
		self.october_posted(exempt=(self.cat,))
		octo = self.rows("fpa_qta_register", adjustment_type="FPA", **WIDE)[-1]
		self.assertEqual((octo["bills"], octo["units"], octo["amount"], octo["exempt"]), (1, 0, 0, 1))

	# ----- 7 and 8. adjustments and surcharge -----
	def test_adjustments_and_surcharge_registers(self):
		run, bill = self.october_posted()
		account.apply_surcharges(as_of="2026-11-16", society=SOC)
		note = self.waive_surcharge(781)
		adj = self.rows("adjustments_register", **WIDE)
		self.assertEqual((adj[0]["name"], adj[0]["note_type"], adj[0]["bucket"], adj[0]["amount"], adj[0]["signed"]),
		                 (note.name, "Credit Note", "Surcharge", 781, -781))
		self.assertEqual((adj[0]["reason"], adj[0]["maker"], adj[0]["approved_by"]), ("Surcharge waived by management", BM_AND_GM, GENERAL_MANAGER))
		self.assertEqual(adj[-1]["signed"], -781)
		self.assertEqual(self.rows("adjustments_register", note_type="Debit Note", **WIDE), [])

		# Under the strict rule September (Rs 1,104 unpaid at its due date) is surcharged too: 8% of 59,470 = 4,758.
		# Rows follow transaction dates, and the waiver is dated today, before the surcharge the test dates 16-11.
		sur = self.rows("surcharge_register", **WIDE)
		self.assertEqual([(r["kind"], r["amount"]) for r in sur],
		                 [("Levied", 4758), ("Waived", -781), ("Levied", 1781), ("Levied", 6539), ("Waived or reversed", -781),
		                  ("Net surcharge", 5758)])
		self.assertEqual((sur[1]["approved_by"], sur[1]["reason_code"]), (GENERAL_MANAGER, "SUR-WAIVE"))

	# ----- 9 and 10. readings -----
	def test_reading_completion_and_exceptions(self):
		self.october_posted()
		cycle = f"{SOC}-Electricity-2026-10"
		by_route = self.rows("reading_completion", reading_cycle=cycle)
		self.assertEqual((by_route[0]["item"], by_route[0]["meters"], by_route[0]["done"], by_route[0]["rate"]), ("(no route)", 1, 1, 100))
		by_reader = self.rows("reading_completion", reading_cycle=cycle, group_by="Reader")
		self.assertEqual((by_reader[0]["item"], by_reader[0]["entered"], by_reader[0]["days"], by_reader[0]["per_day"]), ("Administrator", 1, 1, 1.0))
		# A November sheet nobody has read yet.
		nov = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-11-01"}).insert()
		readings.create_reading_sheet(nov.name)
		pending = self.rows("reading_completion", reading_cycle=nov.name)
		self.assertEqual((pending[0]["meters"], pending[0]["pending"], pending[0]["done"], pending[0]["rate"]), (1, 1, 0, 0))
		exc = self.rows("reading_exceptions", reading_cycle=nov.name)
		self.assertEqual([(r["connection"], r["status"]) for r in exc], [(self.conn, "Pending")])
		self.assertEqual(self.report("reading_completion")["message"] is not None, True)  # defaults to the latest cycle

	# ----- 11. estimated bills -----
	def test_estimated_bills_and_streak(self):
		run, bill = self.october_posted()
		for month in ("2026-09-01", "2026-10-01"):
			name = frappe.db.get_value("Meter Reading", {"connection": self.conn, "billing_month": month})
			frappe.db.set_value("Meter Reading", name, {"status": "Estimated", "consumption_type": "Estimated"})
		r = self.rows("estimated_bills", month="2026-10-20")
		self.assertEqual((len(r), r[0]["months_running"], r[0]["flag"], r[0]["bill"], r[0]["current_amount"]), (1, 2, "2+ months", bill.name, 22260))
		self.assertEqual(len(self.rows("estimated_bills", month="2026-10-01", repeat_only=1)), 1)
		self.assertEqual(self.rows("estimated_bills", month="2026-09-01", repeat_only=1), [])  # only 1 month by September
		self.assertEqual(self.rows("estimated_bills", month="2026-08-01"), [])

	# ----- 12. meter register -----
	def test_meter_register(self):
		self.october_posted()
		out = self.report("meter_register")
		m = out["data"][0]
		self.assertEqual((m["serial_no"], m["status"], m["connection"], str(m["installed_on"])), ("TST-BIL-M1", "Installed", self.conn, "2026-07-01"))
		self.assertGreaterEqual(m["age_months"], 4)
		self.assertEqual({c["label"]: c["value"] for c in out["summary"]}["Installed"], 1)
		self.assertEqual(self.rows("meter_register", status="Defective"), [])

	# ----- 13, 14, 15. collections, cashier closing, reversals -----
	def test_collections_cashier_closing_and_reversals(self):
		self.october_posted()
		day = self.open_day()
		cash = self.counter_receipt(1000)
		self.counter_receipt(500, mode="Cheque", instrument_no="778899")
		by_day = {str(r["item"]): r for r in self.rows("collections", **WIDE)}
		self.assertEqual(by_day["2026-10-10"]["amount"], 58366)
		self.assertEqual(by_day[frappe.utils.nowdate()]["amount"], 1500)
		by_counter = {r["item"]: r["amount"] for r in self.rows("collections", group_by="Counter", **WIDE)}
		self.assertEqual((by_counter["TB1"], by_counter["(bank / channel)"]), (1500, 58366))
		by_mode = {r["item"]: r["amount"] for r in self.rows("collections", group_by="Payment Mode", **WIDE)}
		self.assertEqual((by_mode["Cash"], by_mode["Cheque"], by_mode["Bank Transfer"]), (1000, 500, 58366))
		self.assertEqual({r["item"] for r in self.rows("collections", group_by="Channel", **WIDE)}, {"Counter", "Bank / Channel", "Total"})
		self.assertEqual(self.rows("collections", group_by="Cashier", **WIDE)[0]["item"] in (CASHIER, "Administrator"), True)

		frappe.set_user(CASHIER)
		close_day(day.name, 2000, "DS-9")
		frappe.set_user("Administrator")
		closing = self.rows("cashier_closing", **WIDE)
		self.assertEqual((closing[0]["name"], closing[0]["cash_counter"], closing[0]["status"], closing[0]["receipt_count"]), (day.name, "TB1", "Closed", 2))
		self.assertEqual((closing[0]["cash_total"], closing[0]["cheque_total"], closing[0]["expected_cash"], closing[0]["cash_counted"],
		                  closing[0]["difference"], closing[0]["deposit_slip_no"]), (1000, 500, 2000, 2000, 0, "DS-9"))
		self.assertEqual(self.rows("cashier_closing", status="Approved", **WIDE), [])

		frappe.set_user(BM_AND_GM)
		account.reverse_document("Payment Receipt", cash.name, "WRONG-ACC", "Entered on the wrong account")
		frappe.set_user("Administrator")
		rev = self.rows("payment_reversals", **WIDE)
		self.assertEqual((rev[0]["kind"], rev[0]["document"], rev[0]["amount"], rev[0]["reason"], rev[0]["reversed_by"]),
		                 ("Payment", cash.name, 1000, "WRONG-ACC", BM_AND_GM))
		by_counter = {r["item"]: r for r in self.rows("collections", group_by="Counter", **WIDE)}
		self.assertEqual((by_counter["TB1"]["amount"], by_counter["TB1"]["reversed"], by_counter["TB1"]["reversed_amount"]), (500, 1, 1000))

	# ----- 16, 17. ageing and defaulters -----
	def test_arrears_ageing_and_defaulters(self):
		self.october_posted()
		age = self.rows("arrears_ageing", as_of="2026-12-20")
		self.assertEqual((age[0]["block"], age[0]["category"], age[0]["accounts"]), ("N Block", "TST Billing", 1))
		self.assertEqual((age[0]["31-60"], age[0]["61-90"], age[0]["0-30"], age[0]["total"]), (22260, 1104, 0, 23364))
		self.assertEqual(age[-1]["total"], 23364)
		self.assertEqual(self.report("arrears_ageing", as_of="2026-12-20")["chart"]["data"]["datasets"][0]["values"], [0, 22260, 1104, 0, 0])

		d = self.rows("defaulters", as_of="2026-12-20")
		self.assertEqual((d[0]["connection"], d[0]["block"], d[0]["owed"], d[0]["overdue"], d[0]["oldest"], str(d[0]["last_paid"])),
		                 (self.conn, "N Block", 23364, 23364, 66, "2026-10-10"))
		self.assertEqual(self.rows("defaulters", as_of="2026-12-20", min_amount=30000), [])
		self.assertEqual(self.rows("defaulters", as_of="2026-12-20", min_days=70), [])
		self.assertEqual(self.rows("defaulters", as_of="2026-12-20", block="Z Block"), [])
		# On 10-11 the October bill isn't due yet: only September's Rs 1,104 is overdue.
		early = self.rows("defaulters", as_of="2026-11-10")
		self.assertEqual((early[0]["owed"], early[0]["overdue"], early[0]["oldest"]), (23364, 1104, 26))
		self.assertEqual(self.rows("arrears_ageing", as_of="2026-12-20", consumer_category="No Such Category"), [])

	# ----- 18. credit balances -----
	def test_credit_balances(self):
		self.october_posted()
		self.pay(25000, "2026-11-05")
		r = self.rows("credit_balances")
		self.assertEqual((r[0]["connection"], r[0]["credit"], r[0]["owed"], str(r[0]["last_paid"])), (self.conn, 1636, -1636, "2026-11-05"))
		self.assertEqual(r[-1]["credit"], 1636)

	# ----- 19. census -----
	def test_consumer_census(self):
		r = self.rows("consumer_census")
		self.assertEqual((r[0]["category"], r[0]["block"], r[0]["active"], r[0]["total"]), ("TST Billing", "N Block", 1, 1))
		frappe.db.set_value("Connection", self.conn, {"status": "Disconnected", "sanctioned_load_kw": 5})
		r = self.rows("consumer_census")
		self.assertEqual((r[0]["active"], r[0]["disconnected"], r[0]["load_kw"]), (0, 1, 0))  # load counts active connections only

	# ----- 20. pending approvals -----
	def test_pending_approvals(self):
		adj = self.fpa("2026-12-01", 2, approve_it=False)
		self.assertEqual([r for r in self.rows("pending_approvals") if r["document"] == adj.name], [])
		act(adj, "Submit for Approval", BILLING_MANAGER)
		r = [x for x in self.rows("pending_approvals") if x["document"] == adj.name]
		self.assertEqual((r[0]["doctype"], r[0]["requested_by"], r[0]["age"], r[0]["waiting_for"]), ("Tariff Adjustment", BILLING_MANAGER, 0, "General Manager"))
		self.assertEqual(self.rows("pending_approvals", transaction="Bill Run"), [])
		self.assertEqual([x["document"] for x in self.rows("pending_approvals", transaction="Tariff Adjustment")], [adj.name])
		self.assertEqual(self.rows("pending_approvals", older_than=1), [])

	# ----- 21. audit log -----
	def test_audit_log_changes_and_logins(self):
		data = json.dumps({"changed": [["surcharge_rate", 8, 10], ["modified", "a", "b"], ["submitted_snapshot", "x", "y"]],
		                   "added": [["bank_accounts", {}]], "removed": [], "row_changed": [], "updater_reference": None})
		frappe.get_doc({"doctype": "Version", "ref_doctype": "Society", "docname": SOC, "data": data}).insert(ignore_permissions=True)
		today = frappe.utils.nowdate()
		r = self.rows("audit_log", from_date=today, to_date=today, doctype="Society")
		self.assertEqual((r[0]["docname"], r[0]["ref_doctype"], r[0]["summary"]), (SOC, "Society", "Surcharge Rate: 8 → 10; + Bank Accounts"))
		only_tariffs = self.rows("audit_log", from_date=today, to_date=today, doctype="Tariff")  # (other tests may have saved some)
		self.assertTrue(all(x["ref_doctype"] == "Tariff" and x["docname"] != SOC for x in only_tariffs))
		self.assertEqual(mis.version_summary("Tariff", json.dumps({"changed": [], "added": [["approval_log", {}]], "removed": [], "row_changed": []})), "")
		frappe.get_doc({"doctype": "Activity Log", "user": GENERAL_MANAGER, "operation": "Login", "status": "Success",
		                "subject": "General Manager logged in"}).insert(ignore_permissions=True)
		logins = self.rows("audit_log", show="Logins and Logouts", from_date=today, to_date=today, user=GENERAL_MANAGER)
		self.assertEqual((logins[0]["user"], logins[0]["operation"], logins[0]["status"]), (GENERAL_MANAGER, "Login", "Success"))

	# ----- KPI dictionary (RPT-45) -----
	def test_kpi_dictionary(self):
		create_default_kpis()
		kpis = frappe.get_all("KPI Definition", fields=["name", "definition", "formula", "data_source", "refresh", "drill_down", "report"])
		self.assertGreaterEqual(len(kpis), 15)
		for k in kpis:
			for field in ("definition", "formula", "data_source", "refresh", "drill_down"):
				self.assertTrue(k[field], f"{k.name}.{field}")
		by = {k.name: k for k in kpis}
		self.assertEqual(by["COLLECTION_RATE"].report, "Collection vs Billing by Category and Block")
		for k in kpis:
			if k.report:
				self.assertIn(k.report, {s["name"] for s in mis.REPORTS.values()})

	# ----- registration, rights, empty data -----
	def test_every_report_is_registered_with_roles_and_files(self):
		root = Path(frappe.get_app_path("utility_billing", "utility_billing", "report"))
		self.assertEqual(len(mis.REPORTS), 37)  # increment 16 added Bill Delivery Status and Period Close Status
		for key, spec in mis.REPORTS.items():
			doc = frappe.get_doc("Report", spec["name"])
			self.assertEqual((doc.report_type, doc.is_standard, doc.module), ("Script Report", "Yes", "Utility Billing"), spec["name"])
			self.assertEqual({r.role for r in doc.roles}, set(spec["roles"]), spec["name"])
			folder = root / frappe.scrub(spec["name"])
			self.assertIn(f'mis.run("{key}"', (folder / f"{frappe.scrub(spec['name'])}.py").read_text(), spec["name"])
			js = (folder / f"{frappe.scrub(spec['name'])}.js").read_text()
			for flt_ in spec["filters"]:
				self.assertIn(f'"fieldname": "{flt_["fieldname"]}"', js, spec["name"])

	def test_report_filters_are_usable_by_every_role_of_the_report(self):
		"""Found in the visual check: the Audit Log's User and Document Type link filters failed for a Billing Auditor, who
		can't read User or DocType records, although the same report worked over the API. A link filter must point at a
		DocType that every role allowed to open the report can read."""
		for key, spec in mis.REPORTS.items():
			for flt_ in spec["filters"]:
				if flt_["fieldtype"] != "Link":
					continue
				readers = {p.role for p in frappe.get_meta(flt_["options"]).permissions if p.read}
				missing = set(spec["roles"]) - readers
				self.assertFalse(missing, f"{spec['name']}: filter {flt_['label']} ({flt_['options']}) unreadable by {missing}")

	def test_every_report_runs_on_the_test_case_data(self):
		self.october_posted()
		self.pay(500, "2026-11-05")
		filters = {**WIDE, "month": "2026-10-01", "as_of": "2026-12-20"}
		for key, spec in mis.REPORTS.items():
			with self.subTest(report=spec["name"]):
				columns, data, *_rest = mis.run(key, {"society": SOC, **filters})
				self.assertTrue(columns)
				fields = {c["fieldname"] for c in columns}
				for row in data:
					self.assertLessEqual(set(row) - fields - {"doctype", "kind", "signed"}, set(), f"{spec['name']}: row has fields without a column")

	def test_reports_on_a_society_with_no_data(self):
		ensure("Society", "TST-EMPTY", {"society_name": "Empty Society", "short_name": "TST-EMPTY", "energy_charge_type": "TST Energy"})
		for key, spec in mis.REPORTS.items():
			with self.subTest(report=spec["name"]):
				if key in ("reading_completion", "reading_exceptions"):
					with self.assertRaises(frappe.ValidationError):  # no reading cycle yet
						mis.run(key, {"society": "TST-EMPTY"})
					continue
				columns, data, *_rest = mis.run(key, {"society": "TST-EMPTY", **WIDE, "month": "2026-10-01", "as_of": "2026-12-20"})
				self.assertTrue(columns)
				if key not in ("dashboard", "audit_log", "atc_losses", "recovery_vs_target"):  # these always list their months; the audit log isn't per society
					self.assertEqual(data, [], spec["name"])

	def test_report_rights(self):
		from frappe.desk.query_report import run

		self.october_posted()
		allowed = {
			MANAGEMENT: ("Management Dashboard", "Arrears Ageing", "Pending Approvals"),
			AUDITOR: ("Audit Log and User Activity", "Management Dashboard"),
		}
		for user, names in allowed.items():
			frappe.set_user(user)
			for name in names:
				result = run(name, {"society": SOC, "as_of": "2026-12-20", "from_date": "2026-01-01", "to_date": "2026-12-31"})
				self.assertTrue(result["columns"], f"{user} / {name}")
		frappe.set_user(MANAGEMENT)
		with self.assertRaises(frappe.PermissionError):  # only the auditor and General Manager read the audit log
			run("Audit Log and User Activity", {})
		for name in ("Management Dashboard", "Collections", "Defaulters"):
			frappe.set_user(CASHIER)
			with self.assertRaises(frappe.PermissionError):  # cashiers use their cashier day, not management reports
				run(name, {"society": SOC})
		frappe.set_user("Administrator")
