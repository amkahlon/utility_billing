# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 22, billing completeness: defective meters (FS-WF-08, SC-29), fixed charges while disconnected, a category change split
inside a bill and a holder change split into a final bill and the incoming holder's bill (FS-BIL-02, SC-13; user decisions 2026-11-16).

House N 2308 (spec test case 1): reading 3641 on 05-09-2026 (September, 811 units), 3905 on 05-10-2026 (October, 264 units); Rs 70 a unit
plus Service 1,700 and Aquifer 1,000 a month; after September it owes Rs 1,104."""

import frappe
from frappe.utils import getdate

from utility_billing.utility_billing import account, billing, defective, holders, readings, servicereq
from utility_billing.utility_billing.test_billing import SOC, BillingCase, ensure
from utility_billing.utility_billing.testing import approve


class IntegrationTestCompleteness(BillingCase):
	def setUp(self):
		super().setUp()
		for code in ("Defective", "Burnt"):
			frappe.db.set_value("Reading Exception Code", code, "billing_treatment", defective.TREATMENT)
		self.society(tariff_split_method=holders.BY_READING, disconnected_fixed_charges="Stop from the disconnection date",
		             defective_billing_method=defective.AVERAGE, defective_average_months=3, defective_max_months=3)

	def society(self, **values):
		frappe.db.set_value("Society", SOC, values)
		frappe.clear_document_cache("Society", SOC)

	def october_reading(self, present=None, exception=None):
		cycle = f"{SOC}-Electricity-2026-10"
		if not frappe.db.exists("Reading Cycle", cycle):
			frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01"}).insert()
		readings.create_reading_sheet(cycle)
		r = frappe.get_doc("Meter Reading", {"reading_cycle": cycle, "connection": self.conn})
		if exception:
			r.exception_code = exception
		else:
			r.present_reading, r.reading_date = present, "2026-10-05"
		r.save()
		if exception:
			readings.estimate_unread(cycle)
			r.reload()
		readings.close_cycle(cycle)
		return r

	def october_bills(self):
		self.fpa("2026-10-01", 1.30)
		run = self.make_run("2026-10-01", "2026-11-01")
		return run, frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["*"], order_by="final_bill desc, name")

	# ---- A1 defective meters (FS-WF-08)
	def test_a_defective_meter_is_billed_on_its_average_and_a_meter_change_is_raised(self):
		self.september_posted_with_arrears()  # August 831, September 811 units
		r = self.october_reading(exception="Defective")
		self.assertEqual((r.status, r.units, r.defective), ("Estimated", 821, 1))  # average of the months there are
		self.assertIn("Average billed: meter defective", r.checks)
		meter = frappe.get_doc("Meter", r.meter)
		self.assertEqual((meter.status, str(meter.defective_since)), ("Defective", "2026-10-01"))
		sr = frappe.get_doc("Service Request", meter.meter_change_request)
		self.assertEqual((sr.kind, sr.connection, sr.status), (servicereq.METER, self.conn, "Open"))
		run, (bill,) = self.october_bills()
		self.assertEqual((bill.units, bill.consumption_type), (821, "Average billed: meter defective"))
		rows = defective.still_defective(SOC)
		self.assertEqual([(x["meter"], x["months_averaged"], x["over_limit"]) for x in rows], [(r.meter, 1, False)])

	def test_the_other_methods_and_the_averaging_limit(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, "sanctioned_load_kw", 5)
		self.society(defective_billing_method=defective.LOAD, defective_load_factor=30, defective_hours=720)
		r = self.october_reading(exception="Burnt")
		self.assertEqual(r.units, 1080)  # 5 kW x 30% x 720 hours
		r.flags.estimated = False
		self.society(defective_billing_method=defective.MINIMUM)
		self.assertEqual(defective.units_for(r)[0], 0)
		self.society(defective_billing_method=defective.AVERAGE, defective_max_months=1)
		frappe.db.set_value("Meter Reading", r.name, "billing_month", "2026-09-01")  # pretend the defective spell began in September
		frappe.db.set_value("Meter", r.meter, "defective_since", "2026-09-01")
		later = frappe._dict(r.as_dict(), billing_month=getdate("2026-10-01"))
		units, note = defective.units_for(later)
		self.assertEqual(units, 0)
		self.assertIn("averaging stopped", note)

	def test_the_true_up_after_replacement_unless_the_old_meter_cant_be_read(self):
		self.september_posted_with_arrears()
		self.october_reading(exception="Defective")  # 821 units billed on averages
		old = frappe.db.get_value("Connection", self.conn, "current_meter")
		new = frappe.get_doc({"doctype": "Meter", "serial_no": "TST-BIL-M2", "society": SOC, "utility": "Electricity"}).insert().name
		conn = frappe.get_doc("Connection", self.conn)
		row = next(m for m in conn.meters if m.meter == old)
		row.removed_on, row.end_reading = "2026-10-20", 4000
		conn.append("meters", {"meter": new, "installed_on": "2026-10-20", "start_reading": 0})
		conn.save()
		ctx = readings.reading_context(self.conn, "2026-11-01")
		self.assertEqual((ctx.old_meter_units, ctx.estimated_since_units), (359, 821))  # 4000 - 3641 on the old meter, less what was billed
		frappe.db.set_value("Connection Meter", row.name, "unreadable", 1)
		ctx = readings.reading_context(self.conn, "2026-11-01")
		self.assertEqual((ctx.old_meter_units, ctx.estimated_since_units), (0, 0))  # the averaged months stand

	# ---- A2 fixed charges while disconnected
	def test_fixed_charges_continue_while_disconnected_when_the_society_says_so(self):
		self.september_posted_with_arrears()
		frappe.db.set_value("Connection", self.conn, {"status": "Disconnected", "disconnected_on": "2026-09-20"})
		cycle = frappe.get_doc({"doctype": "Reading Cycle", "society": SOC, "utility": "Electricity", "billing_month": "2026-10-01"}).insert()
		readings.create_reading_sheet(cycle.name)  # a disconnected account isn't read
		readings.close_cycle(cycle.name)
		run, bills = self.october_bills()
		self.assertEqual(bills, [])  # "Stop": no bill
		self.society(disconnected_fixed_charges="Continue")
		billing.calculate_run(run.name)
		(bill,) = frappe.get_all("Utility Bill", filters={"bill_run": run.name}, fields=["units", "current_amount", "consumption_type", "name"])
		self.assertEqual((bill.units, bill.current_amount, bill.consumption_type), (0, 2700, "Disconnected"))  # service + aquifer, in full

	# ---- A3 category change inside a bill
	def other_category(self):
		cat = ensure("Consumer Category", "Electricity-TST Billing B", {"category_name": "TST Billing B", "utility": "Electricity"})
		if not frappe.db.get_value("Tariff", {"society": SOC, "consumer_category": cat, "docstatus": 1}):
			approve(frappe.get_doc({"doctype": "Tariff", "society": SOC, "utility": "Electricity", "consumer_category": cat,
			                        "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": 100}).insert())
		return cat

	def change_category(self, reading=None):
		new = self.other_category()
		conn = frappe.get_doc("Connection", self.conn)
		holders.record(conn, holders.CATEGORY, "2026-09-20", old_value=conn.consumer_category, new_value=new, reading=reading)
		conn.consumer_category = new
		conn.save()

	def energy_amounts(self, bill):
		return sorted(round(l.amount) for l in frappe.get_doc("Utility Bill", bill).lines if l.charge_type == "TST Energy")

	def test_a_category_change_splits_the_bill_by_the_change_day_reading(self):
		self.september_posted_with_arrears()
		self.change_category(reading=3741)  # 100 units before the change, 164 after
		self.month_reading("2026-10-01", 3905)  # flagged as below the usual range, then approved (test case 1)
		run, (bill,) = self.october_bills()
		self.assertEqual(self.energy_amounts(bill.name), [7000, 16400])  # 100 x 70 and 164 x 100
		self.assertEqual(bill.consumer_category, "Electricity-TST Billing B")

	def test_without_a_reading_or_set_to_days_the_split_is_by_days(self):
		self.september_posted_with_arrears()
		self.change_category()  # no reading on the day: 15 of 30 days before
		self.month_reading("2026-10-01", 3905)  # flagged as below the usual range, then approved (test case 1)
		run, (bill,) = self.october_bills()
		self.assertEqual(self.energy_amounts(bill.name), [9240, 13200])  # 132 x 70 and 132 x 100

	# ---- A5 holder change: final bill and a clean start (user decision 1)
	def move_tenant_in(self, reading=3741, owner_liable=1):
		tenant = frappe.get_doc({"doctype": "Consumer", "consumer_name": "Tenant of N 2308"}).insert().name
		sr = frappe._dict(name=None, steps=[frappe._dict(step_kind="Field", reading=reading)] if reading else [])
		servicereq.change_holder(frappe.get_doc("Connection", self.conn), tenant, getdate("2026-09-20"), role="Tenant", owner_liable=owner_liable, sr=sr)
		return tenant

	def test_a_tenant_moving_in_gives_the_owner_a_final_bill_and_the_tenant_a_clean_start(self):
		owner = frappe.db.get_value("Connection", self.conn, "consumer")
		self.september_posted_with_arrears()  # owes 1,104
		tenant = self.move_tenant_in()
		self.month_reading("2026-10-01", 3905)  # flagged as below the usual range, then approved (test case 1)
		run, (final, first) = self.october_bills()
		self.assertEqual((final.final_bill, final.consumer, final.units, final.arrears), (1, owner, 100, 1104))
		self.assertEqual((first.final_bill, first.consumer, first.units, first.arrears), (0, tenant, 164, 0))
		self.assertTrue(final.period_note.startswith("Final bill up to"))
		self.post(run)
		fob = frappe.get_doc("Former Occupant Balance", {"final_bill": final.name})
		final_payable = frappe.db.get_value("Utility Bill", final.name, "payable_within_due")
		self.assertEqual((fob.consumer, fob.liable_consumer, fob.amount, fob.status), (owner, owner, final_payable, "Open"))
		first = frappe.get_doc("Utility Bill", first.name)
		self.assertEqual((first.arrears, first.payable_within_due), (0, first.current_amount))  # the tenant's bill carries no old debt
		self.assertEqual(account.arrears_for_new_bill(self.conn), first.current_amount)
		e = account.enquiry_of(self.conn)
		self.assertEqual((e["former_balance"], e["amount_due"]), (final_payable, first.current_amount))
		# the former holder pays their balance
		r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": self.conn, "payment_channel": "Bank / Channel", "payment_date": "2026-11-05",
		                    "amount": final_payable, "mode": "Bank Transfer", "channel_txn_id": "FOB-PAY-1", "former_balance": fob.name}).insert()
		r.submit()
		fob.reload()
		self.assertEqual((fob.paid, fob.outstanding, fob.status), (final_payable, 0, "Settled"))
		self.assertEqual(account.balance(self.conn), first.current_amount)

	def test_a_paid_final_bill_escapes_the_surcharge_and_the_tenant_isnt_charged_for_it(self):
		self.september_posted_with_arrears()
		self.move_tenant_in()
		self.month_reading("2026-10-01", 3905)  # flagged as below the usual range, then approved (test case 1)
		run, (final, first) = self.october_bills()
		self.post(run)
		account.apply_surcharges(as_of="2026-12-31", society=SOC)
		fob = frappe.get_doc("Former Occupant Balance", {"final_bill": final.name})
		self.assertEqual(frappe.db.get_value("Utility Bill", final.name, "surcharge_status"), "Posted")
		self.assertGreater(fob.surcharge, 0)  # unpaid: the final bill's surcharge is the former holder's
		self.assertEqual(account.arrears_for_new_bill(self.conn), round(account.balance(self.conn) - fob.outstanding, 2))

	def test_cancelling_the_run_removes_the_former_occupant_balance(self):
		self.september_posted_with_arrears()
		self.move_tenant_in(reading=None)
		self.month_reading("2026-10-01", 3905)  # flagged as below the usual range, then approved (test case 1)
		run, (final, first) = self.october_bills()
		self.assertEqual((final.units, first.units), (132, 132))  # no reading on the day: by days
		self.post(run)
		self.assertTrue(frappe.db.exists("Former Occupant Balance", {"final_bill": final.name}))
		run.reload()
		run.cancellation_reason = "Wrong readings"
		run.flags.ignore_permissions = True
		run.cancel()
		self.assertFalse(frappe.db.exists("Former Occupant Balance", {"final_bill": final.name}))
		self.assertEqual(account.balance(self.conn), 1104)
