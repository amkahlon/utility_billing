# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""SoftVerx's own billing of the societies that use the software (gap 14, FS-SAS-02, 03; increment 24).

User decisions 2026-11-16: SoftVerx charges **per active account per month** (with a monthly minimum), and invoices from **one control
site**. The same app runs on both kinds of site; Licence Settings says which this one is.

- A society's site counts, for each month, the accounts active on any day of it (per society and utility), keeps that as a Licence
  Usage and sends it to the control site with the site's licence key. It tries again every day until the control site accepts it.
- The control site finds the client by the key (only its hash is stored), keeps the usage, raises the month's invoice from the
  client's invoice day (accounts × rate, the monthly minimum, tax), takes payments against the oldest invoices first and marks
  invoices overdue. Usage that hasn't arrived is listed, so nobody is forgotten."""

import hashlib
import secrets
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_last_day, getdate, now_datetime, nowdate
from utility_billing.utility_billing import rights

SITE, CONTROL = "A society's site", "The SoftVerx control site"
KEY_HEADER = "X-UMS-Licence-Key"
COUNTED_STATUSES = ("Active", "Disconnected", "Closed")


def settings():
	return frappe.get_cached_doc("Licence Settings")


def is_control() -> bool:
	return settings().mode == CONTROL


def month_start(d):
	return getdate(d).replace(day=1)


def key_hash(key: str) -> str:
	return hashlib.sha256((key or "").strip().encode()).hexdigest()


def require_manager():
	if not rights.can("licence.manage"):
		raise frappe.PermissionError(_("Only a Licence Manager does this."))


# ---------------------------------------------------------------- a society's site: counting and sending
def count_usage(month) -> list[dict]:
	"""[{society, utility, accounts}]: accounts active on any day of the month (connected by its end, not disconnected before its start)."""
	start, end = month_start(month), get_last_day(month)
	rows = frappe.db.sql(
		"""select society, utility, count(*) n from `tabConnection`
		where status in %(st)s and (connection_date is null or connection_date <= %(end)s)
		and (disconnected_on is null or disconnected_on >= %(start)s)
		group by society, utility order by society, utility""", {"st": COUNTED_STATUSES, "start": start, "end": end}, as_dict=True)
	return [{"society": r.society, "utility": r.utility, "accounts": cint(r.n)} for r in rows if cint(r.n)]


def record_usage(month):
	"""This site's Licence Usage for the month, counted again unless the control site has already accepted it."""
	month = month_start(month)
	name = frappe.db.get_value("Licence Usage", {"month": month, "client": ["is", "not set"]})
	doc = frappe.get_doc("Licence Usage", name) if name else frappe.new_doc("Licence Usage")
	if doc.status == "Accepted":
		return doc
	lines = count_usage(month)
	doc.update({"month": month, "status": doc.status or "Counted", "accounts": sum(x["accounts"] for x in lines)})
	doc.set("lines", lines)
	doc.flags.ignore_permissions = True
	doc.save()
	return doc


def send_usage(usage):
	"""Send a Licence Usage to the control site. Returns True when it was accepted."""
	import requests  # noqa: PLC0415

	s = settings()
	key = s.get_password("licence_key", raise_exception=False) if s.licence_key else None
	if not (s.control_url and key):
		return False
	payload = {"month": str(usage.month), "site": frappe.local.site,
	           "lines": [{"society": x.society, "utility": x.utility, "accounts": cint(x.accounts)} for x in usage.lines]}
	url = s.control_url.rstrip("/") + "/api/method/utility_billing.utility_billing.licensing.receive_usage"
	try:
		r = requests.post(url, json=payload, headers={KEY_HEADER: key}, timeout=30)
		reply = (r.json() or {}).get("message") or {} if r.headers.get("content-type", "").startswith("application/json") else {}
	except Exception as e:  # noqa: BLE001
		r, reply = None, {"error": str(e)[:300]}
	accepted = bool(r is not None and r.status_code == 200 and reply.get("accepted"))
	usage.db_set({"status": "Accepted" if accepted else "Sent", "sent_on": now_datetime(),
	              "response": frappe.as_json(reply)[:1000] if reply else (r.text[:500] if r is not None else "")})
	frappe.db.set_single_value("Licence Settings", {"last_error": None if accepted else (reply.get("error") or f"HTTP {getattr(r, 'status_code', '-')}"),
	                                                 **({"last_sent_month": usage.month} if accepted else {})})
	frappe.clear_document_cache("Licence Settings", "Licence Settings")
	return accepted


