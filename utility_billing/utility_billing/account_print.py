# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Receipt and account statement print (FS-PAY-01, FS-REC-14, FS-CLT-03), with the society's logo."""

import frappe
from frappe import _
from frappe.utils import getdate, money_in_words, nowdate

from utility_billing.utility_billing import account
from utility_billing.utility_billing.bill_print import date, logo_data_uri, money, qr_data_uri, send_pdf

RECEIPT_PDF = {"page-size": "A5", "margin-top": "6mm", "margin-bottom": "6mm", "margin-left": "7mm",
               "margin-right": "7mm", "encoding": "UTF-8"}
STATEMENT_PDF = {"page-size": "A4", "margin-top": "8mm", "margin-bottom": "10mm", "margin-left": "8mm",
                 "margin-right": "8mm", "encoding": "UTF-8"}
ENTRY_LABELS = {"Bill": "Bill", "Surcharge": "Late payment surcharge", "Payment": "Payment", "Fee": "Fee",
                "Credit Note": "Credit note", "Debit Note": "Debit note", "Refund": "Refund", "Write-off": "Written off",
                "Deposit offset": "Security deposit applied", "Reversal": "Reversal"}


def society_header(society):
	s = frappe.get_cached_doc("Society", society)
	return {"name": s.society_name, "address": s.address, "phone": s.phone, "email": s.email, "ntn": s.ntn,
	        "colour": s.brand_colour or "#1F5F8B", "logo": logo_data_uri(s.logo),
	        "letterhead": [l for l in (s.letterhead_text or "").splitlines() if l.strip() and l.strip() != s.society_name]}


def wrap(body, title):
	return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
	        f"<title>{frappe.utils.escape_html(title)}</title></head><body>{body}</body></html>")


# ---------------------------------------------------------------- receipt
def receipt_context(r):
	return {
		"r": r, "soc": society_header(r.society), "money": money, "date": date,
		"words": money_in_words(r.amount, "PKR"),
		"cashier": frappe.db.get_value("User", r.owner, "full_name") or r.owner,
		"counter": frappe.db.get_value("Cash Counter", r.cash_counter, "counter_name") if r.cash_counter else None,
		"qr": qr_data_uri(r.connection) if r.connection else None,
		"watermark": _("REVERSED") if r.docstatus == 2 else (_("DRAFT") if r.docstatus == 0 else None),
	}


def receipt_html(name):
	r = frappe.get_doc("Payment Receipt", name)
	return frappe.render_template("utility_billing/templates/account/receipt.html", receipt_context(r))


def payment_receipt_html(name):
	"""Jinja method for the "Payment Receipt" print format."""
	return receipt_html(name)


@frappe.whitelist()
def download_receipt(name: str):
	r = frappe.get_doc("Payment Receipt", name)
	r.check_permission("print")
	from frappe.utils.pdf import get_pdf

	send_pdf(get_pdf(wrap(receipt_html(name), name), options=dict(RECEIPT_PDF)), f"{name}.pdf")


# ---------------------------------------------------------------- statement
def statement_html(connection, from_date=None, to_date=None):
	data = account.statement(connection, from_date, to_date)
	conn = frappe.get_doc("Connection", connection)
	ctx = {
		"d": data, "soc": society_header(conn.society), "conn": conn, "money": money, "date": date,
		"labels": ENTRY_LABELS, "today": date(nowdate()),
		"property": frappe.db.get_value("Property", conn.property, ["property_no", "block"], as_dict=True) or {},
		"category": frappe.db.get_value("Consumer Category", conn.consumer_category, "category_name"),
	}
	return frappe.render_template("utility_billing/templates/account/statement.html", ctx)


@frappe.whitelist()
def download_statement(connection: str, from_date: str | None = None, to_date: str | None = None):
	"""Account statement PDF for a date range (FS-REC-14)."""
	frappe.get_doc("Connection", connection).check_permission("read")
	if not frappe.has_permission("Customer Ledger Entry", "print"):
		raise frappe.PermissionError(_("You can't print account statements."))
	if from_date and to_date and getdate(from_date) > getdate(to_date):
		frappe.throw(_("The start date is after the end date."))
	from frappe.utils.pdf import get_pdf

	html = wrap(statement_html(connection, from_date or None, to_date or None), connection)
	send_pdf(get_pdf(html, options=dict(STATEMENT_PDF)), f"statement-{connection}.pdf")
