# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 22 part B, billing flexibility: a service billed without a meter (maintenance by plot size), the surcharge on arrears too,
service agreements with a contract tariff, and billing accounts whose services get separate bills or one combined bill (each society's
choice, user decision 2026-11-16) with payments shared over the services.

House N 2308 (spec test case 1): the October electricity bill is 22,260 with 1,104 of arrears."""

import frappe

from utility_billing.install import create_default_utilities
from utility_billing.utility_billing import account, billaccounts, billing, bill_engine
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import BILLING_MANAGER, BILLING_OFFICER, act, approve

MAINT_CAT = "Maintenance-TST Maintenance"


class IntegrationTestFlexibility(BillingCase):
	def setUp(self):
		super().setUp()
		create_default_utilities()
		frappe.db.set_value("Society", SOC, {"bill_presentation": "Separate bill per service", "surcharge_basis": "Percentage of current bill"})
		frappe.clear_document_cache("Society", SOC)
		frappe.db.set_value("Property", self.prop, {"plot_size_class": "10 Marla", "area": 10})

	# ---- helpers
	def maintenance(self):
		ensure("Charge Type", "TST Maintenance", {"charge_name": "TST Maintenance", "charge_group": "Society", "calculation_basis": "By Plot Size",
		                                          "bill_order": 30, "bill_label": "Maintenance"})
		ensure("Consumer Category", MAINT_CAT, {"category_name": "TST Maintenance", "utility": "Maintenance"})
		if not frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": MAINT_CAT, "docstatus": 1}):
			approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Maintenance", "consumer_category": MAINT_CAT,
			                        "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": 0,
			                        "charges": [{"charge_type": "TST Maintenance"}],
			                        "plot_rates": [{"charge_type": "TST Maintenance", "plot_size_class": "10 Marla", "rate": 2500},
			                                       {"charge_type": "TST Maintenance", "plot_size_class": "1 Kanal", "rate": 4000}]}).insert())
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		return frappe.get_doc({"doctype": "Connection", "society": SOC, "property": self.prop, "consumer": consumer, "utility": "Maintenance",
		                       "consumer_category": MAINT_CAT, "status": "Active"}).insert().name

	def maintenance_run(self, post=True):
		frappe.set_user(BILLING_OFFICER)
		run = frappe.get_doc({"doctype": "Bill Run", "society": SOC, "utility": "Maintenance", "billing_month": "2026-10-01",
		                      "issue_date": "2026-11-01"}).insert()
		billing.calculate_run(run.name)
		frappe.set_user("Administrator")
		run.reload()
		return self.post(run) if post else run

	def october(self):
		self.september_posted_with_arrears()
		self.month_reading("2026-10-01", 3905)
		self.fpa("2026-10-01", 1.30)
		return self.make_run("2026-10-01", "2026-11-01")

	# ---- B1 a service without a meter
	def test_maintenance_is_billed_by_plot_size_without_a_meter_or_reading(self):
		maint = self.maintenance()
		run = self.maintenance_run(post=False)
		self.assertEqual(run.reading_cycle, None)
		bill = frappe.get_doc("Utility Bill", {"bill_run": run.name, "connection": maint})
		self.assertEqual((bill.units, bill.consumption_type, bill.current_amount), (0, "Not metered", 2500))
		self.assertEqual([(l.charge_type, l.amount) for l in bill.lines], [("TST Maintenance", 2500)])  # no energy line
		self.assertEqual(run.problems, [])
		frappe.db.set_value("Property", self.prop, "plot_size_class", "2 Kanal")
		billing.calculate_run(run.name)
		run.reload()
		self.assertIn("no TST Maintenance amount for plot size 2 Kanal", run.problems[0].problem)

	# ---- B2 surcharge on arrears too
	def test_the_surcharge_can_include_unpaid_arrears(self):
		run = self.october()
		bill = self.bill(run)
		self.assertEqual(bill.surcharge_amount, 1781)  # 8% of 22,260 (test case 1)
		frappe.db.set_value("Society", SOC, "surcharge_basis", bill_engine.SURCHARGE_WITH_ARREARS)
		frappe.clear_document_cache("Society", SOC)
		billing.calculate_run(run.name)
		self.assertEqual(self.bill(run).surcharge_amount, 1869)  # 8% of 22,260 + 1,104

	# ---- B3 service agreement with a contract tariff
	def contract(self, **kw):
		cat = ensure("Consumer Category", "Electricity-TST Billing B", {"category_name": "TST Billing B", "utility": "Electricity"})
		t = frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": cat, "docstatus": 1})
		if not t:
			t = approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": cat,
			                            "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": 100}).insert()).name
		a = frappe.get_doc({"doctype": "Service Agreement", "connection": self.conn, "start_date": "2026-10-01", "tariff": t, **kw}).insert()
		a.submit()
		return a

	def test_a_contract_tariff_replaces_the_category_tariff_while_in_force(self):
		a = self.contract()
		self.assertEqual(a.status, "Active")
		run = self.october()
		bill = self.bill(run)
		self.assertEqual((bill.tariff, [l.amount for l in bill.lines if l.charge_type == "TST Energy"]), (a.tariff, [26400]))  # 264 x 100
		self.assertRaises(frappe.ValidationError, frappe.get_doc({"doctype": "Service Agreement", "connection": self.conn,
		                                                         "start_date": "2026-12-01"}).insert)  # one at a time
		frappe.set_user(BILLING_MANAGER)
		from utility_billing.utility_billing.doctype.service_agreement.service_agreement import daily, terminate

		terminate(a.name, "2026-10-31", "Contract ended early")
		frappe.set_user("Administrator")
		self.assertIsNone(billing.contract_tariff(self.conn, "2026-11-01"))
		renewing = frappe.get_doc({"doctype": "Service Agreement", "connection": self.conn, "start_date": "2026-11-01", "end_date": "2026-11-30",
		                           "auto_renew": 1, "renewal_months": 12}).insert()
		renewing.submit()
		daily("2026-12-01")
		self.assertEqual(str(frappe.db.get_value("Service Agreement", renewing.name, "end_date")), "2027-11-30")

	# ---- B4 billing accounts: separate or combined bills
	def billing_account(self, maint, mode="As the society sets"):
		consumer = frappe.db.get_value("Connection", self.conn, "consumer")
		return frappe.get_doc({"doctype": "Billing Account", "society": SOC, "consumer": consumer, "bill_mode": mode,
		                       "connections": [{"connection": self.conn}, {"connection": maint}]}).insert()

	def test_separate_bills_unless_the_society_chooses_combined(self):
		maint = self.maintenance()
		ba = self.billing_account(maint)
		self.assertTrue(ba.name.startswith("9") and len(ba.name) == 10)
		self.post(self.october())
		self.maintenance_run()
		self.assertFalse(frappe.db.exists("Combined Bill", {"billing_account": ba.name}))  # the society's default: separate bills
		frappe.db.set_value("Society", SOC, "bill_presentation", "One combined bill per billing account")
		frappe.clear_document_cache("Society", SOC)
		cb = frappe.get_doc("Combined Bill", billaccounts.refresh(ba.name, "2026-10-01"))
		elec = self.bill(frappe.get_doc("Bill Run", frappe.db.get_value("Utility Bill", {"connection": self.conn, "billing_month": "2026-10-01"}, "bill_run")))
		self.assertEqual((cb.name, len(cb.items)), (f"CB-{ba.name}-202610", 2))
		self.assertEqual(cb.payable_within_due, elec.payable_within_due + 2500)
		self.assertEqual(str(cb.due_date), "2026-11-15")
		self.assertTrue(frappe.get_doc("Billing Account", ba.name).connections)
		self.assertRaises(frappe.ValidationError, frappe.get_doc({"doctype": "Billing Account", "society": SOC, "consumer": ba.consumer,
		                                                         "connections": [{"connection": maint}]}).insert)  # in one billing account only
		html = billaccounts.combined_html(cb.name)
		for text in ("Combined Bill", ba.name, "Electricity Bill", "Maintenance Bill", "Maintenance"):
			self.assertIn(text, html, text)

	def test_a_combined_bill_is_paid_with_the_billing_account_number_and_shared(self):
		maint = self.maintenance()
		ba = self.billing_account(maint, mode="One combined bill")
		self.post(self.october())
		self.maintenance_run()
		cb = frappe.get_doc("Combined Bill", {"billing_account": ba.name})
		e = account.enquiry(ba.name)
		self.assertEqual((e["amount_due"], e["bill"]), (cb.payable_within_due, cb.name))
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": ba.name, "payment_channel": "Bank / Channel", "payment_date": "2026-11-10",
		                    "amount": cb.payable_within_due, "mode": "Bank Transfer", "channel_txn_id": "BA-PAY-1"}).insert()
		r.submit()
		r.reload()
		self.assertEqual((r.billing_account, sorted((a.connection, a.amount) for a in r.allocations)),
		                 (ba.name, sorted([(self.conn, cb.items[0].payable_within_due if cb.items[0].connection == self.conn else cb.items[1].payable_within_due),
		                                   (maint, 2500)])))
		self.assertEqual((account.balance(self.conn), account.balance(maint)), (0, 0))
		from utility_billing.utility_billing.account import reverse_document

		reverse_document("Payment Receipt", r.name, "WRONG-AMT", "Posted on the wrong date")
		self.assertEqual(account.balance(maint), 2500)  # every share reversed

	def test_1link_takes_the_billing_account_number(self):
		from frappe.utils import nowdate

		from utility_billing.api import channels
		from utility_billing.install import create_default_reason_codes
		from utility_billing.utility_billing.test_api import API_USER, integration_user

		create_default_reason_codes()
		maint = self.maintenance()
		ba = self.billing_account(maint, mode="One combined bill")
		self.post(self.october())
		self.maintenance_run()
		cb = frappe.get_doc("Combined Bill", {"billing_account": ba.name})
		frappe.get_doc({"doctype": "API Client", "client_name": "TST 1Link BA", "user": integration_user(API_USER), "enabled": 1, "rate_limit": 100,
		                "scope_account_read": 1, "scope_bill_read": 1, "scope_payment_write": 1, "scope_payment_reverse": 1, "channel": "1Link",
		                "payment_mode": "1Link", "societies": [{"society": SOC}]}).insert()

		def as_api(fn, **kw):
			frappe.set_user(API_USER)
			try:
				return fn(**kw)
			finally:
				frappe.set_user("Administrator")

		inq = as_api(channels.bill_inquiry, consumer_number=ba.name)
		self.assertEqual((inq["response_Code"], inq["bill_status"], inq["billing_month"], inq["amount_within_dueDate"]),
		                 ("00", "U", "2610", channels.amount_out(cb.payable_within_due)))
		ok = as_api(channels.bill_payment, consumer_number=ba.name, tran_auth_id="BA-AUTH1", transaction_amount=channels.amount_out(cb.payable_within_due),
		            tran_date=nowdate().replace("-", ""))
		self.assertEqual(ok["response_Code"], "00")
		self.assertEqual(len(frappe.get_doc("Payment Receipt", ok["Identification_parameter"]).allocations), 2)
		self.assertEqual(as_api(channels.bill_inquiry, consumer_number=ba.name)["bill_status"], "P")
		self.assertEqual(as_api(channels.bill_reversal, consumer_number=ba.name, tran_auth_id="BA-AUTH1")["response_Code"], "00")
		self.assertEqual(account.balance(maint), 2500)