@frappe.whitelist(methods=["POST"])
def send_now(month: str | None = None):
	"""The Send Usage Now button: last month (or the given one) counted and sent."""
	if is_control():
		frappe.throw(_("The control site receives usage; it doesn't send any."))
	if not rights.can("licence.settings"):
		raise frappe.PermissionError(_("Only a Society Admin sends the licence usage."))
	usage = record_usage(month or add_months(month_start(nowdate()), -1))
	return {"usage": usage.name, "accepted": send_usage(usage)}


# ---------------------------------------------------------------- the control site: receiving
@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_usage(month=None, site=None, lines=None):
	"""Called by a society's site with its licence key in the X-UMS-Licence-Key header."""
	if not is_control():
		frappe.throw(_("This isn't the SoftVerx control site."), frappe.PermissionError)
	key = frappe.get_request_header(KEY_HEADER) if frappe.request else frappe.flags.licence_key  # (tests pass the key in a flag)
	client = frappe.db.get_value("Licence Client", {"key_hash": key_hash(key)}, ["name", "status"], as_dict=True) if (key or "").strip() else None
	if not client:
		frappe.throw(_("Unknown licence key."), frappe.AuthenticationError)
	lines = frappe.parse_json(lines) if isinstance(lines, str) else (lines or [])
	month = month_start(month)
	if month >= month_start(nowdate()):
		frappe.throw(_("Usage is reported for a finished month."))
	clean = [{"society": str(x.get("society") or "")[:140], "utility": str(x.get("utility") or "")[:140], "accounts": max(cint(x.get("accounts")), 0)}
	         for x in lines]
	frappe.set_user("Administrator")  # the key is the caller's identity; nothing else is reachable from here
	name = frappe.db.get_value("Licence Usage", {"client": client.name, "month": month})
	doc = frappe.get_doc("Licence Usage", name) if name else frappe.new_doc("Licence Usage")
	if doc.status == "Invoiced":
		return {"accepted": True, "usage": doc.name, "note": "already invoiced"}
	doc.update({"client": client.name, "month": month, "status": "Received", "sent_on": now_datetime(),
	            "accounts": sum(x["accounts"] for x in clean), "response": f"from {site or '-'}"})
	doc.set("lines", clean)
	doc.flags.ignore_permissions = True
	doc.save()
	return {"accepted": True, "usage": doc.name, "accounts": doc.accounts}


@frappe.whitelist(methods=["POST"])
def new_key(client: str):
	"""A new licence key for a client, shown once; only its hash is kept. The site's old key stops working."""
	require_manager()
	key = "ums_" + secrets.token_urlsafe(32)
	frappe.db.set_value("Licence Client", client, {"key_hash": key_hash(key), "key_hint": key[-4:], "key_made_on": now_datetime()})
	return {"key": key}


# ---------------------------------------------------------------- the control site: invoices and payments
def raise_invoices(as_of=None) -> list[str]:
	"""From the invoice day on, an invoice for every active client whose usage for last month has arrived and isn't invoiced."""
	as_of = getdate(as_of or nowdate())
	if as_of.day < (cint(settings().invoice_day) or 1):
		return []
	month = add_months(month_start(as_of), -1)
	made = []
	for u in frappe.get_all("Licence Usage", filters={"month": month, "status": "Received", "client": ["is", "set"]}, fields=["name", "client", "accounts"]):
		if frappe.db.get_value("Licence Client", u.client, "status") != "Active":
			continue
		if frappe.db.exists("Licence Invoice", {"client": u.client, "month": month, "docstatus": ["<", 2]}):
			continue
		inv = frappe.get_doc({"doctype": "Licence Invoice", "client": u.client, "month": month, "usage": u.name, "accounts": u.accounts,
		                      "invoice_date": as_of})
		inv.flags.ignore_permissions = True
		inv.insert()
		inv.submit()
		made.append(inv.name)
	return made


def missing_usage(month) -> list[str]:
	"""Active clients whose usage for the month hasn't arrived."""
	month = month_start(month)
	have = set(frappe.get_all("Licence Usage", filters={"month": month, "client": ["is", "set"]}, pluck="client"))
	return [c for c in frappe.get_all("Licence Client", filters={"status": "Active", "start_date": ["<=", get_last_day(month)]}, pluck="name")
	        if c not in have]


