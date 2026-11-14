# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Corrections to issued bills and accounts (increment 13; FS-BIL-10, 11, FS-WF-01, 03, 06, 07, 10, 11, 12, FS-RUN-06).

An issued bill is never edited (FS-BIL-10). It is corrected in one of two ways:
- a **revised bill** (Bill Revision), while nothing has been paid against it and its due date has not passed: the bill is
  recalculated from the corrected reading or category, replaces the earlier version under the same reference number, and
  both stay visible (the old one marked Superseded). Payment channels always show the latest version (FS-BIL-11);
- after that, a credit or debit note (Account Adjustment), or a **Bulk Adjustment** for a whole group.
Also here: a **due date extension** within limits, a **bill dispute** that holds the surcharge and the disputed amount from
recovery, and the charge and cash-only flag that follow a **dishonoured cheque**."""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, get_first_day, get_last_day, getdate, now_datetime, nowdate

from utility_billing.utility_billing import account, bill_print, billing, notify, reading_engine
from utility_billing.utility_billing import account_engine as engine

HANDLERS = ["Billing Manager", "General Manager", "Billing Officer", "Society Admin"]


def is_gm() -> bool:
	return "General Manager" in frappe.get_roles() or frappe.session.user == "Administrator"


def due_date_of(bill):
	return getdate(bill.get("extended_due_date") or bill.due_date)


def latest_posted_bill(connection):
	return frappe.db.get_value("Utility Bill", {"connection": connection, "docstatus": 1, "status": "Posted"}, "name",
	                           order_by="billing_month desc, creation desc")


# ---------------------------------------------------------------- revised bill (BIL-11, WF-01, 06, 07)
def check_revisable(bill):
	"""Raise, saying what to do instead, unless this bill can be replaced by a revised bill."""
	use_note = _("Correct it with a credit or debit note instead (Account Adjustment).")
	if bill.docstatus != 1 or bill.status != "Posted":
		frappe.throw(_("Only a posted bill that has not been replaced can be revised."))
	if bill.name != latest_posted_bill(bill.connection):
		frappe.throw(_("A newer bill exists for this account, so {0} can't be replaced. {1}").format(bill.name, use_note))
	if getdate(nowdate()) > due_date_of(bill):
		frappe.throw(_("The due date of {0} has passed, so it can't be replaced. {1}").format(bill.name, use_note))
	try:
		account.check_bill_reversible(bill)
	except frappe.ValidationError:
		frappe.throw(_("A payment or another entry was posted to the account after {0}, so it can't be replaced. {1}").format(bill.name, use_note))


def calculate_revision(bill, kind, new_present=None, new_category=None):
	"""(bill fields, units, category) of the bill as it would be with the correction. Nothing is saved."""
	reading = frappe.get_doc("Meter Reading", bill.meter_reading)
	units, category = flt(bill.units), bill.consumer_category
	if kind == "Corrected reading":
		if new_present in (None, ""):
			frappe.throw(_("Enter the correct present reading."))
		if flt(reading.old_meter_units):
			frappe.throw(_("This reading spans a meter change. Correct it with a credit or debit note."))
		u, _rolled, lower = reading_engine.raw_units(reading.previous_reading, new_present, reading.digits, reading.multiplier)
		if lower:
			frappe.throw(_("The present reading can't be lower than the previous reading ({0}).").format(reading.previous_reading))
		units = float(u)
	elif kind == "Corrected category":
		if not new_category or new_category == bill.consumer_category:
			frappe.throw(_("Choose the correct category: it must differ from the one billed."))
		cat = frappe.db.get_value("Consumer Category", new_category, ["utility", "enabled"], as_dict=True)
		if not cat or not cat.enabled or cat.utility != bill.utility:
			frappe.throw(_("{0} isn't an enabled {1} category.").format(new_category, bill.utility))
		category = new_category
	else:
		frappe.throw(_("Choose what was wrong."))
	run = frappe.get_doc("Bill Run", bill.bill_run)
	try:
		data = billing.build_bill(run, bill.connection, override={"units": units, "category": category, "arrears": flt(bill.arrears)})
	except billing.BillProblem as e:
		frappe.throw(str(e))
	return data, units, category


def fill_preview(rev):
	"""Show on the form what the correction does to the bill."""
	bill = frappe.get_doc("Utility Bill", rev.original_bill)
	rev.connection, rev.consumer_name, rev.society, rev.billing_month = bill.connection, bill.consumer_name, bill.society, bill.billing_month
	reading = frappe.get_doc("Meter Reading", bill.meter_reading)
	rev.meter_reading, rev.previous_reading, rev.old_present_reading = reading.name, reading.previous_reading, reading.present_reading
	rev.old_category = bill.consumer_category
	data, units, _category = calculate_revision(bill, rev.kind, rev.new_present_reading, rev.new_category)
	rev.old_units, rev.new_units = flt(bill.units), flt(units)
	rev.old_current, rev.new_current = flt(bill.current_amount), flt(data["current_amount"])
	rev.difference = round(rev.new_current - rev.old_current, 2)
	rev.new_payable_within_due = flt(data["payable_within_due"])


def revise_bill(rev):
	"""On approval: correct the input, replace the bill in the customer account, post the revised bill (FS-BIL-11)."""
	old = frappe.get_doc("Utility Bill", rev.original_bill)
	check_revisable(old)
	_data, units, category = calculate_revision(old, rev.kind, rev.new_present_reading, rev.new_category)
	reading = frappe.get_doc("Meter Reading", old.meter_reading)
	if rev.kind == "Corrected reading":
		frappe.db.set_value("Meter Reading", reading.name, {
			"present_reading": flt(rev.new_present_reading), "units": units, "raw_units": units, "corrected_from": reading.present_reading,
			"correction_reason": rev.remarks, "reviewed_by": frappe.session.user, "reviewed_on": now_datetime()})
	else:
		frappe.db.set_value("Connection", old.connection, "consumer_category", category)
		frappe.clear_document_cache("Connection", old.connection)
	# the earlier version leaves the account; the revised one takes its place under the same reference number
	account.reverse_bill(old)
	old.flags.from_bill_run = old.flags.ignore_permissions = old.flags.superseding = old.flags.ignore_links = True  # the revision itself links to it
	old.cancel()
	run = frappe.get_doc("Bill Run", old.bill_run)
	data = billing.build_bill(run, old.connection, override={"units": units, "category": category, "arrears": flt(old.arrears),
	                                                         "revises": old.name, "revision_no": cint(old.revision_no or 1) + 1})
	new = frappe.get_doc(data)
	new.flags.from_bill_run = new.flags.ignore_permissions = new.flags.ignore_links = True  # it points to the version it replaces
	new.insert()
	new.status, new.outstanding_amount = "Posted", flt(new.payable_within_due)
	new.print_snapshot = json.dumps(bill_print.build_snapshot(new))
	new.submit()
	account.post_bill(new)
	frappe.db.set_value("Utility Bill", old.name, "superseded_by", new.name, update_modified=False)
	rev.db_set("revised_bill", new.name)
	notify.bill_revised(new)
	return new


# ---------------------------------------------------------------- due date extension (WF-03)
def check_extension(ext):
	name = latest_posted_bill(ext.connection)
	if not name:
		frappe.throw(_("Account {0} has no posted bill to extend.").format(ext.connection))
	bill = frappe.get_doc("Utility Bill", name)
	if flt(account.balance(ext.connection)) <= 0:
		frappe.throw(_("Account {0} owes nothing, so there is nothing to extend.").format(ext.connection))
	old, new = due_date_of(bill), getdate(ext.new_due_date)
	days = (new - old).days
	if days <= 0:
		frappe.throw(_("The new due date must be after the current one, {0}.").format(formatdate(old)))
	soc = frappe.get_cached_doc("Society", bill.society)
	if not is_gm():
		if days > cint(soc.max_extension_days):
			frappe.throw(_("A Billing Manager can extend a due date by up to {0} days. Ask a General Manager for {1}.").format(soc.max_extension_days, days))
		used = frappe.db.count("Due Date Extension", {"connection": ext.connection, "name": ["!=", ext.name or ""],
		                                              "new_due_date": ["between", [f"{new.year}-01-01", f"{new.year}-12-31"]]})
		if used >= cint(soc.max_extensions_per_year):
			frappe.throw(_("This account already had {0} extensions this year (the limit). Ask a General Manager.").format(used))
	return bill, old, days


def apply_extension(ext, bill, old, days):
	"""Move the bill's due date and settle the surcharge: one already charged is reversed (to be charged again after the new
	date if the account is still unpaid), one not yet due simply follows the new date."""
	ext.bill, ext.old_due_date, ext.days_extended = bill.name, old, days
	frappe.db.set_value("Utility Bill", bill.name, "extended_due_date", ext.new_due_date, update_modified=False)
	posted = bill.surcharge_entry and bill.surcharge_status == "Posted" and not frappe.db.get_value("Customer Ledger Entry", bill.surcharge_entry, "reversed_by")
	if posted:
		new_deadline = add_days(ext.new_due_date, cint(frappe.db.get_value("Society", bill.society, "grace_days")))
		if getdate(new_deadline) >= getdate(nowdate()):
			account.lock(bill.connection)
			account.reverse_entry(bill.surcharge_entry, "Utility Bill", bill.name, remarks=_("Due date extended to {0}").format(formatdate(ext.new_due_date)))
			frappe.db.set_value("Utility Bill", bill.name, "surcharge_status", "Not due yet", update_modified=False)
			return _("The surcharge already charged was reversed; it is charged again after the new due date if the account is still unpaid.")
		return _("The new due date is already past, so the surcharge already charged stands.")
	return _("No surcharge had been charged; it now falls due after the new due date.")


# ---------------------------------------------------------------- bill dispute (WF-10)
def disputed_by_connection(society) -> dict:
	rows = frappe.db.sql("""select connection, sum(disputed_amount) amount from `tabBill Dispute` where society = %s and status = 'Open' group by connection""",
	                     society, as_dict=True)
	return {r.connection: flt(r.amount) for r in rows}


def hold_surcharge(dispute):
	"""A surcharge already charged on the disputed bill is reversed, and not charged again while the dispute is open."""
	bill = frappe.get_doc("Utility Bill", dispute.bill)
	if bill.surcharge_entry and bill.surcharge_status == "Posted" and not frappe.db.get_value("Customer Ledger Entry", bill.surcharge_entry, "reversed_by"):
		account.lock(bill.connection)
		account.reverse_entry(bill.surcharge_entry, "Utility Bill", bill.name, remarks=_("Held while bill {0} is in dispute").format(bill.name))
		frappe.db.set_value("Utility Bill", bill.name, "surcharge_status", "Not due yet", update_modified=False)


def resolve_dispute(d, outcome, remarks, revision=None):
	if not set(frappe.get_roles()) & {"Billing Manager", "General Manager"} and frappe.session.user != "Administrator":
		frappe.throw(_("Only a Billing Manager or General Manager decides a dispute."), frappe.PermissionError)
	if d.status != "Open":
		frappe.throw(_("This dispute was already decided."))
	if outcome not in ("Released", "Bill revised", "Withdrawn"):
		frappe.throw(_("Choose the outcome."))
	if len((remarks or "").strip()) < 5:
		frappe.throw(_("Write the decision and its reason (at least 5 characters)."))
	if outcome == "Bill revised":
		if not revision or frappe.db.get_value("Bill Revision", revision, "docstatus") != 1 or frappe.db.get_value("Bill Revision", revision, "connection") != d.connection:
			frappe.throw(_("Choose the approved bill revision that settled this dispute."))
		d.revision = revision
	d.status, d.resolution = outcome, remarks.strip()
	d.resolved_by, d.resolved_on = frappe.session.user, now_datetime()


# ---------------------------------------------------------------- bulk adjustment (WF-12)
def bulk_connections(doc) -> list[dict]:
	filters = {"society": doc.society, "status": "Active"}
	if doc.audience == "Category":
		filters["consumer_category"] = doc.consumer_category
	elif doc.audience == "Feeder":
		filters["feeder"] = doc.feeder
	elif doc.audience == "Transformer":
		filters["transformer"] = doc.transformer
	rows = frappe.get_all("Connection", filters=filters, fields=["name", "consumer_name", "property"], order_by="name")
	if doc.audience == "Block":
		blocks = {p.name: p.block for p in frappe.get_all("Property", filters={"society": doc.society}, fields=["name", "block"])}
		rows = [r for r in rows if blocks.get(r.property) == doc.block]
	return rows


def _deltas(doc, connection):
	bal = account.balances(connection)
	bucket = account.BUCKET_OF[doc.bucket]
	if doc.note_type == "Credit Note":
		return engine.credit_note(bal, bucket, doc.amount)  # ValueError when the balance can't take it
	return engine.charge(bal, bucket, doc.amount, account.posting_order(doc.society))


def prepare_bulk(doc):
	"""List the accounts and show which will take the adjustment; a credit note that exceeds an account's balance is skipped."""
	doc.rows = []
	for c in bulk_connections(doc):
		try:
			_deltas(doc, c.name)
			result = "Will post"
		except ValueError:
			result = "Skipped: nothing to credit"
		doc.append("rows", {"connection": c.name, "consumer_name": c.consumer_name, "amount": doc.amount, "result": result})
	n = sum(1 for r in doc.rows if r.result == "Will post")
	doc.accounts, doc.total_amount = n, round(n * flt(doc.amount), 2)


