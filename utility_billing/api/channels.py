# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""1Link and Kuickpay bill inquiry, payment and reversal (FS-API-03, gap 11).

Each channel is an API Client of kind 1Link or Kuickpay with its own integration user, scopes and societies. The messages follow
the common "1Bill" layout both channels use: a response code in every reply (never an HTTP error for a business refusal),
amounts as a sign and zero-padded paisa, dates as YYYYMMDD and the billing month as YYMM.

**Confirm the exact layouts against each channel's specification when a society signs up**: the field names, widths and codes
are all in LAYOUT and CODES below, so an adjustment doesn't touch the posting. A payment posts a Bank / Channel receipt (mode 1Link
or Kuickpay) with the channel's authorisation ID as the transaction ID, which posts once and is later matched by the settlement
file (increment 12)."""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from utility_billing.api import v1
from utility_billing.api.guard import check_society, endpoint
from utility_billing.utility_billing import account

CODES = {"ok": "00", "not_found": "01", "blocked": "02", "duplicate": "03", "invalid": "04", "failed": "05"}
LAYOUT = {"amount_digits": 13, "name_length": 30}
BLOCKED_STATUSES = ("Closed", "Applied")


def amount_out(value) -> str:
	"""Rs 23,364.50 -> '+0000002336450' (sign, then paisa zero-padded to the layout's width)."""
	paisa = round(flt(value) * 100)
	return ("-" if paisa < 0 else "+") + str(abs(paisa)).zfill(LAYOUT["amount_digits"])


def amount_in(text) -> float:
	"""'+0000002336450' or '23364.50' -> 23364.5."""
	text = str(text or "").strip()
	if not text:
		raise ValueError("no amount")
	if text[0] in "+-" and text[1:].isdigit():
		return (-1 if text[0] == "-" else 1) * int(text[1:]) / 100
	if text.isdigit() and len(text) >= LAYOUT["amount_digits"]:
		return int(text) / 100
	return float(text)


def date_out(d) -> str:
	return getdate(d).strftime("%Y%m%d") if d else ""


def date_in(text):
	return datetime.strptime(str(text).strip(), "%Y%m%d").date()


def _client_is_channel(client):
	if client.channel not in ("1Link", "Kuickpay"):
		raise frappe.PermissionError(_("This service is for the 1Link and Kuickpay clients."))


def _connection(client, consumer_number):
	"""(connection, response code or None)."""
	connection = account.connection_of((consumer_number or "").strip())
	if not connection:
		return None, CODES["not_found"]
	try:
		check_society(client, frappe.db.get_value("Connection", connection, "society"))
	except frappe.PermissionError:
		return None, CODES["not_found"]  # another society's account doesn't exist for this channel
	if frappe.db.get_value("Connection", connection, "status") in BLOCKED_STATUSES:
		return connection, CODES["blocked"]
	return connection, None


def _last_payment_since(connection, since):
	return frappe.db.get_value("Payment Receipt", {"connection": connection, "docstatus": 1, "payment_date": [">=", since]},
	                           ["payment_date", "amount", "channel_txn_id", "name"], order_by="payment_date desc, creation desc", as_dict=True)


def inquiry_reply(client, consumer_number):
	connection, code = _connection(client, consumer_number)
	reply = {"response_Code": code or CODES["ok"], "consumer_Detail": "", "bill_status": "", "due_date": "", "amount_within_dueDate": amount_out(0),
	         "amount_after_dueDate": amount_out(0), "billing_month": "", "date_paid": "", "amount_paid": "", "tran_auth_Id": "", "reserved": ""}
	if not connection:
		return reply
	e = account.enquiry_of(connection)
	reply["consumer_Detail"] = (e["consumer_name"] or "").upper()[: LAYOUT["name_length"]]
	if code:
		reply["bill_status"] = "B"
		return reply
	due = e["amount_due"]
	surcharge = max(flt(e["payable_after_due"]) - flt(e["payable_within_due"]), 0)
	before_due = e["due_date"] and getdate(nowdate()) <= getdate(e["due_date"])
	reply.update({"due_date": date_out(e["due_date"]), "amount_within_dueDate": amount_out(due),
	              "amount_after_dueDate": amount_out(due + surcharge if before_due and due > 0 else due)})
	if e["bill"]:
		month, issue = frappe.db.get_value("Utility Bill", e["bill"], ["billing_month", "issue_date"])
		reply["billing_month"] = getdate(month).strftime("%y%m")
		paid = _last_payment_since(connection, issue)
		if paid and due <= 0:
			reply.update({"bill_status": "P", "date_paid": date_out(paid.payment_date), "amount_paid": amount_out(paid.amount),
			              "tran_auth_Id": paid.channel_txn_id or ""})
			return reply
	reply["bill_status"] = "U" if due > 0 else "P"
	return reply


@endpoint("account.read", summary="1Link / Kuickpay bill inquiry: the consumer's name, bill status (U unpaid, P paid, B blocked), due date and amounts.",
          params=[("consumer_number", "string", True, "Consumer reference number."), ("bank_mnemonic", "string", False, "The paying bank."),
                  ("reserved", "string", False, "Not used.")],
          returns={"response_Code": "00", "consumer_Detail": "MUHAMMAD ALI", "bill_status": "U", "due_date": "20261115",
                   "amount_within_dueDate": "+0000002336400", "amount_after_dueDate": "+0000002522000", "billing_month": "2610"},
          response_code=lambda r: r.get("response_Code"))
def bill_inquiry(client, consumer_number, bank_mnemonic=None, reserved=None):
	_client_is_channel(client)
	return inquiry_reply(client, consumer_number)


@endpoint("payment.write", post_only=True,
          summary="1Link / Kuickpay bill payment. The authorisation ID posts once: a repeat gets 03. Amounts as in the inquiry; full payment unless the client accepts part payments.",
          params=[("consumer_number", "string", True, "Consumer reference number."), ("tran_auth_id", "string", True, "The channel's authorisation ID."),
                  ("transaction_amount", "string", True, "Amount, as '+0000002336400' or '23364.00'."), ("tran_date", "string", True, "YYYYMMDD"),
                  ("tran_time", "string", False, "HHMMSS"), ("bank_mnemonic", "string", False, "The paying bank."), ("reserved", "string", False, "Not used.")],
          returns={"response_Code": "00", "Identification_parameter": "BNK-2026-00001", "reserved": ""},
          response_code=lambda r: r.get("response_Code"))
def bill_payment(client, consumer_number, tran_auth_id, transaction_amount, tran_date, tran_time=None, bank_mnemonic=None, reserved=None):
	_client_is_channel(client)
	reply = {"response_Code": CODES["ok"], "Identification_parameter": "", "reserved": ""}
	connection, code = _connection(client, consumer_number)
	if code:
		return dict(reply, response_Code=code)
	txn = (tran_auth_id or "").strip()
	if frappe.db.exists("Payment Receipt", {"channel_txn_id": txn}):
		return dict(reply, response_Code=CODES["duplicate"])
	try:
		amount, paid_on = amount_in(transaction_amount), date_in(tran_date)
	except (ValueError, TypeError):
		return dict(reply, response_Code=CODES["invalid"])
	frappe.db.savepoint("channel_payment")
	try:
		r = v1.post_channel_payment(client, connection, amount, paid_on, txn)
	except frappe.DuplicateEntryError:
		frappe.db.rollback(save_point="channel_payment")
		return dict(reply, response_Code=CODES["duplicate"])
	except frappe.ValidationError:
		frappe.db.rollback(save_point="channel_payment")  # nothing of a refused payment stays (FS-API-10)
		return dict(reply, response_Code=CODES["invalid"])
	return dict(reply, Identification_parameter=r.name)


@endpoint("payment.reverse", post_only=True, summary="1Link / Kuickpay reversal of a payment the channel posted today.",
          params=[("consumer_number", "string", True, "Consumer reference number."), ("tran_auth_id", "string", True, "The authorisation ID of the payment."),
                  ("reserved", "string", False, "Not used.")],
          returns={"response_Code": "00", "Identification_parameter": "BNK-2026-00001", "reserved": ""},
          response_code=lambda r: r.get("response_Code"))
def bill_reversal(client, consumer_number, tran_auth_id, reserved=None):
	_client_is_channel(client)
	reply = {"response_Code": CODES["ok"], "Identification_parameter": "", "reserved": ""}
	connection, code = _connection(client, consumer_number)
	if not connection:
		return dict(reply, response_Code=code)
	receipt = frappe.db.get_value("Payment Receipt", {"channel_txn_id": (tran_auth_id or "").strip(), "connection": connection}, ["name", "docstatus"], as_dict=True)
	if not receipt:
		return dict(reply, response_Code=CODES["not_found"])
	if receipt.docstatus == 2:
		return dict(reply, response_Code=CODES["duplicate"], Identification_parameter=receipt.name)
	frappe.db.savepoint("channel_reversal")
	try:
		v1.reverse_channel_payment(client, tran_auth_id, _("Reversed by {0}").format(client.name))
	except (frappe.ValidationError, frappe.PermissionError):
		frappe.db.rollback(save_point="channel_reversal")
		return dict(reply, response_Code=CODES["failed"])
	return dict(reply, Identification_parameter=receipt.name)
