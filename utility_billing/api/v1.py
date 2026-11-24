# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Billing API, version 1 (FS-API-01, 06 to 10). Path: /api/method/utility_billing.api.v1.<method>.

Sign in with the integration user's API key and secret ("Authorization: token <key>:<secret>"). Each method needs a scope on the
caller's API Client; accounts outside the client's societies can't be seen. Writes need an Idempotency-Key header. Changes stay
backward compatible within v1; a breaking change gets v2 beside it.

The documentation is generated from the methods below: /api/method/utility_billing.api.v1.openapi (OpenAPI 3) and /ums-api."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from utility_billing.api.guard import REGISTRY, check_society, endpoint
from utility_billing.utility_billing import account, billaccounts

VERSION = "1.0.0"
TXN_MAX = 64


def _connection(client, reference_no):
	connection = account.connection_of(reference_no)
	if not connection:
		raise frappe.DoesNotExistError(_("No account with reference number {0}.").format(reference_no))
	check_society(client, frappe.db.get_value("Connection", connection, "society"))
	return connection


def _bill_view(b):
	return {"bill": b.name, "billing_month": str(b.billing_month)[:7], "issue_date": str(b.issue_date) if b.issue_date else None,
	        "due_date": str(b.extended_due_date or b.due_date) if b.due_date else None, "units": flt(b.units), "current_amount": flt(b.current_amount),
	        "arrears": flt(b.arrears), "payable_within_due": flt(b.payable_within_due), "payable_after_due": flt(b.payable_after_due),
	        "status": b.status,
	        # net metering (increment 28): null for an account without solar
	        "solar": {"method": b.nm_method, "imported_units": flt(b.import_units), "exported_units": flt(b.export_units),
	                  "credited_units": flt(b.credited_units), "units_carried_forward": flt(b.units_carried_out),
	                  "buyback_credit": flt(b.buyback_credit)} if b.get("nm_method") else None}


# ---------------------------------------------------------------- reading
@endpoint("account.read", summary="Account enquiry: consumer, status, latest bill and what is payable today.",
          params=[("reference_no", "string", True, "The 10-digit consumer reference number printed on the bill.")],
          returns={"reference_no": "1000000016", "consumer_name": "…", "society": "DEMO", "status": "Active", "latest_bill": {"bill": "BILL-…"},
                   "balance": 23364.0, "amount_due": 23364.0})
def account_enquiry(client, reference_no):
	connection = _connection(client, reference_no)
	e = account.enquiry_of(connection)
	return {"reference_no": connection, "consumer_name": e["consumer_name"], "society": e["society"], "status": e["status"],
	        "latest_bill": {"bill": e["bill"], "billing_month": e["bill_month"], "due_date": e["due_date"],
	                        "payable_within_due": e["payable_within_due"], "payable_after_due": e["payable_after_due"]} if e["bill"] else None,
	        "balance": e["balance"], "balances": e["balances"], "amount_due": e["amount_due"]}


@endpoint("bill.read", summary="The account's posted bills, newest first.",
          params=[("reference_no", "string", True, "Consumer reference number."), ("limit", "integer", False, "How many (default 12, at most 36).")],
          returns={"reference_no": "1000000016", "bills": [{"bill": "BILL-…", "billing_month": "2026-10", "payable_within_due": 23364.0}]})
def bills(client, reference_no, limit=12):
	connection = _connection(client, reference_no)
	rows = frappe.get_all("Utility Bill", filters={"connection": connection, "docstatus": 1}, limit=min(max(cint(limit) or 12, 1), 36),
	                      order_by="billing_month desc, creation desc",
	                      fields=["name", "billing_month", "issue_date", "due_date", "extended_due_date", "units", "current_amount", "arrears",
	                              "payable_within_due", "payable_after_due", "status", "nm_method", "import_units", "export_units", "credited_units",
	                              "units_carried_out", "buyback_credit"])
	return {"reference_no": connection, "bills": [_bill_view(b) for b in rows]}


@endpoint("bill.read", summary="One bill as a PDF, exactly as issued.", params=[("bill", "string", True, "Bill number from `bills`.")],
          returns="application/pdf")
def bill_pdf(client, bill):
	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	b = frappe.db.get_value("Utility Bill", bill, ["society", "docstatus"], as_dict=True)
	if not b or b.docstatus != 1:
		raise frappe.DoesNotExistError(_("No posted bill {0}.").format(bill))
	check_society(client, b.society)
	content = bill_print.pdf_of([bill])
	frappe.local.response.filename = f"{bill}.pdf"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"
	return None


@endpoint("bill.read", summary="Account statement: opening balance, every entry with a running balance, closing balance.",
          params=[("reference_no", "string", True, "Consumer reference number."), ("from_date", "string", False, "YYYY-MM-DD"),
                  ("to_date", "string", False, "YYYY-MM-DD")],
          returns={"opening": 0.0, "closing": 23364.0, "entries": [{"date": "2026-10-01", "type": "Bill", "amount": 22260.0, "balance": 23364.0}]})