def post_bulk(doc):
	"""On approval: one ledger entry per account, all under this document, in one transaction."""
	done = 0
	for row in doc.rows:
		if row.result != "Will post":
			continue
		account.lock(row.connection)
		try:
			deltas = _deltas(doc, row.connection)
		except ValueError:
			frappe.db.set_value("Bulk Adjustment Row", row.name, "result", "Skipped: nothing to credit", update_modified=False)
			continue
		entry = account.post(row.connection, doc.note_type, deltas, "Bulk Adjustment", doc.name, nowdate(), doc.remarks)
		frappe.db.set_value("Bulk Adjustment Row", row.name, {"result": "Posted", "ledger_entry": entry.name}, update_modified=False)
		done += 1
	doc.db_set({"posting_date": nowdate(), "accounts": done})
	return done


def reverse_bulk(doc):
	for row in doc.rows:
		if row.ledger_entry and not frappe.db.get_value("Customer Ledger Entry", row.ledger_entry, "reversed_by"):
			account.lock(row.connection)
			account.reverse_entry(row.ledger_entry, "Bulk Adjustment", doc.name, remarks=_("Bulk adjustment {0} cancelled").format(doc.name))


# ---------------------------------------------------------------- dishonoured cheque (WF-11)
def after_dishonour(receipt):
	"""A cheque or pay order reversed as dishonoured: charge the society's fee and, if it chose to, allow cash only (FS-WF-11)."""
	soc = frappe.get_cached_doc("Society", receipt.society)
	out = {"fee": None, "cash_only": False}
	if soc.cheque_dishonour_fee:
		fee = frappe.get_doc({"doctype": "Fee Charge", "connection": receipt.connection, "fee_type": soc.cheque_dishonour_fee,
		                      "source_type": "Payment Receipt", "source_name": receipt.name,
		                      "remarks": _("Dishonoured {0} {1}").format(_(receipt.mode).lower(), receipt.instrument_no or receipt.name)})
		fee.flags.ignore_permissions = fee.flags.ignore_links = True  # the receipt it points to has just been cancelled
		fee.insert()
		fee.submit()
		out["fee"] = fee.name
	if cint(soc.cash_only_after_dishonour):
		consumer = frappe.db.get_value("Connection", receipt.connection, "consumer")
		frappe.db.set_value("Consumer", consumer, "cash_only", 1)
		out["cash_only"] = True
	return out