def invoice_status(outstanding, total, due_date, as_of=None) -> str:
	if flt(outstanding) <= 0.005:
		return "Paid"
	if getdate(due_date) < getdate(as_of or nowdate()):
		return "Overdue"
	return "Part Paid" if flt(outstanding) < flt(total) - 0.005 else "Unpaid"


def mark_overdue(as_of=None):
	for inv in frappe.get_all("Licence Invoice", filters={"docstatus": 1, "status": ["in", ["Unpaid", "Part Paid"]], "due_date": ["<", getdate(as_of or nowdate())]},
	                          pluck="name"):
		frappe.db.set_value("Licence Invoice", inv, "status", "Overdue", update_modified=False)


def outstanding_invoices(client):
	return frappe.get_all("Licence Invoice", filters={"client": client, "docstatus": 1, "outstanding": [">", 0.005]},
	                      fields=["name", "outstanding"], order_by="invoice_date, name")


def apply_to_invoice(invoice, amount):
	"""Add (or, negative, take back) a payment on an invoice."""
	inv = frappe.db.get_value("Licence Invoice", invoice, ["total", "paid_amount", "due_date"], as_dict=True)
	paid = round(flt(inv.paid_amount) + flt(amount), 2)
	out = round(flt(inv.total) - paid, 2)
	frappe.db.set_value("Licence Invoice", invoice, {"paid_amount": paid, "outstanding": out,
	                                                 "status": invoice_status(out, inv.total, inv.due_date)}, update_modified=False)


def daily(as_of=None):
	"""Scheduler: a society's site reports last month; the control site invoices and marks overdue invoices."""
	if not frappe.db.exists("DocType", "Licence Settings"):  # (a single DocType has no table of its own)
		return
	if is_control():
		raise_invoices(as_of)
		mark_overdue(as_of)
		return
	s = settings()
	last = add_months(month_start(as_of or nowdate()), -1)
	if not s.control_url or (s.last_sent_month and getdate(s.last_sent_month) >= last):
		return
	send_usage(record_usage(last))


# ---------------------------------------------------------------- invoice PDF
def invoice_html(name) -> str:
	inv = frappe.get_doc("Licence Invoice", name)
	return frappe.render_template("utility_billing/templates/bill/licence_invoice.html", {
		"inv": inv, "client": frappe.get_doc("Licence Client", inv.client), "seller": settings(),
		"usage": frappe.get_doc("Licence Usage", inv.usage) if inv.usage else None,
		"month": getdate(inv.month).strftime("%B %Y"), "money": lambda v: f"{flt(v):,.2f}",
		"date": lambda d: getdate(d).strftime("%d-%m-%Y") if d else ""})


@frappe.whitelist()
def download_invoice(name: str):
	frappe.get_doc("Licence Invoice", name).check_permission("print")
	from utility_billing import pdf as ubpdf  # noqa: PLC0415

	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	html = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + invoice_html(name) + "</body></html>"
	bill_print.send_pdf(ubpdf.render(html, options=dict(bill_print.PDF_OPTIONS)), f"{name}.pdf")


def receivables(as_of=None) -> list[dict]:
	"""Per client: invoiced, paid, outstanding, by age of the due date (Licence Receivables report)."""
	as_of = getdate(as_of or nowdate())
	out = defaultdict(lambda: {"invoiced": 0.0, "paid": 0.0, "outstanding": 0.0, "current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0})
	for i in frappe.get_all("Licence Invoice", filters={"docstatus": 1}, fields=["client", "total", "paid_amount", "outstanding", "due_date"]):
		row = out[i.client]
		row["invoiced"] += flt(i.total)
		row["paid"] += flt(i.paid_amount)
		row["outstanding"] += flt(i.outstanding)
		late = (as_of - getdate(i.due_date)).days
		bucket = "current" if late <= 0 else "d30" if late <= 30 else "d60" if late <= 60 else "d90"
		row[bucket] += flt(i.outstanding)
	return [{"client": c, **{k: round(v, 2) for k, v in r.items()}} for c, r in sorted(out.items())]


def due_date_for(invoice_date, client) -> object:
	return add_days(invoice_date, cint(frappe.db.get_value("Licence Client", client, "payment_days")) or 15)
