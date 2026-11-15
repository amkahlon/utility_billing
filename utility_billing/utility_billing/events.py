# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Events for other systems (FS-API-02): bills issued, payments posted and reversed, disconnections and reconnections, service
request changes. Each event has a unique ID, type, time, society, reference number and data. An event is written to the outbox
only when an enabled subscription wants it, with one delivery per subscription; the scheduler posts the deliveries every few
minutes with an HMAC-SHA256 signature, retrying with back-off. Receivers de-duplicate by the event ID (X-UMS-Event-Id)."""

import hashlib
import hmac
import json

import frappe
from frappe.utils import add_to_date, cint, now_datetime

TYPES = ("bill.issued", "payment.posted", "payment.reversed", "connection.disconnected", "connection.reconnected", "service_request.updated")
BACKOFF_MINUTES = (1, 5, 15, 60, 240, 720)  # after the last one the delivery is marked Failed
TIMEOUT = 10


def subscriptions_for(event_type, society):
	"""Enabled subscriptions that want this event, of enabled clients with the events scope that may see the society."""
	out = []
	for s in frappe.get_all("Event Subscription", filters={"enabled": 1}, fields=["name", "client", "event_types"]):
		if event_type not in {x.strip() for x in (s.event_types or "").splitlines()}:
			continue
		client = frappe.get_cached_doc("API Client", s.client)
		if not cint(client.enabled) or not cint(client.scope_events):
			continue
		if not cint(client.all_societies) and society not in {r.society for r in client.societies}:
			continue
		out.append(s.name)
	return out


def emit(event_type, society, reference_no, data, doc=None):
	"""Write the event and its deliveries (in the same transaction as the change, so a rolled-back change sends nothing)."""
	subs = subscriptions_for(event_type, society)
	if not subs:
		return None
	ev = frappe.get_doc({"doctype": "API Event", "event_type": event_type, "society": society, "reference_no": reference_no,
	                     "document_type": doc.doctype if doc else None, "document_name": doc.name if doc else None,
	                     "data": json.dumps(data, default=str)}).insert(ignore_permissions=True)
	for s in subs:
		frappe.get_doc({"doctype": "API Event Delivery", "event": ev.name, "event_type": event_type, "subscription": s,
		                "status": "Pending", "next_attempt": now_datetime()}).insert(ignore_permissions=True)
	return ev.name


def payload(ev) -> dict:
	return {"id": ev.name, "type": ev.event_type, "time": str(ev.creation), "society": ev.society, "reference_no": ev.reference_no,
	        "data": json.loads(ev.data or "{}")}


def signature(secret: str, body: bytes) -> str:
	return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(limit=200):
	"""Scheduler: post the deliveries that are due."""
	import requests  # noqa: PLC0415

	due = frappe.get_all("API Event Delivery", filters={"status": "Pending", "next_attempt": ["<=", now_datetime()]},
	                     fields=["name", "event", "subscription", "attempts"], order_by="creation", limit=limit)
	for d in due:
		sub = frappe.get_doc("Event Subscription", d.subscription)
		ev = frappe.get_doc("API Event", d.event)
		body = json.dumps(payload(ev), separators=(",", ":")).encode()
		headers = {"Content-Type": "application/json", "X-UMS-Event-Id": ev.name, "X-UMS-Event-Type": ev.event_type,
		           "X-UMS-Signature": "sha256=" + signature(sub.get_password("secret"), body)}
		attempts = cint(d.attempts) + 1
		try:
			r = requests.post(sub.url, data=body, headers=headers, timeout=TIMEOUT)
			ok, error = 200 <= r.status_code < 300, None if 200 <= r.status_code < 300 else f"HTTP {r.status_code}: {r.text[:200]}"
		except Exception as e:
			ok, error = False, f"{type(e).__name__}: {str(e)[:200]}"
		if ok:
			frappe.db.set_value("API Event Delivery", d.name, {"status": "Delivered", "attempts": attempts, "delivered_on": now_datetime(), "last_error": None})
		elif attempts > len(BACKOFF_MINUTES):
			frappe.db.set_value("API Event Delivery", d.name, {"status": "Failed", "attempts": attempts, "last_error": error})
		else:
			frappe.db.set_value("API Event Delivery", d.name, {"attempts": attempts, "last_error": error,
			                                                   "next_attempt": add_to_date(now_datetime(), minutes=BACKOFF_MINUTES[attempts - 1])})
		frappe.db.commit()


@frappe.whitelist(methods=["POST"])
def retry(delivery: str):
	"""Send a failed delivery again (System Manager)."""
	frappe.only_for("System Manager")
	frappe.db.set_value("API Event Delivery", delivery, {"status": "Pending", "next_attempt": now_datetime()})


# ---------------------------------------------------------------- doc_events hooks
def bill_issued(doc, method=None):
	emit("bill.issued", doc.society, doc.connection, {"bill": doc.name, "billing_month": str(doc.billing_month)[:7], "issue_date": doc.issue_date,
	                                                  "due_date": doc.due_date, "payable_within_due": doc.payable_within_due,
	                                                  "payable_after_due": doc.payable_after_due}, doc)


def payment_posted(doc, method=None):
	emit("payment.posted", doc.society, doc.connection, {"receipt": doc.name, "amount": doc.amount, "paid_on": doc.payment_date, "mode": doc.mode,
	                                                     "transaction_id": doc.channel_txn_id}, doc)


def payment_reversed(doc, method=None):
	emit("payment.reversed", doc.society, doc.connection, {"receipt": doc.name, "amount": doc.amount, "transaction_id": doc.channel_txn_id,
	                                                       "reason": (doc.flags.reversal or {}).get("reason_code")}, doc)


def connection_changed(doc, method=None):
	before = doc.get_doc_before_save()
	if not before or before.status == doc.status:
		return
	if doc.status == "Disconnected":
		emit("connection.disconnected", doc.society, doc.name, {"status": doc.status, "disconnected_on": doc.get("disconnected_on")}, doc)
	elif before.status == "Disconnected" and doc.status == "Active":
		emit("connection.reconnected", doc.society, doc.name, {"status": doc.status}, doc)


def service_request_changed(doc, method=None):
	before = doc.get_doc_before_save()
	if before and before.status == doc.status and before.current_step == doc.current_step:
		return
	emit("service_request.updated", doc.society, doc.connection, {"request": doc.name, "status": doc.status, "current_step": doc.current_step}, doc)