def statement(client, reference_no, from_date=None, to_date=None):
	connection = _connection(client, reference_no)
	s = account.statement(connection, from_date, to_date)
	return {"reference_no": connection, "from_date": str(s["from_date"]) if s["from_date"] else None, "to_date": str(s["to_date"]),
	        "opening": s["opening"], "closing": s["closing"],
	        "entries": [{"date": str(r.posting_date), "type": r.entry_type, "document": r.voucher_no, "amount": flt(r.amount), "balance": flt(r.running)}
	                    for r in s["rows"]]}


@endpoint("account.read", summary="Where a service request stands.", params=[("request", "string", True, "Service request number.")],
          returns={"request": "SR-…", "type": "Reconnection", "status": "In progress", "current_step": "Payment"})
def service_request(client, request):
	sr = frappe.db.get_value("Service Request", request, ["name", "request_type", "status", "current_step", "connection", "society", "creation"], as_dict=True)
	if not sr:
		raise frappe.DoesNotExistError(_("No service request {0}.").format(request))
	check_society(client, sr.society)
	return {"request": sr.name, "type": frappe.db.get_value("Service Request Type", sr.request_type, "type_name") or sr.request_type,
	        "status": sr.status, "current_step": sr.current_step, "reference_no": sr.connection, "requested_on": str(sr.creation)[:10]}


# ---------------------------------------------------------------- payments
def enquiry_for(reference):
	"""The enquiry of an account, or of a billing account (all its services together; increment 22)."""
	ba = billaccounts.account_of(reference) if not frappe.db.exists("Connection", reference) else None
	return billaccounts.enquiry(ba) if ba else account.enquiry_of(reference)


def accepted_amounts(e, paid_on) -> set:
	"""What a full payment on this date may be: the balance owed today, and the bill's within-due or after-due amount."""
	amounts = {round(flt(e["amount_due"]), 2)}
	if e["bill"]:
		on_time = e["due_date"] and getdate(paid_on) <= getdate(e["due_date"])
		amounts.add(round(flt(e["payable_within_due"] if on_time else e["payable_after_due"]), 2))
	return {a for a in amounts if a > 0}


def post_channel_payment(client, connection, amount, paid_on, transaction_id):
	"""Post a payment received by the client's channel as a Bank / Channel receipt (FS-PAY-06). The transaction ID posts once."""
	amount = flt(amount, 2)
	transaction_id = (transaction_id or "").strip()
	if amount <= 0:
		frappe.throw(_("The amount must be more than zero."))
	if not transaction_id or len(transaction_id) > TXN_MAX:
		frappe.throw(_("Send the channel's transaction ID (at most {0} characters).").format(TXN_MAX))
	if getdate(paid_on) > getdate(nowdate()):
		frappe.throw(_("The payment date can't be in the future."))
	if frappe.db.exists("Payment Receipt", {"channel_txn_id": transaction_id}):
		raise frappe.DuplicateEntryError(_("Transaction {0} is already posted.").format(transaction_id))
	if not cint(client.partial_payments):
		allowed = accepted_amounts(enquiry_for(connection), paid_on)
		if amount not in allowed:
			frappe.throw(_("The amount must be the full amount payable ({0}).").format(", ".join(f"{a:,.2f}" for a in sorted(allowed)) or "0"))
	r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": connection, "payment_channel": "Bank / Channel", "payment_date": str(getdate(paid_on)),
	                    "amount": amount, "mode": client.payment_mode or "Bank Transfer", "channel_txn_id": transaction_id,
	                    "remarks": _("Posted by {0} through the billing API").format(client.name)})
	r.flags.api_client = client.name
	r.flags.ignore_permissions = True
	r.insert()
	r.submit()
	return r


@endpoint("payment.write", write=True, summary="Post a payment the system received. Send an Idempotency-Key header; a retry with the same key returns the same receipt.",
          params=[("reference_no", "string", True, "Consumer reference number."), ("amount", "number", True, "Amount received, in rupees."),
                  ("paid_on", "string", True, "Date the consumer paid, YYYY-MM-DD."), ("transaction_id", "string", True, "The system's own transaction ID (posts once).")],
          returns={"receipt": "BNK-2026-00001", "reference_no": "1000000016", "amount": 23364.0, "balance_after": 0.0})
def post_payment(client, reference_no, amount, paid_on, transaction_id):
	connection = _connection(client, reference_no)
	r = post_channel_payment(client, connection, amount, paid_on, transaction_id)
	return {"receipt": r.name, "reference_no": connection, "amount": flt(r.amount), "paid_on": str(r.payment_date),
	        "balance_after": flt(frappe.db.get_value("Payment Receipt", r.name, "balance_after"))}


