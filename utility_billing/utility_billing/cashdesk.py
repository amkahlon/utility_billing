# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Cash counter page (ub-cash), designed after the mock-up "Cash counter" (2026-11-16): scan or type the reference, see what is payable,
take the money (cash with change, cheque, card), post and print. A payment is an ordinary Payment Receipt at the counter, so its rules
all hold: the cashier's open day, numbering per counter, the society's posting order and part-payment rules, the ledger and the receipt
notification (FS-PAY-01, 05, 07, 13)."""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from utility_billing.utility_billing import account
from utility_billing.utility_billing.doctype.cashier_day.cashier_day import open_day_of
from utility_billing.utility_billing import rights

CASHIERS = {"Cashier", "Accounts Officer", "System Manager"}


def require():
	if not rights.can("cash.counter"):
		raise frappe.PermissionError(_("The cash counter is for cashiers."))


@frappe.whitelist()
def state():
	"""The cashier's counter and open day, today's totals and the latest receipts."""
	require()
	day = open_day_of()
	if not day:
		return {"open": False, "counters": frappe.get_all("Cash Counter", filters={"enabled": 1}, fields=["name", "counter_name", "society"])}
	d = frappe.get_doc("Cashier Day", day.name)
	counter = frappe.db.get_value("Cash Counter", d.cash_counter, ["counter_name", "office", "counter_code"], as_dict=True) or {}
	recent = frappe.get_all("Payment Receipt", filters={"cashier_day": d.name, "docstatus": 1},
	                        fields=["name", "creation", "amount", "mode", "connection", "consumer_name"], order_by="creation desc", limit=8)
	houses = {k.name: k.property_no for k in frappe.db.sql(
		"""select c.name, p.property_no from `tabConnection` c left join `tabProperty` p on p.name = c.property where c.name in %s""",
		([r.connection for r in recent] or [""],), as_dict=True)}
	cheques = frappe.db.count("Payment Receipt", {"cashier_day": d.name, "docstatus": 1, "mode": ["in", ["Cheque", "Pay Order"]]})
	return {
		"open": True, "day": d.name, "business_date": str(d.business_date), "stale": str(d.business_date) != nowdate(),
		"opened": str(d.creation), "counter": d.cash_counter, "counter_name": counter.get("counter_name"), "office": counter.get("office"),
		"cashier": frappe.db.get_value("User", d.cashier, "full_name") or d.cashier, "society": d.society,
		"totals": {"receipts": d.receipt_count, "collected": flt(d.total_collected), "cash": flt(d.cash_total), "cheque": flt(d.cheque_total),
		           "cheques": cheques, "card": flt(d.card_total)},
		"recent": [{"name": r.name, "time": str(r.creation), "amount": flt(r.amount), "mode": r.mode, "house": houses.get(r.connection) or r.connection,
		            "consumer_name": r.consumer_name} for r in recent],
		"rules": frappe.db.get_value("Society", d.society, ["allow_part_payment", "allow_overpayment", "payment_posting_order"], as_dict=True),
	}


@frappe.whitelist()
def lookup(reference: str):
	"""What the account owes, as the counter shows it (the reference as typed or scanned from the bill's barcode or QR)."""
	require()
	ref = (reference or "").strip()
	if "/" in ref:  # a QR payment link carries the reference as its last part
		ref = ref.rstrip("/").split("/")[-1].split("=")[-1]
	e = account.enquiry(ref)
	conn = e.get("connection")
	prop = frappe.db.get_value("Property", frappe.db.get_value("Connection", conn, "property"), ["property_no", "block"], as_dict=True) if conn \
		and frappe.db.exists("Connection", conn) else None
	bill = frappe.db.get_value("Utility Bill", e["bill"], ["billing_month", "current_amount", "arrears"], as_dict=True) if e.get("bill") \
		and frappe.db.exists("Utility Bill", e["bill"]) else None
	return {**e, "reference": ref, "property_no": prop.property_no if prop else None, "block": prop.block if prop else None,
	        "bill_month": frappe.utils.getdate(bill.billing_month).strftime("%B %Y") if bill else e.get("bill_month"),
	        "current": flt(bill.current_amount) if bill else None, "arrears": flt(bill.arrears) if bill else None}


@frappe.whitelist(methods=["POST"])
def post(reference: str, amount: float, mode: str = "Cash", instrument_no: str | None = None, bank_name: str | None = None,
         instrument_date: str | None = None):
	"""Post a counter payment (an ordinary Payment Receipt) and return its number for the receipt print."""
	require()
	r = frappe.get_doc({"doctype": "Payment Receipt", "payment_channel": "Counter", "reference_no": (reference or "").strip(), "amount": flt(amount),
	                    "mode": mode, "instrument_no": instrument_no, "bank_name": bank_name, "instrument_date": instrument_date})
	r.insert()
	r.submit()
	return {"name": r.name, "balance_after": flt(r.balance_after), "amount": flt(r.amount)}
