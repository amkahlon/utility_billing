# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Billing accounts and combined bills (FS-MST-20, 21, FS-BIL-06; increment 22, user decision 2026-11-16: separate or combined bills per
service is each society's choice).

A billing account groups one customer's accounts (connections) across services under its own number (utility code 9 and a check digit,
so banks and wallets can take it like any reference number). The society's setting, or the billing account's own, decides whether its
services get separate bills (as before) or **one combined bill**: once a month's bill runs are posted, a Combined Bill gathers the
services' bills, each with its own section, and one payable total. The bills stay one per service underneath (each service's account
keeps its own ledger); a payment made against the billing account number is shared over its services, each bill's amount due first."""

from decimal import Decimal

import frappe
from frappe import _
from frappe.model.naming import getseries
from frappe.utils import flt, getdate

from utility_billing.utility_billing import account
from utility_billing.utility_billing.reference import make_reference

CODE = 9  # the reference number's first digit for billing accounts
SEPARATE, COMBINED = "Separate bill per service", "One combined bill"


def new_number() -> str:
	return make_reference(CODE, int(getseries("UB-REF-9", 8)))


def account_of(reference_no):
	"""The active billing account with this number, or None."""
	ref = (reference_no or "").strip()
	return frappe.db.get_value("Billing Account", {"account_no": ref, "status": "Active"}, "name") if ref else None


def members(ba) -> list[str]:
	return [r.connection for r in frappe.get_cached_doc("Billing Account", ba).connections] if isinstance(ba, str) else [r.connection for r in ba.connections]


def is_combined(ba) -> bool:
	doc = frappe.get_cached_doc("Billing Account", ba) if isinstance(ba, str) else ba
	mode = doc.bill_mode
	if mode in (None, "", "As the society sets"):
		return frappe.get_cached_value("Society", doc.society, "bill_presentation") == "One combined bill per billing account"
	return mode == COMBINED


def account_of_connection(connection):
	rows = frappe.db.sql("""select b.name from `tabBilling Account` b join `tabBilling Account Connection` c on c.parent = b.name
	                        where c.connection = %s and b.status = 'Active' limit 1""", connection)
	return rows[0][0] if rows else None


# ---------------------------------------------------------------- combined bills
def latest_bill(connection, month):
	"""The month's posted bill of an account (the latest version; a final bill of a holder change isn't the current holder's)."""
	return frappe.db.get_value("Utility Bill", {"connection": connection, "billing_month": month, "docstatus": 1, "final_bill": 0,
	                                            "superseded_by": ["is", "not set"]}, "name", order_by="creation desc")


def refresh(ba, month):
	"""Create or update the billing account's Combined Bill for the month from its services' posted bills. None when it isn't combined
	or nothing is billed yet."""
	doc = frappe.get_doc("Billing Account", ba)
	if not is_combined(doc):
		return None
	month = getdate(month).replace(day=1)
	bills = [b for b in (latest_bill(c, month) for c in members(doc)) if b]
	if not bills:
		return None
	name = frappe.db.get_value("Combined Bill", {"billing_account": ba, "billing_month": month})
	cb = frappe.get_doc("Combined Bill", name) if name else frappe.new_doc("Combined Bill")
	cb.update({"billing_account": ba, "consumer_name": doc.consumer_name, "society": doc.society, "billing_month": month})
	cb.set("items", [])
	for b in bills:
		u = frappe.db.get_value("Utility Bill", b, ["connection", "utility", "current_amount", "arrears", "payable_within_due", "surcharge_amount",
		                                            "payable_after_due", "issue_date", "due_date"], as_dict=True)
		cb.append("items", {"utility_bill": b, **{k: u[k] for k in ("connection", "utility", "current_amount", "arrears", "payable_within_due",
		                                                           "surcharge_amount", "payable_after_due")}})
	rows = [frappe.db.get_value("Utility Bill", b, ["issue_date", "due_date", "extended_due_date"], as_dict=True) for b in bills]
	cb.issue_date = max(getdate(r.issue_date) for r in rows)
	cb.due_date = min(getdate(r.extended_due_date or r.due_date) for r in rows)
	for field in ("current_amount", "arrears", "payable_within_due", "surcharge_amount", "payable_after_due"):
		cb.set(field, round(sum(flt(i.get(field)) for i in cb.items), 2))
	cb.flags.ignore_permissions = True
	cb.save()
	return cb.name


def after_run_posted(run):
	"""A posted run brings its services' bills into the combined bills of the billing accounts they belong to."""
	month = getdate(run.billing_month).replace(day=1)
	done = set()
	# one statement for the run (the performance step), not one per bill
	for ba in frappe.db.sql("""select distinct b.name from `tabUtility Bill` u join `tabBilling Account Connection` c on c.connection = u.connection
	                           join `tabBilling Account` b on b.name = c.parent
	                           where u.bill_run = %s and u.docstatus = 1 and b.status = 'Active' order by b.name""", run.name, pluck=True):
		if ba not in done:
			done.add(ba)
			refresh(ba, month)


