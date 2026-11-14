# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumer 360 (FS-MST-13, 15): one view of a consumer with every connection, meter, flag, deposit, bill, payment, ledger entry and
reading, and a combined statement across the consumer's accounts. Data for the page ub-consumer-360."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from utility_billing.utility_billing import access, account, flags

STAFF = {"System Manager", "Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Cashier", "Recovery Officer",
         "Accounts Officer", "Management", "Billing Auditor"}


def _require():
	if not set(frappe.get_roles()) & STAFF:
		raise frappe.PermissionError(_("You can't open the consumer view."))


@frappe.whitelist()
def search(text: str, limit: int = 20):
	"""Consumers matching a name, CNIC, phone, house number, reference number or meter number (FS-MST-12)."""
	_require()
	text = (text or "").strip()
	if len(text) < 2:
		return []
	like = f"%{text}%"
	rows = frappe.db.sql(
		"""select distinct c.name, c.consumer_name, c.mobile_no, c.id_number from `tabConsumer` c
		left join `tabConnection` k on k.consumer = c.name left join `tabProperty` p on p.name = k.property
		where c.consumer_name like %(l)s or c.id_number like %(l)s or c.mobile_no like %(l)s or c.phone like %(l)s
		   or k.reference_no like %(l)s or k.current_meter like %(l)s or p.property_no like %(l)s
		order by c.consumer_name limit %(n)s""", {"l": like, "n": int(limit)}, as_dict=True)
	return access.mask_rows(rows)


def _connections(consumer):
	return frappe.get_all("Connection", filters={"consumer": consumer},
	                      fields=["name", "reference_no", "society", "property", "utility", "consumer_category", "status", "connection_date",
	                              "sanctioned_load_kw", "last_mdi_kw", "phase", "current_meter", "transformer", "feeder", "route", "security_deposit"],
	                      order_by="society, name")


@frappe.whitelist()
def view(consumer: str):
	_require()
	c = frappe.get_doc("Consumer", consumer)
	conns = _connections(consumer)
	names = [k.name for k in conns] or [""]
	today = getdate(nowdate())
	accounts = []
	for k in conns:
		bal = {b: flt(v) for b, v in account.balances(k.name).items()}
		prop = frappe.db.get_value("Property", k.property, ["property_no", "block", "sector"], as_dict=True) or {}
		accounts.append({**k, "property_no": prop.get("property_no"), "block": prop.get("block"), "balances": bal,
		                 "owes": flt(account.balance(k.name)), "flags": [f.name for f in flags.active(k.name, today)]})
	bills = frappe.get_all("Utility Bill", filters={"connection": ["in", names], "docstatus": 1},
	                       fields=["name", "connection", "billing_month", "issue_date", "due_date", "units", "current_amount", "payable_within_due",
	                               "status", "surcharge_status", "delivery_status"], order_by="issue_date desc", limit=24)
	payments = frappe.get_all("Payment Receipt", filters={"connection": ["in", names], "docstatus": 1},
	                          fields=["name", "connection", "payment_date", "amount", "mode", "payment_channel"], order_by="payment_date desc", limit=24)
	ledger = frappe.get_all("Customer Ledger Entry", filters={"connection": ["in", names]},
	                        fields=["name", "connection", "posting_date", "entry_type", "voucher_type", "voucher_no", "amount", "balance"],
	                        order_by="posting_date desc, seq desc", limit=40)
	readings = frappe.get_all("Meter Reading", filters={"connection": ["in", names]},
	                          fields=["name", "connection", "billing_month", "reading_date", "present_reading", "units", "status", "consumption_type"],
	                          order_by="billing_month desc", limit=24)
	deposits = frappe.get_all("Security Deposit", filters={"connection": ["in", names], "docstatus": 1},
	                          fields=["name", "connection", "deposit_type", "amount", "posting_date"], order_by="posting_date desc", limit=20)
	occupancy = frappe.get_all("Property Occupancy", filters={"consumer": consumer, "parenttype": "Property"},
	                           fields=["parent", "occupant_role", "from_date", "to_date", "share_percent", "is_billed"], order_by="from_date desc")
	open_items = []
	for dt, label in (("Bill Dispute", "Dispute"), ("Recovery Case", "Recovery case"), ("Installment Plan", "Installment plan")):
		meta = frappe.get_meta(dt)
		filters = {"connection": ["in", names]}
		if meta.has_field("status"):
			filters["status"] = ["not in", ["Closed", "Resolved", "Decided", "Cancelled"]]
		if meta.has_field("plan_status"):
			filters["plan_status"] = "Active"
		for n in frappe.get_all(dt, filters=filters, pluck="name", limit=10):
			open_items.append({"doctype": dt, "name": n, "label": _(label)})
	if not access.sees_personal():
		c.id_number, c.mobile_no = access.mask(c.id_number), access.mask(c.mobile_no)
	return {
		"consumer": {"name": c.name, "consumer_name": c.consumer_name, "consumer_type": c.consumer_type, "id_type": c.id_type, "id_number": c.id_number,
		             "mobile_no": c.mobile_no, "email": c.email, "address": c.address, "preferred_language": c.preferred_language,
		             "preferred_channel": c.preferred_channel, "tax_status": c.tax_status, "cash_only": c.cash_only,
		             "registration_no": c.get("registration_no"), "tax_no": c.get("tax_no"),
		             "representatives": [{"name": r.rep_name, "relation": r.relation, "pay": r.can_pay, "notices": r.can_receive_notices,
		                                  "sign": r.can_sign, "changes": r.can_request_changes} for r in c.get("representatives") or []]},
		"accounts": accounts, "total_owed": round(sum(a["owes"] for a in accounts), 2),
		"total_deposit": round(sum(flt(a["security_deposit"]) for a in accounts), 2),
		"bills": bills, "payments": payments, "ledger": ledger, "readings": readings, "deposits": deposits, "occupancy": occupancy,
		"open_items": open_items,
		"service_requests": None,  # service requests and complaints arrive with the parked service-request workflows
	}


def combined_statement(consumer, from_date=None, to_date=None) -> list[dict]:
	"""Every ledger entry of every account of the consumer, by date, with each account's running balance (FS-MST-15)."""
	names = [k.name for k in _connections(consumer)] or [""]
	filters = {"connection": ["in", names]}
	if to_date:
		filters["posting_date"] = ["<=", getdate(to_date)]
	rows = frappe.get_all("Customer Ledger Entry", filters=filters,
	                      fields=["connection", "posting_date", "entry_type", "voucher_no", "remarks", "amount", "balance", "seq"],
	                      order_by="posting_date, connection, seq")
	if from_date:
		start = getdate(from_date)
		opening = {}
		for r in rows:
			if getdate(r.posting_date) < start:
				opening[r.connection] = opening.get(r.connection, 0) + flt(r.amount)
		rows = [{"connection": k, "posting_date": start, "entry_type": _("Opening balance"), "voucher_no": "", "remarks": "", "amount": 0,
		         "balance": round(v, 2)} for k, v in sorted(opening.items())] + [r for r in rows if getdate(r.posting_date) >= start]
	return rows
