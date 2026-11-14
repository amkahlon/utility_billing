# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bank and channel settlement files, and the suspense list (increment 12; FS-PAY-10, 11, and the fee column of FS-PAY-14).

A **Settlement File** holds one day's file from a bank or channel (CSV: transaction_id, reference_no, amount, date, and
optionally fee). Importing it matches each row to the payment receipts recorded with the same channel transaction ID:
- Matched: recorded for the same amount;
- Amount differs: recorded, but for another amount;
- Not recorded: the money reached the bank but no receipt exists;
- Recorded but reversed: the receipt was reversed;
- Duplicate in file: the same transaction ID twice in the file;
- Missing from file: a receipt recorded for this channel in the file's period that the file does not list.
Rows "Not recorded" are then posted as payments when their reference number is a real account of the society, and go to the
**Suspense Payment** list when it is not (wrong or missing reference), until someone matches them (FS-PAY-11). A file
can be closed only when every flagged row has an action or a remark."""

import csv
import io
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from utility_billing.utility_billing import account

POSTERS = ["Accounts Officer"]
HANDLERS = ["Accounts Officer", "Billing Manager"]
TOLERANCE = 0.005
ALIASES = {
	"txn_id": ("transaction_id", "txn_id", "transaction id", "trx_id", "transaction_no", "rrn", "stan", "tran_id"),
	"reference_no": ("reference_no", "reference", "consumer_no", "consumer_reference", "consumer reference", "customer_ref", "reference no"),
	"amount": ("amount", "paid_amount", "amount_paid", "credit"),
	"paid_on": ("date", "paid_on", "payment_date", "transaction_date", "settlement_date"),
	"fee": ("fee", "fees", "commission", "charges"),
}


def can(roles) -> bool:
	return bool(set(frappe.get_roles()) & set(roles)) or frappe.session.user == "Administrator"


# ---------------------------------------------------------------- pure parts (unit tested)
def parse_date(text):
	"""YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY or YYYYMMDD."""
	t = (text or "").strip()
	for pattern, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})", (0, 1, 2)), (r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", (2, 1, 0)), (r"^(\d{4})(\d{2})(\d{2})$", (0, 1, 2))):
		m = re.match(pattern, t)
		if m:
			g = m.groups()
			try:
				return getdate(f"{int(g[order[0]]):04d}-{int(g[order[1]]):02d}-{int(g[order[2]]):02d}")
			except Exception:
				break
	raise ValueError(f"Unreadable date: {text!r}")


def parse_amount(text) -> float:
	t = str(text or "").replace(",", "").replace("Rs", "").strip()
	if not t:
		raise ValueError("Empty amount")
	return round(float(t), 2)


def _norm(name: str) -> str:
	return re.sub(r"\s+", "_", (name or "").strip().lower())


def parse_csv(content: str) -> list[dict]:
	"""Rows of a settlement CSV as dicts with txn_id, reference_no, amount, paid_on (date), fee. Raises ValueError with a line
	number and reason when the file is unusable."""
	reader = csv.reader(io.StringIO(content.lstrip("﻿")))
	try:
		header = [_norm(h) for h in next(reader)]
	except StopIteration:
		raise ValueError("The file is empty.") from None
	index = {}
	for key, names in ALIASES.items():
		wanted = {_norm(n) for n in names}
		for i, h in enumerate(header):
			if h in wanted:
				index[key] = i
				break
	missing = [k for k in ("txn_id", "amount", "paid_on") if k not in index]
	if missing:
		raise ValueError("Missing column(s): " + ", ".join({"txn_id": "transaction_id", "amount": "amount", "paid_on": "date"}[k] for k in missing))
	rows = []
	for n, line in enumerate(reader, start=2):
		if not any(c.strip() for c in line):
			continue
		get = lambda k: line[index[k]].strip() if k in index and index[k] < len(line) else ""  # noqa: E731
		try:
			rows.append({"txn_id": get("txn_id"), "reference_no": get("reference_no"), "amount": parse_amount(get("amount")),
			             "paid_on": parse_date(get("paid_on")), "fee": parse_amount(get("fee")) if get("fee") else 0.0})
		except ValueError as e:
			raise ValueError(f"Line {n}: {e}") from None
		if not rows[-1]["txn_id"]:
			raise ValueError(f"Line {n}: the transaction ID is empty.")
	if not rows:
		raise ValueError("The file has no rows.")
	return rows


def classify(rows, recorded) -> list[str]:
	"""Result for each file row. `recorded` maps transaction ID to (receipt name, amount, docstatus)."""
	seen, out = set(), []
	for r in rows:
		t = r["txn_id"]
		if t in seen:
			out.append("Duplicate in file")
			continue
		seen.add(t)
		rec = recorded.get(t)
		if not rec:
			out.append("Not recorded")
		elif rec[2] == 2:
			out.append("Recorded but reversed")
		elif abs(flt(rec[1]) - flt(r["amount"])) > TOLERANCE:
			out.append("Amount differs")
		else:
			out.append("Matched")
	return out


def missing_from_file(receipts, listed_txns, start, end):
	"""Receipts of the channel in [start, end] whose transaction ID the file does not list. `receipts` are dicts with
	name, channel_txn_id, payment_date, amount."""
	return [r for r in receipts if r["channel_txn_id"] not in listed_txns and start <= getdate(r["payment_date"]) <= end]


# ---------------------------------------------------------------- the file's data
def recorded_receipts(society, mode):
	return frappe.db.sql("""select name, channel_txn_id, amount, docstatus, payment_date from `tabPayment Receipt`
	                        where society = %s and mode = %s and payment_channel = 'Bank / Channel' and ifnull(channel_txn_id, '') != ''
	                          and docstatus in (1, 2)""", (society, mode), as_dict=True)


def read_file(doc) -> str:
	if not doc.settlement_file:
		frappe.throw(_("Attach the settlement file (CSV) first."))
	file_doc = frappe.get_doc("File", {"file_url": doc.settlement_file})
	raw = file_doc.get_content()
	if isinstance(raw, bytes):
		try:
			return raw.decode("utf-8-sig")
		except UnicodeDecodeError:
			return raw.decode("latin-1")
	return raw


def import_rows(doc, content: str | None = None):
	"""Read the attached file and match it to the recorded receipts. Replaces earlier rows of a draft or matched file."""
	if doc.status == "Closed":
		frappe.throw(_("This settlement file is closed."))
	try:
		rows = parse_csv(content if content is not None else read_file(doc))
	except ValueError as e:
		frappe.throw(_("The settlement file can't be read: {0}").format(str(e)))
	receipts = recorded_receipts(doc.society, doc.mode)
	recorded = {r.channel_txn_id: (r.name, r.amount, r.docstatus) for r in receipts}
	results = classify(rows, recorded)
	doc.rows = []
	for r, result in zip(rows, results):
		rec = recorded.get(r["txn_id"])
		doc.append("rows", {"txn_id": r["txn_id"], "reference_no": r["reference_no"], "paid_on": r["paid_on"], "amount": r["amount"], "fee": r["fee"],
		                    "result": result, "receipt": rec[0] if rec and result != "Duplicate in file" else None,
		                    "recorded_amount": flt(rec[1]) if rec and result != "Duplicate in file" else None})
	listed = {r["txn_id"] for r in rows}
	start, end = min(r["paid_on"] for r in rows), max(r["paid_on"] for r in rows)
	for r in missing_from_file([x for x in receipts if x.docstatus == 1], listed, start, end):
		doc.append("rows", {"txn_id": r["channel_txn_id"], "paid_on": r["payment_date"], "amount": 0, "result": "Missing from file",
		                    "receipt": r["name"], "recorded_amount": flt(r["amount"])})
	refresh_counts(doc)
	doc.status = "Matched"


def refresh_counts(doc):
	count = lambda res: sum(1 for r in doc.rows if r.result == res)  # noqa: E731
	file_rows = [r for r in doc.rows if r.result != "Missing from file"]
	doc.rows_in_file = len(file_rows)
	doc.matched, doc.amount_differs, doc.not_recorded = count("Matched"), count("Amount differs"), count("Not recorded")
	doc.missing_from_file = count("Missing from file")
	doc.other_flags = count("Recorded but reversed") + count("Duplicate in file")
	doc.total_amount = round(sum(flt(r.amount) for r in file_rows if r.result != "Duplicate in file"), 2)
	doc.total_fees = round(sum(flt(r.fee) for r in file_rows if r.result != "Duplicate in file"), 2)


def post_unrecorded(doc):
	"""For each 'Not recorded' row: post the payment when its reference number is a real account of this society, else hold
	it in suspense (FS-PAY-10, 11). Money that reached the bank is never dropped."""
	if not can(POSTERS):
		frappe.throw(_("Only an Accounts Officer can post payments from a settlement file."), frappe.PermissionError)
	if doc.status == "Closed":
		frappe.throw(_("This settlement file is closed."))
	posted = held = 0
	for row in doc.rows:
		if row.result != "Not recorded" or row.action_taken:
			continue
		conn = account.connection_of((row.reference_no or "").strip()) if (row.reference_no or "").strip() else None
		if conn and frappe.db.get_value("Connection", conn, "society") == doc.society and flt(row.amount) > 0:
			r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": row.reference_no.strip(), "payment_channel": "Bank / Channel",
			                    "payment_date": row.paid_on, "amount": row.amount, "mode": doc.mode, "channel_txn_id": row.txn_id,
			                    "remarks": _("From settlement file {0}").format(doc.name)})
			r.insert()
			r.submit()
			row.receipt, row.recorded_amount, row.result, row.action_taken = r.name, flt(row.amount), "Matched", "Posted"
			posted += 1
		else:
			s = new_suspense(doc.society, row.amount, row.paid_on, doc.mode, row.txn_id, row.reference_no, settlement_file=doc.name)
			row.suspense, row.action_taken = s.name, "Suspense"
			held += 1
	refresh_counts(doc)
	return {"posted": posted, "suspense": held}


def close_file(doc):
	if not can(HANDLERS):
		frappe.throw(_("Only an Accounts Officer or Billing Manager can close a settlement file."), frappe.PermissionError)
	if doc.status != "Matched":
		frappe.throw(_("Import the file before closing it."))
	open_rows = [r.idx for r in doc.rows if r.result != "Matched" and not (r.action_taken or (r.remarks or "").strip())]
	if open_rows:
		frappe.throw(_("Rows {0} are flagged and have no action or remark. Post them, or write what was decided.").format(", ".join(map(str, open_rows[:15]))))
	doc.status, doc.closed_by, doc.closed_on = "Closed", frappe.session.user, now_datetime()


# ---------------------------------------------------------------- suspense (FS-PAY-11)
def new_suspense(society, amount, paid_on, mode, txn_id, reference_text=None, settlement_file=None):
	s = frappe.get_doc({"doctype": "Suspense Payment", "society": society, "amount": amount, "payment_date": paid_on, "mode": mode,
	                    "channel_txn_id": txn_id, "reference_text": reference_text, "settlement_file": settlement_file})
	s.insert(ignore_permissions=True)
	return s


def match_suspense(s, connection):
	"""Post a held payment to the account it belongs to."""
	if not can(HANDLERS):
		frappe.throw(_("Only an Accounts Officer or Billing Manager can match a suspense payment."), frappe.PermissionError)
	if s.status != "Held":
		frappe.throw(_("This payment is already {0}.").format(s.status.lower()))
	c = frappe.db.get_value("Connection", connection, ["society", "reference_no"], as_dict=True)
	if not c or c.society != s.society:
		frappe.throw(_("Choose an account of {0}.").format(s.society))
	if not can(POSTERS):
		frappe.throw(_("Posting the payment needs an Accounts Officer."), frappe.PermissionError)
	r = frappe.get_doc({"doctype": "Payment Receipt", "reference_no": c.reference_no, "payment_channel": "Bank / Channel", "payment_date": s.payment_date,
	                    "amount": s.amount, "mode": s.mode, "channel_txn_id": s.channel_txn_id, "remarks": _("From suspense {0}").format(s.name)})
	r.insert()
	r.submit()
	s.status, s.matched_connection, s.payment_receipt = "Matched", connection, r.name
	s.resolved_by, s.resolved_on = frappe.session.user, now_datetime()
	_sync_rows(s, r.name)
	return r


def return_suspense(s, remarks):
	if not can(HANDLERS):
		frappe.throw(_("Only an Accounts Officer or Billing Manager can return a suspense payment."), frappe.PermissionError)
	if s.status != "Held":
		frappe.throw(_("This payment is already {0}.").format(s.status.lower()))
	if not (remarks or "").strip():
		frappe.throw(_("Say how the money was returned or why."))
	s.status, s.remarks, s.resolved_by, s.resolved_on = "Returned to Payer", remarks.strip(), frappe.session.user, now_datetime()


def _sync_rows(s, receipt):
	"""Rows of settlement files that pointed at this suspense payment now point at the receipt."""
	parents = set()
	for r in frappe.get_all("Settlement Row", filters={"suspense": s.name}, fields=["name", "parent"]):
		frappe.db.set_value("Settlement Row", r.name, {"receipt": receipt, "result": "Matched", "recorded_amount": s.amount}, update_modified=False)
		parents.add(r.parent)
	for p in parents:  # the file's own counts follow its rows
		f = frappe.get_doc("Settlement File", p)
		refresh_counts(f)
		frappe.db.set_value("Settlement File", p, {k: f.get(k) for k in ("rows_in_file", "matched", "amount_differs", "not_recorded", "missing_from_file",
		                                                                "other_flags", "total_amount", "total_fees")}, update_modified=False)
