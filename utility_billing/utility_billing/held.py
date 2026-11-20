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
  after go-live (increment 36; user's decision 2026-11-20: "on hold will be a separate transaction just like the installments or bill
               correction or due date extension. it would also generate a revised bill") -> a Court Hold, approved like a bill
               revision: a "Held by court order" ledger entry takes the amount out of what is owed (journal: Held Receivables /
               Receivables), a Held Amount records it, and the latest bill is reissued as a revised bill without it.
A held amount "is part of total receivables but it is not part of payable amount unless its withdrawn" (user, 2026-11-20): bills print
it for information and the receivables reports add it to what is owed.
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


def held_by_connection(society) -> dict:
	"""{account: amount held now} for a society (a bill run reads it once)."""
	out = {}
	for r in frappe.get_all("Held Amount", filters={"society": society, "status": "Held"}, fields=["connection", "amount"]):
		out[r.connection] = round(out.get(r.connection, 0) + flt(r.amount), 2)
	return out


def held_as_of(society, as_of) -> dict:
	"""{account: amount held at the end of a day}: held by then and not yet released or written off by then (the reports)."""
	as_of = getdate(as_of)
	out = {}
	for r in frappe.get_all("Held Amount", filters={"society": society, "held_on": ["<=", as_of]},
	                        fields=["connection", "amount", "status", "decided_on"]):
		if r.status != "Held" and r.decided_on and getdate(r.decided_on) <= as_of:
			continue
		out[r.connection] = round(out.get(r.connection, 0) + flt(r.amount), 2)
	return out


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


# ---------------------------------------------------------------- after go-live: a Court Hold (increment 36)
def due_date_for(society, issue_date):
	from utility_billing.utility_billing import corrections  # noqa: PLC0415

	return corrections.due_date_for(society, issue_date)


def _standing_surcharge(bill):
	"""The latest bill's surcharge entry when it still stands, else None."""
	if bill and bill.surcharge_entry and bill.surcharge_status == "Posted" and not frappe.db.get_value(
			"Customer Ledger Entry", bill.surcharge_entry, "reversed_by"):
		return bill.surcharge_entry
	return None


def hold_plan(connection, amount) -> frappe._dict:
	"""What a court hold of `amount` does to the account, without saving anything. The latest bill's surcharge, if charged, is reversed
	(the revised bill has a new due date and its own surcharge); then the amount leaves what is owed, oldest first: arrears, surcharge,
	fees, then the current bill."""
	from utility_billing.utility_billing import corrections, holders, plans  # noqa: PLC0415

	if plans.active_plan(connection):
		frappe.throw(_("Account {0} has an active installment plan. Complete or cancel the plan before holding an amount by court order.").format(connection))
	name = corrections.latest_posted_bill(connection)
	bill = frappe.get_doc("Utility Bill", name) if name else None
	before = account.balances(connection)
	bal, surcharge_entry, surcharge = before, _standing_surcharge(bill), 0
	if surcharge_entry:
		bal = engine.apply(before, engine.reverse(before, account.deltas_of(surcharge_entry)))
		surcharge = flt(frappe.db.get_value("Customer Ledger Entry", surcharge_entry, "amount"))
	try:
		deltas = engine.hold(bal, amount)
	except ValueError:
		frappe.throw(_("Account {0} owes Rs {1}{2}, so no more than that can be held.").format(
			connection, f"{max(flt(engine.total(bal)), 0):,.2f}", _(" once the latest bill's surcharge is reversed") if surcharge else ""))
	after = engine.apply(bal, deltas)
	return frappe._dict(bill=bill, owed_now=flt(engine.total(before)), surcharge_entry=surcharge_entry, surcharge_reversed=surcharge,
	                    deltas=deltas, from_current=-flt(deltas["current"]),
	                    from_arrears=-flt(deltas["arrears"] + deltas["surcharge"] + deltas["fees"]),
	                    payable=max(round(flt(engine.total(after)) - holders.former_outstanding(connection), 2), 0))


def fill_hold_preview(doc):
	"""Show on the Court Hold form what it does to the account and the bill."""
	conn = frappe.db.get_value("Connection", doc.connection, ["consumer_name", "society"], as_dict=True)
	doc.consumer_name, doc.society = conn.consumer_name, conn.society
	p = hold_plan(doc.connection, doc.amount)
	doc.owed_now, doc.surcharge_reversed, doc.held_from_arrears, doc.held_from_current = p.owed_now, p.surcharge_reversed, p.from_arrears, p.from_current
	doc.payable_after = p.payable
	doc.bill = p.bill.name if p.bill else None
	doc.new_due_date = due_date_for(doc.society, nowdate()) if p.bill else None


def place_hold(doc):
	"""On approval: reverse the latest bill's surcharge, take the amount out of what is owed (a "Held by court order" ledger entry; its
	journal, with the day's transactions: Held Receivables / Receivables), record it as a Held Amount and reissue the latest bill."""
	held_account(doc.society)  # refuse now rather than when the day's journal is made
	account.lock(doc.connection)
	p = hold_plan(doc.connection, doc.amount)
	today = getdate(nowdate())
	if p.surcharge_entry:
		account.reverse_entry(p.surcharge_entry, "Court Hold", doc.name, today, _("Bill revised for court hold {0}").format(doc.name))
	entry = account.post(doc.connection, "Held by court order", p.deltas, "Court Hold", doc.name, today,
	                     _("Held by court order {0}").format(doc.court_order)[:140])
	ha = frappe.get_doc({"doctype": "Held Amount", "connection": doc.connection, "consumer_name": doc.consumer_name, "society": doc.society,
	                     "status": "Held", "amount": flt(doc.amount), "held_on": today, "court_order": doc.court_order,
	                     "court_hold": doc.name, "hold_entry": entry.name}).insert(ignore_permissions=True)
	revised = None
	if p.bill:  # the latest bill reissued without the held amount (corrections.reissue_bill); no plan is active (hold_plan refuses one)
		from utility_billing.utility_billing import corrections  # noqa: PLC0415

		revised = corrections.reissue_bill(p.bill, today, due_date_for(doc.society, today), _("Court hold {0} ({1})").format(doc.name, doc.court_order),
		                                   court_hold=doc.name, held_deducted=doc.amount)
	doc.db_set({"held_amount": ha.name, "ledger_entry": entry.name, "revised_bill": revised.name if revised else None,
	            "surcharge_reversed": p.surcharge_reversed})
	return revised
