# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Amounts held by court order (increment 35; user's decision 2026-11-20 on LESCO's "Deferred": disputed and set aside by court order,
included in the total receivable).

A held amount is owed, so it stays in the receivable, but it must not be billed, attract the late payment surcharge, take payments
or go to recovery. So it is kept out of the customer ledger (which drives all of those) and carried on its own Held Receivables
account in the journals:
  at go-live   an Opening Balance Import row's held amount -> a Held Amount; journal: Held Receivables / Opening Balances
  release      (Billing Manager, General Manager) -> a "Hold released" ledger entry adding it to the arrears, billed with the next
               bill; its journal (with the day's transactions): Receivables / Held Receivables
  write-off    (General Manager) -> journal: Write-off Expense / Held Receivables; the account's ledger is untouched
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import account, gl, periods
from utility_billing.utility_billing import account_engine as engine

RELEASERS = {"Billing Manager", "General Manager", "System Manager"}
WRITERS_OFF = {"General Manager", "System Manager"}


def held_of(connection) -> float:
	"""What the account has held by court order now."""
	return round(sum(flt(a) for a in frappe.get_all("Held Amount", filters={"connection": connection, "status": "Held"}, pluck="amount")), 2)


def _journal(society, posting_date, narration, lines, entry_count=1):
	"""A balanced journal from [(account, debit, credit)]."""
	rows = [{"account": a, "account_name": frappe.db.get_value("Ledger Account", a, "account_name"), "debit": flt(dr, 2), "credit": flt(cr, 2),
	         "narration": narration[:140]} for a, dr, cr in lines]
	total = round(sum(r["debit"] for r in rows), 2)
	j = frappe.get_doc({"doctype": "GL Journal", "journal_type": "Held Amounts", "society": society, "posting_date": posting_date, "narration": narration,
	                    "entry_count": entry_count, "lines": rows, "total_debit": total, "total_credit": round(sum(r["credit"] for r in rows), 2),
	                    "receivables_change": 0, "journal_title": f"{society} Held Amounts {frappe.utils.formatdate(posting_date)}"})
	j.flags.from_gl = True
	j.insert(ignore_permissions=True)
	return j


def held_account(society):
	return gl.need(gl.settings(society).held, _("amounts held by court order (Society {0} → Held Receivables Account)").format(society))


# ---------------------------------------------------------------- at go-live
def from_opening(doc, as_of):
	"""The held amounts of a posted Opening Balance Import, with one journal against the opening balances account."""
	rows = [r for r in doc.rows if flt(r.get("held_amount")) > 0]
	if not rows:
		return None
	batch = frappe.db.get_value("Migration Batch", {"opening_import": doc.name}, "name")
	total = round(sum(flt(r.held_amount) for r in rows), 2)
	s = gl.settings(doc.society)
	j = _journal(doc.society, as_of, _("Amounts held by court order at go-live ({0})").format(doc.name),
	             [(held_account(doc.society), total, 0), (gl.need(s.opening, _("opening balances (Society {0})").format(doc.society)), 0, total)],
	             entry_count=len(rows))
	for r in rows:
		frappe.get_doc({"doctype": "Held Amount", "connection": r.connection, "consumer_name": r.consumer_name, "society": doc.society,
		                "status": "Held", "amount": flt(r.held_amount), "held_on": as_of, "court_order": r.get("court_order") or None,
		                "opening_import": doc.name, "migration_batch": batch, "gl_journal": j.name}).insert(ignore_permissions=True)
	return j.name


# ---------------------------------------------------------------- decisions
def _check(doc, remarks, roles, what):
	if doc.status != "Held":
		frappe.throw(_("This amount was already {0}.").format(_(doc.status).lower()))
	if not set(frappe.get_roles()) & roles:
		raise frappe.PermissionError(_("Only {0} can {1} a held amount.").format(_(" or ").join(sorted(r for r in roles if r != "System Manager")), what))
	if len((remarks or "").strip()) < 5:
		frappe.throw(_("Say what was decided and why (at least 5 characters), for example the court's order."))


def _decided(doc, remarks, status, **fields):
	doc.flags.deciding = True
	doc.update({"status": status, "decision_remarks": remarks.strip(), "decided_by": frappe.session.user, "decided_on": now_datetime(), **fields})
	try:
		doc.save(ignore_permissions=True)
	finally:
		doc.flags.deciding = False  # only this save may change a decided amount
	return doc.status


def release(doc, remarks):
	"""The court decided the consumer pays: the amount moves into the arrears (using any credit on the account) and is billed with
	the next bill."""
	_check(doc, remarks, RELEASERS, _("release"))
	held_account(doc.society)  # its journal needs the account: refuse now rather than when the day's journal is made
	today = getdate(nowdate())
	account.lock(doc.connection)
	deltas = engine.charge(account.balances(doc.connection), "arrears", doc.amount, account.posting_order(doc.society))
	e = account.post(doc.connection, "Hold released", deltas, "Held Amount", doc.name, today,
	                 _("Held by court order, released: {0}").format(remarks.strip())[:140])
	return _decided(doc, remarks, "Released", ledger_entry=e.name)


def write_off(doc, remarks):
	"""The court decided the amount isn't owed (or it can't be collected): written off; the customer ledger is untouched."""
	_check(doc, remarks, WRITERS_OFF, _("write off"))
	today = getdate(nowdate())
	periods.assert_open(doc.society, today, "Write-off")
	expense = gl.need(gl.settings(doc.society).write_off, _("written-off receivables (Society {0} → Write-off Expense Account)").format(doc.society))
	j = _journal(doc.society, today, _("Held amount {0} of {1} written off").format(doc.name, doc.connection),
	             [(expense, doc.amount, 0), (held_account(doc.society), 0, doc.amount)])
	return _decided(doc, remarks, "Written off", decision_journal=j.name)
