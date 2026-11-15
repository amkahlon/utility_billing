# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Opening balances at go-live (gap 9): what each account owed, its credit, the security deposit held and any installment plan
running, brought over from the society's previous system.

A Billing Manager or Accounts Officer loads a CSV or Excel file into an Opening Balance Import, checks it (every problem is listed
against its row and can be downloaded), and submits it; a General Manager approves it. Approval posts, in one transaction:
an "Opening" ledger entry per account dated the as-at day, the security deposits held, the installment plans still running, an
opening journal (receivables and deposits against the society's opening balances account) and the society's go-live date, after
which nothing can be dated on or before the as-at day. A posted import is not cancelled: mistakes are corrected with credit and
debit notes like any other balance."""

import io

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, getdate, now_datetime

from utility_billing.utility_billing import account, gl, periods, plans
from utility_billing.utility_billing.readings import month_start

AMOUNTS = ("current_bill", "arrears", "surcharge", "fees", "credit", "security_deposit", "plan_remaining")
COLUMNS = ("reference_no",) + AMOUNTS + ("plan_installments",)
ALIASES = {"reference": "reference_no", "reference number": "reference_no", "consumer number": "reference_no", "account": "reference_no",
           "current": "current_bill", "current bill": "current_bill", "deposit": "security_deposit", "security deposit": "security_deposit",
           "plan": "plan_remaining", "installments": "plan_installments", "installments left": "plan_installments"}
BUCKETS = {"current_bill": "current", "arrears": "arrears", "surcharge": "surcharge", "fees": "fees"}
WRITERS = {"System Manager", "Billing Manager", "Accounts Officer"}


def column_of(header):
	h = " ".join(str(header or "").strip().lower().replace("_", " ").split())
	key = h.replace(" ", "_")
	return key if key in COLUMNS else ALIASES.get(h)


def rows_from_table(table) -> list[dict]:
	"""[[header...], [values...], ...] -> row dicts with the known columns (blank lines skipped)."""
	if not table:
		frappe.throw(_("The file is empty."))
	cols = [column_of(h) for h in table[0]]
	if "reference_no" not in cols:
		frappe.throw(_("The file needs a reference_no column. Columns: {0}.").format(", ".join(COLUMNS)))
	out = []
	for line in table[1:]:
		if not any(str(v or "").strip() for v in line):
			continue
		row = {c: v for c, v in zip(cols, line, strict=False) if c}
		row["reference_no"] = str(row.get("reference_no") or "").strip().split(".")[0]  # Excel may hand back 1000000016.0
		for k in AMOUNTS:
			row[k] = _number(row.get(k))
		row["plan_installments"] = cint(_number(row.get("plan_installments")))
		out.append(row)
	return out


def _number(v):
	if v in (None, ""):
		return 0
	try:
		return float(str(v).replace(",", "").strip())  # not flt(): it turns "abc" into 0 silently
	except ValueError:
		return str(v)  # left as text: the check reports it


def read_file(file_url) -> list[list]:
	f = frappe.get_doc("File", {"file_url": file_url})
	content = f.get_content()
	name = (f.file_name or file_url).lower()
	if name.endswith(".xlsx"):
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file  # noqa: PLC0415

		return read_xlsx_file_from_attached_file(fcontent=content)
	if name.endswith(".csv"):
		from frappe.utils.csvutils import read_csv_content  # noqa: PLC0415

		return read_csv_content(content)
	frappe.throw(_("Attach a CSV or Excel (.xlsx) file."))


# ---------------------------------------------------------------- checks
def check(doc) -> int:
	"""Check every row; fill the account, consumer, status and problem of each and the totals. Returns the number of rows with problems."""
	soc = frappe.get_cached_doc("Society", doc.society)
	max_inst = cint(soc.get("max_installments")) or 36
	others = set(frappe.db.sql_list("""select r.connection from `tabOpening Balance Row` r join `tabOpening Balance Import` i on i.name = r.parent
	                                   where i.name != %s and i.docstatus < 2 and ifnull(r.connection, '') != ''""", doc.name or ""))
	seen = set()
	errors = 0
	for r in doc.rows:
		problems = []
		r.reference_no = (r.reference_no or "").strip()
		conn = account.connection_of(r.reference_no) if r.reference_no else None
		r.connection, r.consumer_name = conn, None
		if not r.reference_no:
			problems.append(_("no reference number"))
		elif not conn:
			problems.append(_("no account with this reference number"))
		else:
			c = frappe.db.get_value("Connection", conn, ["society", "consumer_name", "status"], as_dict=True)
			r.consumer_name = c.consumer_name
			if c.society != doc.society:
				problems.append(_("the account belongs to {0}").format(c.society))
			if conn in seen:
				problems.append(_("the account is listed twice"))
			if conn in others:
				problems.append(_("the account is in another opening balance import"))
			if frappe.db.exists("Customer Ledger Entry", {"connection": conn}):
				problems.append(_("the account already has entries in this system"))
			seen.add(conn)
		for k in AMOUNTS:
			v = r.get(k)
			if isinstance(v, str):
				problems.append(_("{0} isn't a number").format(_(k.replace("_", " "))))
				r.set(k, 0)
			elif flt(v) < 0:
				problems.append(_("{0} can't be negative").format(_(k.replace("_", " "))))
		owes = sum(flt(r.get(k)) for k in BUCKETS)
		if flt(r.credit) > 0 and owes > 0:
			problems.append(_("an account either owes or is in credit, not both"))
		if flt(r.plan_remaining) > 0:
			if not 1 <= cint(r.plan_installments) <= max_inst:
				problems.append(_("installments left must be from 1 to {0}").format(max_inst))
			if flt(r.plan_remaining) > flt(r.arrears) + flt(r.surcharge) + flt(r.fees) + 0.005:
				problems.append(_("the plan can't be for more than the arrears, surcharge and fees"))
		elif cint(r.plan_installments):
			problems.append(_("installments left without an amount still to pay"))
		if not problems and not owes and not flt(r.credit) and not flt(r.security_deposit):
			problems.append(_("nothing to bring over"))
		r.row_status = "Error" if problems else "OK"
		r.error = "; ".join(problems) or None
		errors += bool(problems)
	doc.row_count, doc.error_count = len(doc.rows), errors
	for field, key in (("total_current", "current_bill"), ("total_arrears", "arrears"), ("total_surcharge", "surcharge"), ("total_fees", "fees"),
	                   ("total_credit", "credit"), ("total_deposits", "security_deposit"), ("total_plans", "plan_remaining")):
		doc.set(field, round(sum(flt(r.get(key)) for r in doc.rows), 2))
	doc.total_receivable = round(doc.total_current + doc.total_arrears + doc.total_surcharge + doc.total_fees - doc.total_credit, 2)
	return errors


def check_accounts(doc):
	"""The journal accounts must be mapped before posting (the whole posting is refused otherwise)."""
	s = gl.settings(doc.society)
	gl.need(s.receivables, _("receivables (Society {0} → Accounting)").format(doc.society))
	gl.need(s.opening, _("opening balances (Society {0} → Go-live → Opening Balances Account)").format(doc.society))
	if flt(doc.total_deposits):
		gl.need(s.deposits, _("security deposits held (Society {0} → Security Deposits Held Account)").format(doc.society))


# ---------------------------------------------------------------- posting (on approval)
def post(doc):
	as_of = getdate(doc.as_of_date)
	approver = frappe.session.user
	entries = []
	for r in doc.rows:
		deltas = {BUCKETS[k]: flt(r.get(k)) for k in BUCKETS}
		deltas["credit"] = flt(r.credit)
		if any(deltas.values()):
			account.lock(r.connection)
			e = account.post(r.connection, "Opening", deltas, "Opening Balance Import", doc.name, posting_date=as_of,
			                 remarks=_("Opening balance brought over at go-live ({0})").format(doc.name))
			r.db_set("ledger_entry", e.name, update_modified=False)
			entries.append(e.name)
	journal = gl.build_journal(doc.society, entries, "Opening Balances", as_of, _("Opening balances at go-live, as at {0} ({1})").format(
		frappe.utils.formatdate(as_of), doc.name)) if entries else None
	deposit_journal = _deposits(doc, as_of, approver)
	for r in doc.rows:
		if flt(r.plan_remaining) > 0:
			_plan(doc, r, as_of, approver)
	frappe.db.set_value("Society", doc.society, "go_live_date", add_days(as_of, 1))
	frappe.clear_document_cache("Society", doc.society)
	periods.clear_cache()
	doc.db_set({"posted_on": now_datetime(), "gl_journal": journal.name if journal else None, "deposit_journal": deposit_journal}, update_modified=False)


def _deposits(doc, as_of, approver):
	rows = [r for r in doc.rows if flt(r.security_deposit) > 0]
	if not rows:
		return None
	names = []
	for r in rows:
		sd = frappe.get_doc({"doctype": "Security Deposit", "connection": r.connection, "consumer_name": r.consumer_name, "society": doc.society,
		                     "deposit_type": "Received", "amount": flt(r.security_deposit), "mode": "Opening balance", "posting_date": as_of,
		                     "remarks": _("Held at go-live, brought over by {0}").format(doc.name), "balance_before": 0, "balance_after": flt(r.security_deposit),
		                     "workflow_state": "Approved", "approved_by": approver, "approved_on": now_datetime(), "requested_by": doc.requested_by,
		                     "deposit_title": f"{_('Received')} {r.consumer_name or r.connection} Rs {flt(r.security_deposit):,.0f}"})
		sd.set_new_name()
		sd.docstatus = 1
		sd.db_insert()  # the import's approval is the approval: no second workflow per deposit
		frappe.db.set_value("Connection", r.connection, "security_deposit", flt(r.security_deposit), update_modified=False)
		names.append(sd.name)
	s = gl.settings(doc.society)
	total = round(sum(flt(r.security_deposit) for r in rows), 2)
	narration = _("Security deposits held at go-live ({0})").format(doc.name)
	lines = [{"account": a, "account_name": frappe.db.get_value("Ledger Account", a, "account_name"), "debit": dr, "credit": cr, "narration": narration}
	         for a, dr, cr in ((s.opening, total, 0), (s.deposits, 0, total))]
	j = frappe.get_doc({"doctype": "GL Journal", "journal_type": "Opening Balances", "society": doc.society, "posting_date": as_of, "narration": narration,
	                    "entry_count": len(rows), "lines": lines, "total_debit": total, "total_credit": total, "receivables_change": 0,
	                    "journal_title": f"{doc.society} Opening Deposits {frappe.utils.formatdate(as_of)}"})
	j.flags.from_gl = True
	j.insert(ignore_permissions=True)
	for n in names:
		frappe.db.set_value("Security Deposit", n, "gl_journal", j.name, update_modified=False)
	return j.name


def _plan(doc, r, as_of, approver):
	first = month_start(add_months(as_of, 1))
	n = cint(r.plan_installments)
	rows = plans.schedule(flt(r.plan_remaining), 0, n, first)
	p = frappe.get_doc({"doctype": "Installment Plan", "connection": r.connection, "consumer_name": r.consumer_name, "society": doc.society,
	                    "plan_status": "Active", "owed_now": flt(r.arrears) + flt(r.surcharge) + flt(r.fees), "created_on": as_of,
	                    "amount_to_split": flt(r.plan_remaining), "down_payment": 0, "installments": n, "first_month": first,
	                    "installment_amount": float(rows[0][2]) if rows else 0, "workflow_state": "Approved", "approved_by": approver,
	                    "approved_on": now_datetime(), "requested_by": doc.requested_by,
	                    "remarks": _("Plan running at go-live, brought over by {0}").format(doc.name),
	                    "schedule": [{"number": no, "billing_month": m, "amount": float(a), "status": "Scheduled"} for no, m, a in rows]})
	p.plan_title = f"{r.consumer_name or r.connection} · {n} installments of Rs {flt(p.installment_amount):,.0f}"
	p.set_new_name()
	p.docstatus = 1
	p.db_insert()
	for i, row in enumerate(p.schedule, start=1):
		row.parent, row.parenttype, row.parentfield, row.idx, row.docstatus = p.name, "Installment Plan", "schedule", i, 1
		row.db_insert()
	return p.name


# ---------------------------------------------------------------- buttons
@frappe.whitelist(methods=["POST"])
def load_file(name: str) -> dict:
	"""Load Opening Balance Import's attached file into its rows (replacing them) and check them."""
	doc = frappe.get_doc("Opening Balance Import", name)
	doc.check_permission("write")
	if doc.docstatus != 0 or doc.workflow_state not in (None, "", "Draft", "Rejected"):
		frappe.throw(_("Only a draft or rejected import can be reloaded."))
	if not doc.import_file:
		frappe.throw(_("Attach the file first."))
	doc.set("rows", rows_from_table(read_file(doc.import_file)))
	doc.save()
	return {"rows": doc.row_count, "errors": doc.error_count}


@frappe.whitelist()
def error_report(name: str):
	"""The rows with problems as a CSV, to fix in the source file."""
	doc = frappe.get_doc("Opening Balance Import", name)
	doc.check_permission("read")
	data = [list(COLUMNS) + ["problem"]] + [[r.get(c) for c in COLUMNS] + [r.error] for r in doc.rows if r.row_status == "Error"]
	from frappe.utils.csvutils import build_csv_response  # noqa: PLC0415

	build_csv_response(data, f"{doc.name} problems")


@frappe.whitelist()
def template():
	"""An empty file with the columns, for the society's data team."""
	from frappe.utils.csvutils import build_csv_response  # noqa: PLC0415

	build_csv_response([list(COLUMNS), ["1000000016", 22260, 1104, 0, 0, 0, 5000, 0, 0]], "opening balances template")