# ---------------------------------------------------------------- print
def combined_html(name) -> str:
	from utility_billing.utility_billing import barcode, bill_print  # noqa: PLC0415
	from utility_billing.utility_billing.account_print import society_header  # noqa: PLC0415

	cb = frappe.get_doc("Combined Bill", name)
	items = []
	for it in cb.items:
		bill = frappe.get_doc("Utility Bill", it.utility_bill)
		ctx = bill_print.bill_context(bill)
		items.append({"title": ctx["title"], "reference": it.connection, "lines": ctx["lines"], "current": it.current_amount, "arrears": it.arrears,
		              "note": bill.period_note})
	soc = society_header(cb.society, cb.issue_date, "Bill")
	s = frappe.get_cached_doc("Society", cb.society)
	return frappe.render_template("utility_billing/templates/bill/combined_bill.html", {
		"cb": cb, "items": items, "soc": soc, "month": getdate(cb.billing_month).strftime("%B %Y"), "money": bill_print.money, "date": bill_print.date,
		"barcode": barcode.png_data_uri(cb.billing_account) if frappe.utils.cint(s.get("show_barcode")) else None,
		"qr": bill_print.qr_data_uri(cb.billing_account) if frappe.utils.cint(s.show_qr_code) else None,
	})


@frappe.whitelist()
def download_combined(name: str):
	cb = frappe.get_doc("Combined Bill", name)
	cb.check_permission("print")
	from frappe.utils.pdf import get_pdf  # noqa: PLC0415

	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	html = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + combined_html(name) + "</body></html>"
	bill_print.send_pdf(get_pdf(html, options=dict(bill_print.PDF_OPTIONS)), f"{name}.pdf")


# ---------------------------------------------------------------- enquiry and payment
def enquiry(ba) -> dict:
	"""The billing account as one account: what each service owes now, and the latest combined bill (same keys as account.enquiry_of)."""
	doc = frappe.get_doc("Billing Account", ba)
	parts = [account.enquiry_of(c) for c in members(doc)]
	cb = frappe.db.get_value("Combined Bill", {"billing_account": ba}, ["name", "billing_month", "due_date", "payable_within_due", "payable_after_due"],
	                         order_by="billing_month desc", as_dict=True)
	return {
		"connection": doc.account_no, "billing_account": doc.name, "consumer_name": doc.consumer_name, "society": doc.society, "status": doc.status,
		"bill": cb.name if cb else None, "bill_month": getdate(cb.billing_month).strftime("%B %Y") if cb else None,
		"due_date": str(cb.due_date) if cb else None, "payable_within_due": flt(cb.payable_within_due) if cb else 0,
		"payable_after_due": flt(cb.payable_after_due) if cb else 0,
		"balance": round(sum(p["balance"] for p in parts), 2), "balances": {}, "former_balance": round(sum(p["former_balance"] for p in parts), 2),
		"amount_due": round(sum(p["amount_due"] for p in parts), 2),
		"services": [{"connection": c, "amount_due": p["amount_due"]} for c, p in zip(members(doc), parts, strict=True)],
	}


def check_payment(ba, society, amount):
	"""The society's part-payment and overpayment rules, applied to the billing account as a whole."""
	s = frappe.get_cached_doc("Society", society)
	owed = round(sum(max(flt(account.enquiry_of(c)["amount_due"]), 0) for c in members(ba)), 2)
	if flt(amount) > owed + 0.005 and not frappe.utils.cint(s.allow_overpayment):
		frappe.throw(_("Billing account {0} owes Rs {1}; this society doesn't accept overpayments.").format(ba, f"{owed:,.2f}"))
	if flt(amount) < owed - 0.005 and not frappe.utils.cint(s.allow_part_payment):
		frappe.throw(_("Billing account {0} owes Rs {1}; this society doesn't accept part payments.").format(ba, f"{owed:,.2f}"))


def allocate(ba, amount) -> list[dict]:
	"""Share a payment over the account's services: each one's amount due in turn; anything left stays as credit on the first."""
	left = Decimal(str(flt(amount, 2)))
	conns = members(ba)
	rows = []
	for c in conns:
		due = Decimal(str(max(flt(account.enquiry_of(c)["amount_due"]), 0)))
		take = min(left, due)
		if take > 0:
			rows.append({"connection": c, "amount": take})
			left -= take
	if left > 0:
		first = next((r for r in rows if r["connection"] == conns[0]), None)
		if first:
			first["amount"] += left
		else:
			rows.insert(0, {"connection": conns[0], "amount": left})
	return [{"connection": r["connection"], "utility": frappe.db.get_value("Connection", r["connection"], "utility"), "amount": flt(r["amount"])}
	        for r in rows]