def reverse_channel_payment(client, transaction_id, remarks):
	"""Reverse a payment this client posted, on the day it was posted (FS-API-03 reversal). Later corrections go through a
	Billing Manager."""
	name = frappe.db.get_value("Payment Receipt", {"channel_txn_id": (transaction_id or "").strip(), "docstatus": 1},
	                           ["name", "owner", "creation"], as_dict=True)
	if not name:
		raise frappe.DoesNotExistError(_("No posted payment with transaction {0}.").format(transaction_id))
	if name.owner != client.user:
		raise frappe.PermissionError(_("A client can reverse only the payments it posted."))
	if getdate(name.creation) != getdate(nowdate()):
		frappe.throw(_("A payment can be reversed by its channel only on the day it was posted; ask the society to reverse it."))
	doc = frappe.get_doc("Payment Receipt", name.name)
	doc.flags.reversal = {"reason_code": "CHANNEL-REV", "remarks": (remarks or _("Reversed by {0}").format(client.name)).strip()}
	doc.flags.ignore_permissions = True
	doc.cancel()
	return doc


@endpoint("payment.reverse", write=True, summary="Reverse a payment this system posted today (for example a failed transfer).",
          params=[("transaction_id", "string", True, "The transaction ID the payment was posted with."), ("reason", "string", False, "Why.")],
          returns={"receipt": "BNK-2026-00001", "status": "Reversed"})
def reverse_payment(client, transaction_id, reason=None):
	doc = reverse_channel_payment(client, transaction_id, reason)
	return {"receipt": doc.name, "status": "Reversed", "transaction_id": transaction_id}


# ---------------------------------------------------------------- documentation (FS-API-08)
TYPES = {"string": {"type": "string"}, "number": {"type": "number"}, "integer": {"type": "integer"},
         "array": {"type": "array", "items": {"type": "object"}}}  # a list of records (the MDM's readings, increment 47)


@endpoint("reading.write", write=True, summary="Send smart meters' readings from a meter data system (MDM). Each goes to the meter's reading in the newest "
          "open reading cycle and gets the same checks as any reading. Send an Idempotency-Key header.",
          params=[("readings", "array", True, "A list of {meter_no, reading, reading_date (YYYY-MM-DD), export_reading (net meters), mdi (kW)}; at most 5,000.")],
          returns={"accepted": 1, "rejected": 1, "readings": [{"meter_no": "SM-1001", "reading": "RDG-2026-000123", "status": "Valid", "units": 312.0}],
                   "errors": [{"meter_no": "SM-1002", "reason": "No open reading cycle has a reading for this meter"}]})
def post_meter_readings(client, readings):
	from utility_billing.utility_billing import readings as reading_rules  # noqa: PLC0415

	rows = frappe.parse_json(readings) if isinstance(readings, str) else readings
	return reading_rules.receive_mdm(rows, allowed=lambda society: check_society(client, society))


@frappe.whitelist(allow_guest=True)
def openapi():
	"""The OpenAPI 3 description of every method, generated from the code so it can't drift."""
	from utility_billing.api import channels  # noqa: F401, PLC0415 (registers the channel methods)

	paths = {}
	for spec in REGISTRY.values():
		op = {"summary": spec["summary"], "operationId": spec["name"], "tags": ["Channels" if ".channels." in spec["path"] else "Billing v1"],
		      "x-scope": spec["scope"], "security": [{"token": []}],
		      "responses": {"200": {"description": "OK", "content": {"application/json": {"example": {"message": spec["returns"]}}}},
		                    "403": {"description": "Missing scope, society or address not allowed"}, "409": {"description": "Idempotency key reused"},
		                    "417": {"description": "Refused (the message says why)"}, "429": {"description": "Rate limit"}}}
		params = [{"name": n, "in": "query", "required": req, "description": desc, "schema": TYPES[t]} for n, t, req, desc in spec["params"]]
		if spec["write"]:
			op["parameters"] = [{"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"},
			                     "description": "Unique per request; a retry with the same key returns the first answer."}] if spec["idempotent"] else []
			op["requestBody"] = {"required": True, "content": {"application/json": {"schema": {
				"type": "object", "required": [n for n, _t, req, _d in spec["params"] if req],
				"properties": {n: {**TYPES[t], "description": desc} for n, t, _r, desc in spec["params"]}}}}}
			paths[f"/api/method/{spec['path']}"] = {"post": op}
		else:
			op["parameters"] = params
			paths[f"/api/method/{spec['path']}"] = {"get": op}
	return {"openapi": "3.0.3",
	        "info": {"title": "Utility Management System billing API", "version": VERSION,
	                 "description": "For payment channels, the customer portal and other systems. Sign in with the integration user's API key and "
	                                "secret. Each client has scopes and societies; writes need an Idempotency-Key header. Test against the QA site."},
	        "components": {"securitySchemes": {"token": {"type": "apiKey", "in": "header", "name": "Authorization",
	                                                     "description": "token <api_key>:<api_secret>"}}},
	        "paths": dict(sorted(paths.items()))}
