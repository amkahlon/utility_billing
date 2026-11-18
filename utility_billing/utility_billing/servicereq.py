# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Service requests and the connection lifecycle (FS-WF-00, 09, 20 to 33).

A Service Request Type (per society) says what a request does (its kind), its steps in order, the documents it needs, the fee
and the deposit. A Service Request copies the steps; each step is done by a user with its role:
- Office: just done; Document check: every required document attached; Approval: by someone other than whoever raised the request;
- Payment: the account owes nothing and the demanded deposit is approved; Field: a visit with reading, photo and location.
When the last step is done the request's change is made (a new connection made active, a meter changed, a connection disconnected,
a category changed, a no-dues certificate issued …). Money moves only through the existing documents (Fee Charge, Security Deposit,
Account Adjustment), so their approvals and journals apply; drafts that need approval are left for the approver.
Field steps are entered by staff for now; the readers' app (parked) will send them as work orders.
"""

import secrets

import frappe
from frappe import _
from frappe.model.naming import getseries
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import account, holders
from utility_billing.utility_billing.doctype.security_deposit.security_deposit import held
from utility_billing.utility_billing import rights

NEW, TEMP, METER, DISC, RECON, CLOSE, CAT, LOAD, OWNER, T_IN, T_OUT, NODUES, CONTACT, TEST, DEPOSIT, DETECT = (
	"New connection", "Temporary connection", "Meter change", "Disconnection", "Reconnection", "Permanent disconnection",
	"Change of category", "Load change", "Change of name or ownership", "Tenant moving in", "Tenant leaving", "No-dues certificate",
	"Contact update", "Meter test", "Deposit change", "Detection bill")
MERGE, SPLIT = "Merge accounts", "Split account"  # FS-WF-35 (increment 23)
CREATES_CONNECTION = (NEW, TEMP)
INSTALLS_METER = (NEW, TEMP, SPLIT)
BO, BM, GM, AO, CASHIER, RO = "Billing Officer", "Billing Manager", "General Manager", "Accounts Officer", "Cashier", "Recovery Officer"

# code, name, kind, steps (name, kind, role, days), documents (name, required)
DEFAULT_TYPES = (
	("NEW", "New connection", NEW,
	 (("Application received", "Office", BO, 1), ("Documents checked", "Document check", BO, 2), ("Site survey", "Field", BO, 3),
	  ("Approval", "Approval", BM, 2), ("Demand notice paid", "Payment", CASHIER, 7), ("Meter installed and first reading", "Field", BO, 3)),
	 (("CNIC copy", 1), ("Ownership or tenancy proof", 1), ("Site plan", 0))),
	("TEMP", "Temporary or construction connection", TEMP,
	 (("Documents checked", "Document check", BO, 2), ("Approval", "Approval", BM, 2), ("Demand notice paid", "Payment", CASHIER, 7),
	  ("Meter installed and first reading", "Field", BO, 3)),
	 (("CNIC copy", 1), ("Construction approval", 1))),
	("METER", "Meter change", METER, (("Approval", "Approval", BM, 2), ("Meter changed", "Field", BO, 3)), ()),
	("DISC", "Disconnection", DISC, (("Approval", "Approval", BM, 1), ("Supply disconnected", "Field", RO, 2)), ()),
	("RECON", "Reconnection", RECON, (("Dues cleared and fee paid", "Payment", CASHIER, 3), ("Supply restored", "Field", BO, 1)), ()),
	("CLOSE", "Permanent disconnection", CLOSE,
	 (("Approval", "Approval", BM, 2), ("Final reading", "Field", BO, 3), ("Deposit settled", "Office", AO, 5)), (("Written request", 1),)),
	("CAT", "Change of category", CAT, (("Documents checked", "Document check", BO, 2), ("Approval", "Approval", BM, 3)), (("Proof of new use", 1),)),
	("LOAD", "Load extension or reduction", LOAD, (("Approval", "Approval", BM, 3), ("Demand paid", "Payment", CASHIER, 7)), (("Load details", 1),)),
	("OWNER", "Change of name or ownership", OWNER, (("Documents checked", "Document check", BO, 2), ("Approval", "Approval", BM, 3)),
	 (("Transfer deed or succession papers", 1), ("New owner's CNIC copy", 1))),
	("TENANT-IN", "Tenant registration", T_IN, (("Documents checked", "Document check", BO, 2), ("Registered", "Office", BO, 1)),
	 (("Tenancy agreement", 1), ("Tenant's CNIC copy", 1))),
	("TENANT-OUT", "Tenant leaving", T_OUT, (("Final reading", "Field", BO, 2), ("Owner billed again", "Office", BO, 1)), ()),
	("NODUES", "No-dues certificate", NODUES, (("Certificate issued", "Office", BO, 1),), ()),
	("CONTACT", "Contact and address update", CONTACT, (("Updated", "Office", BO, 1),), ()),
	("TEST", "Meter testing on complaint", TEST, (("Meter tested", "Field", BO, 5), ("Approval", "Approval", BM, 2)), ()),
	("DEPOSIT", "Security deposit change", DEPOSIT, (("Worked out", "Office", AO, 2),), ()),
	("DETECT", "Detection bill", DETECT, (("Inspection", "Field", BO, 2), ("Approval", "Approval", GM, 3)), (("Inspection report", 1),)),
	("MERGE", "Merge two accounts", MERGE, (("Approval", "Approval", BM, 2), ("Final reading and meter removed", "Field", BO, 3)),
	 (("Written request", 1),)),
	("SPLIT", "Split an account", SPLIT,
	 (("Documents checked", "Document check", BO, 2), ("Approval", "Approval", BM, 2), ("Demand notice paid", "Payment", CASHIER, 7),
	  ("Meter installed and first reading", "Field", BO, 3)),
	 (("Written request", 1), ("Site plan", 0))),
)


def ensure_types(society):
	"""The standard request types for a society, made once; the society can change them afterwards."""
	made = 0
	for code, name, kind, steps, docs in DEFAULT_TYPES:
		if frappe.db.exists("Service Request Type", f"{society}-{code}"):
			continue
		frappe.get_doc({"doctype": "Service Request Type", "society": society, "type_code": code, "type_name": name, "kind": kind,
		                "days_allowed": sum(s[3] for s in steps),
		                "steps": [{"step_name": n, "step_kind": k, "role": r, "days": d} for n, k, r, d in steps],
		                "documents": [{"document_name": n, "mandatory": m} for n, m in docs]}).insert(ignore_permissions=True)
		made += 1
	return made


@frappe.whitelist(methods=["POST"])
def set_up_types(society: str):
	if not rights.can("servicereq.setup"):
		raise frappe.PermissionError(_("Only a Society Admin or Billing Manager sets up request types."))
	return ensure_types(society)


# ---------------------------------------------------------------- creating a request
def prepare(sr):
	"""Copy the type's steps and documents onto a new request and set its due dates."""
	t = frappe.get_cached_doc("Service Request Type", sr.request_type)
	day = getdate(sr.requested_on or nowdate())
	steps = []
	for s in t.steps:
		day = add_days(day, cint(s.days))
		steps.append({"step_name": s.step_name, "step_kind": s.step_kind, "role": s.role, "due_date": day, "status": "Pending"})
	sr.set("steps", steps)
	have = {d.document_name for d in sr.documents}
	for d in t.documents:
		if d.document_name not in have:
			sr.append("documents", {"document_name": d.document_name, "mandatory": d.mandatory})
	sr.due_by = add_days(sr.requested_on or nowdate(), cint(t.days_allowed)) if cint(t.days_allowed) else day
	sr.current_step = 1 if steps else 0


def demand(sr):
	"""On a new request: the applied connection (new connections), the fee and the deposit asked for (the demand notice)."""
	t = frappe.get_cached_doc("Service Request Type", sr.request_type)
	values = {}
	if sr.kind in CREATES_CONNECTION and not sr.connection:
		conn = frappe.get_doc({"doctype": "Connection", "society": sr.society, "property": sr.property, "consumer": sr.consumer,
		                       "utility": sr.utility, "consumer_category": sr.new_category, "status": "Applied",
		                       "sanctioned_load_kw": flt(sr.new_load_kw), "phase": sr.phase or None, "transformer": sr.transformer,
		                       "expires_on": sr.expires_on if sr.kind == TEMP else None})
		conn.insert(ignore_permissions=True)
		values["connection"] = sr.connection = conn.name
	if sr.kind == SPLIT and not sr.new_connection:  # the new account, applied; the original keeps its balance (user decision 2026-11-16)
		orig = frappe.get_doc("Connection", sr.connection)
		conn = frappe.get_doc({"doctype": "Connection", "society": sr.society, "property": sr.property or orig.property, "consumer": sr.consumer or orig.consumer,
		                       "utility": orig.utility, "consumer_category": sr.new_category or orig.consumer_category, "status": "Applied",
		                       "sanctioned_load_kw": flt(sr.new_load_kw), "phase": sr.phase or orig.phase, "transformer": sr.transformer or orig.transformer,
		                       "split_from": orig.name})
		conn.insert(ignore_permissions=True)
		values["new_connection"] = sr.new_connection = conn.name
	billed = demand_account(sr)
	fee_type = t.fee_type or (frappe.db.get_value("Society", sr.society, "reconnection_fee_type") if sr.kind == RECON else None)  # FS-CFG-18
	if fee_type and billed:
		fee = frappe.get_doc({"doctype": "Fee Charge", "connection": billed, "fee_type": fee_type, "source_type": "Service Request",
		                      "source_name": sr.name, "remarks": _("{0} ({1})").format(t.type_name, sr.name)})
		fee.flags.ignore_permissions = True
		fee.insert()
		fee.submit()
		values["fee_charge"] = fee.name
	amount = flt(t.deposit_amount) if sr.kind in (NEW, TEMP, LOAD, SPLIT) else (flt(sr.deposit_amount) if sr.kind == DEPOSIT and sr.deposit_change == "Raise" else 0)
	if amount > 0 and billed:
		values["security_deposit"] = new_deposit(sr, "Received", amount)
	if values:
		sr.db_set(values)


def demand_account(sr):
	"""The account the request's fee, deposit and demand belong to: the new account of a split, else the request's account."""
	return sr.new_connection if sr.kind == SPLIT else sr.connection


def new_deposit(sr, kind, amount):
	d = frappe.get_doc({"doctype": "Security Deposit", "connection": demand_account(sr), "deposit_type": kind, "amount": amount,
	                    "mode": None if kind == "Offset Against Arrears" else "Cash", "posting_date": nowdate(),
	                    "remarks": _("For {0}").format(sr.name)})
	d.flags.ignore_permissions = True
	d.insert()  # a draft: it goes through its own approval
	return d.name


# ---------------------------------------------------------------- steps
def _step(sr, idx):
	rows = [s for s in sr.steps if s.idx == cint(idx)]
	if not rows:
		frappe.throw(_("No step {0} on {1}.").format(idx, sr.name))
	return rows[0]


def check_can_do(sr, step, user):
	roles = set(frappe.get_roles(user))
	if step.role not in roles and "System Manager" not in roles:
		raise frappe.PermissionError(_("Step {0} is done by a {1}.").format(step.step_name, step.role))
	if step.step_kind == "Approval" and user == sr.owner:
		raise frappe.PermissionError(_("The request was raised by you; another user approves it."))


def check_step(sr, step, values):
	if step.step_kind == "Document check":
		missing = [d.document_name for d in sr.documents if cint(d.mandatory) and not d.file]
		if missing:
			frappe.throw(_("Attach first: {0}.").format(", ".join(missing)))
	elif step.step_kind == "Payment":
		if sr.kind == RECON:
			check_reconnection(sr)
		elif demand_account(sr) and account.balance(demand_account(sr)) > 0.005:
			frappe.throw(_("Account {0} still owes Rs {1}; the demand is paid when it owes nothing.").format(
				demand_account(sr), f"{account.balance(demand_account(sr)):,.2f}"))
		if sr.security_deposit and frappe.db.get_value("Security Deposit", sr.security_deposit, "docstatus") != 1:
			frappe.throw(_("The security deposit {0} is not received and approved yet.").format(sr.security_deposit))
	elif step.step_kind == "Field":
		if sr.kind == DISC and sr.effective_date and getdate(sr.effective_date) > getdate(nowdate()):
			frappe.throw(_("The notice period runs until {0}; the supply can't be cut before then.").format(frappe.utils.formatdate(sr.effective_date)))
		lat, lng = values.get("latitude"), values.get("longitude")
		if (lat and not lng) or (lng and not lat):
			frappe.throw(_("Enter both latitude and longitude, or neither."))


@frappe.whitelist(methods=["POST"])
def advance(request: str, step: int, remarks: str | None = None, reading: float | None = None, photo: str | None = None,
            latitude: float | None = None, longitude: float | None = None, reject: int = 0):
	"""Do (or, at an approval, reject) the current step of a request; the last step makes the change."""
	sr = frappe.get_doc("Service Request", request)
	if sr.status not in ("Open", "In Progress"):
		frappe.throw(_("{0} is {1}.").format(sr.name, _(sr.status).lower()))
	s = _step(sr, step)
	if s.status != "Pending" or cint(step) != cint(sr.current_step):
		frappe.throw(_("Step {0} isn't the step to do now (step {1} is).").format(step, sr.current_step))
	check_can_do(sr, s, frappe.session.user)
	values = {"latitude": latitude, "longitude": longitude}
	if cint(reject):
		if s.step_kind != "Approval":
			frappe.throw(_("Only an approval step can reject a request."))
		if len((remarks or "").strip()) < 5:
			frappe.throw(_("Give the reason for rejecting."))
		s.update({"status": "Rejected", "done_by": frappe.session.user, "done_on": now_datetime(), "remarks": remarks})
		sr.status = "Rejected"
		undo_demand(sr, remarks)
		sr.flags.from_steps = True
		sr.save(ignore_permissions=True)
		return {"status": sr.status}
	check_step(sr, s, values)
	if s.step_kind == "Document check":
		for d in sr.documents:
			if d.file:
				d.verified = 1
	s.update({"status": "Done", "done_by": frappe.session.user, "done_on": now_datetime(), "remarks": remarks,
	          "reading": flt(reading) if reading not in (None, "") else None, "photo": photo, "latitude": flt(latitude) or None, "longitude": flt(longitude) or None})
	pending = [x for x in sr.steps if x.status == "Pending"]
	sr.current_step = pending[0].idx if pending else 0
	sr.status = "In Progress" if pending else sr.status
	sr.flags.from_steps = True
	frappe.db.savepoint("sr_step")  # the last step and the change it makes stand or fall together
	try:
		sr.save(ignore_permissions=True)
		if not pending:
			complete(sr)
	except Exception:
		frappe.db.rollback(save_point="sr_step")
		raise
	return {"status": sr.status, "current_step": sr.current_step}


def undo_demand(sr, why):
	"""A rejected or cancelled request takes back its fee and its draft deposit; an applied connection is closed."""
	if sr.fee_charge and frappe.db.get_value("Fee Charge", sr.fee_charge, "docstatus") == 1:
		fee = frappe.get_doc("Fee Charge", sr.fee_charge)
		fee.flags.reversal = {"reason_code": "FEE-ERROR", "remarks": _("Request {0} not done: {1}").format(sr.name, why)}
		fee.flags.ignore_permissions = True
		fee.cancel()
	if sr.security_deposit and frappe.db.get_value("Security Deposit", sr.security_deposit, "docstatus") == 0:
		frappe.delete_doc("Security Deposit", sr.security_deposit, ignore_permissions=True, force=True)
		sr.security_deposit = None
	applied = sr.new_connection if sr.kind == SPLIT else (sr.connection if sr.kind in CREATES_CONNECTION else None)
	if applied and frappe.db.get_value("Connection", applied, "status") == "Applied":
		frappe.db.set_value("Connection", applied, "status", "Closed")


@frappe.whitelist(methods=["POST"])
def cancel(request: str, reason: str):
	sr = frappe.get_doc("Service Request", request)
	sr.check_permission("write")
	if sr.status not in ("Open", "In Progress"):
		frappe.throw(_("{0} is {1}.").format(sr.name, _(sr.status).lower()))
	if len((reason or "").strip()) < 5:
		frappe.throw(_("Give the reason for cancelling."))
	undo_demand(sr, reason)
	sr.status = "Cancelled"
	sr.result_note = reason
	sr.flags.from_steps = True
	sr.save(ignore_permissions=True)
	return {"status": sr.status}


# ---------------------------------------------------------------- the change itself
def last_field_reading(sr):
	readings = [s.reading for s in sr.steps if s.step_kind == "Field" and s.reading not in (None, 0)]
	return flt(readings[-1]) if readings else None


def check_reconnection(sr):
	owed = account.balance(sr.connection)
	plan = frappe.db.exists("Installment Plan", {"connection": sr.connection, "docstatus": 1, "plan_status": "Active"})
	if owed > 0.005 and not plan:
		frappe.throw(_("Account {0} owes Rs {1}: clear the arrears or agree an installment plan before reconnecting.").format(
			sr.connection, f"{owed:,.2f}"))


def check_no_dues(sr):
	owed = account.balance(sr.connection)
	if owed > 0.005:
		frappe.throw(_("Account {0} owes Rs {1}; a no-dues certificate needs a zero balance.").format(sr.connection, f"{owed:,.2f}"))


def complete(sr):
	"""Make the request's change. Accounts changed here pass the 'changes only through service requests' lock (FS-MST-16)."""
	before = frappe.flags.via_service_request
	frappe.flags.via_service_request = True
	try:
		_complete(sr)
	finally:
		frappe.flags.via_service_request = before


def install_first_meter(sr, conn, day):
	reading = flt(sr.new_meter_reading) or last_field_reading(sr) or 0  # the installer's reading when the form has none
	if not sr.new_meter:
		frappe.throw(_("Choose the meter that was installed."))
	conn.append("meters", {"meter": sr.new_meter, "installed_on": day, "start_reading": flt(reading),
	                       "start_export_reading": flt(sr.get("new_meter_export_reading"))})
	conn.status, conn.connection_date = "Active", day
	return reading


def merge(sr, conn, day):
	"""FS-WF-35, user decision 2026-11-16: the request's account goes into `merge_into`, with its balance, deposits and history link."""
	keep = frappe.get_doc("Connection", sr.merge_into)
	if keep.name == conn.name or keep.society != conn.society or keep.utility != conn.utility:
		frappe.throw(_("Merge {0} into another {1} account of the same society.").format(conn.name, conn.utility))
	if keep.status != "Active":
		frappe.throw(_("Account {0} is {1}; merge into an active account.").format(keep.name, _(keep.status)))
	final = flt(sr.old_meter_reading)
	current = next((m for m in conn.meters if not m.removed_on), None)
	if current:
		from utility_billing.utility_billing.readings import last_actual  # noqa: PLC0415

		last = last_actual(conn.name, add_days(day, 40))
		billed_to = flt(last.present_reading) if last and last.meter == current.meter else flt(current.start_reading)
		if final > billed_to + 0.0001:
			frappe.throw(_("Account {0}'s meter shows {1} units more than its last bill (reading {2}). Merge it once that reading is billed: "
			               "its last units are billed on its own account first.").format(conn.name, round(final - billed_to, 2), billed_to))
		current.removed_on, current.end_reading = day, final or billed_to
		current.removal_reason = _("Merged into {0} ({1})").format(keep.name, sr.name)
	account.lock(conn.name)
	account.lock(keep.name)
	bal = account.balances(conn.name)
	moved = flt(sum(bal.values()))
	if any(flt(v) for v in bal.values()):
		remarks = _("Balance moved by merging {0} into {1} ({2})").format(conn.name, keep.name, sr.name)
		account.post(conn.name, "Transfer", {b: -v for b, v in bal.items()}, "Service Request", sr.name, day, remarks)
		account.post(keep.name, "Transfer", bal, "Service Request", sr.name, day, remarks)
	deposits = frappe.get_all("Security Deposit", filters={"connection": conn.name, "docstatus": ["<", 2]}, pluck="name")
	for d in deposits:
		frappe.db.set_value("Security Deposit", d, {"connection": keep.name, "transferred_from": conn.name}, update_modified=False)
	for c in (conn.name, keep.name):
		frappe.db.set_value("Connection", c, "security_deposit", held(c), update_modified=False)
	holders.record(conn, "Merged", day, old_value=conn.name, new_value=keep.name, reading=final, sr=sr.name)
	conn.status, conn.disconnected_on, conn.merged_into = "Closed", day, keep.name
	conn.save(ignore_permissions=True)
	holders.record(keep, "Merged", day, old_value=conn.name, new_value=keep.name, sr=sr.name)
	keep.save(ignore_permissions=True)
	return _("Account {0} merged into {1} on {2}: balance Rs {3} and {4} deposit record(s) moved; {0} is closed.").format(
		conn.name, keep.name, day, f"{moved:,.2f}", len(deposits))


def _complete(sr):
	day = getdate(sr.effective_date or nowdate())
	note = ""
	conn = frappe.get_doc("Connection", sr.connection) if sr.connection else None
	k = sr.kind
	if k in (DISC, RECON, CLOSE, T_OUT, MERGE) and not flt(sr.old_meter_reading) and last_field_reading(sr):
		sr.old_meter_reading = last_field_reading(sr)  # the reading taken on the visit
	if k in CREATES_CONNECTION:
		reading = install_first_meter(sr, conn, day)
		conn.save(ignore_permissions=True)
		if sr.seal_no:
			frappe.db.set_value("Meter", sr.new_meter, "seal_no", sr.seal_no)
		note = _("Connection {0} is active from {1} with meter {2} at {3}; billing starts with the part month.").format(conn.name, day, sr.new_meter, flt(reading))
	elif k == SPLIT:
		new = frappe.get_doc("Connection", sr.new_connection)
		reading = install_first_meter(sr, new, day)
		holders.record(new, "Split", day, old_value=conn.name, new_value=new.name, sr=sr.name)
		new.save(ignore_permissions=True)
		if sr.seal_no:
			frappe.db.set_value("Meter", sr.new_meter, "seal_no", sr.seal_no)
		holders.record(conn, "Split", day, old_value=conn.name, new_value=new.name, sr=sr.name)
		conn.save(ignore_permissions=True)
		note = _("Account {0} split off from {1}: active from {2} with meter {3} at {4}. {1} keeps its balance and history.").format(
			new.name, conn.name, day, sr.new_meter, flt(reading))
	elif k == MERGE:
		note = merge(sr, conn, day)
	elif k == METER:
		if not sr.new_meter:
			frappe.throw(_("Choose the new meter."))
		old = [r for r in conn.meters if not r.removed_on]
		for r in old:
			r.removed_on, r.end_reading, r.removal_reason = day, flt(sr.old_meter_reading), sr.meter_reason or _("Meter change {0}").format(sr.name)
			r.unreadable = cint(sr.get("old_meter_unreadable"))  # FS-WF-08: no true-up of averaged months from an unreadable meter
			r.end_export_reading = flt(sr.get("old_meter_export_reading"))  # net metering: the exports since its last reading are credited
		conn.append("meters", {"meter": sr.new_meter, "installed_on": day, "start_reading": flt(sr.new_meter_reading),
		                       "start_export_reading": flt(sr.get("new_meter_export_reading"))})
		conn.save(ignore_permissions=True)
		if sr.seal_no:
			frappe.db.set_value("Meter", sr.new_meter, "seal_no", sr.seal_no)
		note = _("Meter {0} replaced by {1} on {2}; the month's units are split between them.").format(old[0].meter if old else "-", sr.new_meter, day)
		if old and frappe.db.get_value("Meter", old[0].meter, "status") == "Defective":
			note += " " + (_("The months billed on averages stand: the old meter couldn't be read.") if cint(sr.get("old_meter_unreadable"))
			               else _("The next reading trues up the months billed on averages from the old meter's final reading."))
	elif k == DISC:
		conn.status, conn.disconnected_on = "Disconnected", day
		conn.save(ignore_permissions=True)
		note = _("Disconnected on {0}; fixed charges stop from that day.").format(day)
	elif k == RECON:
		check_reconnection(sr)
		conn.status, conn.disconnected_on = "Active", None
		conn.save(ignore_permissions=True)
		note = _("Reconnected on {0}.").format(day)
	elif k == CLOSE:
		conn.status, conn.disconnected_on = "Closed", day
		conn.save(ignore_permissions=True)
		have, owed = held(conn.name), account.balance(conn.name)
		if have > 0 and owed > 0.005:
			sr.security_deposit = new_deposit(sr, "Offset Against Arrears", round(min(have, owed), 2))
			note = _("Account closed. Deposit offset {0} drafted for approval; refund the rest once it is approved.").format(sr.security_deposit)
		elif have > 0:
			sr.security_deposit = new_deposit(sr, "Refunded", have)
			note = _("Account closed. Deposit refund {0} drafted for approval.").format(sr.security_deposit)
		else:
			note = _("Account closed; no deposit held.") + (_(" It still owes Rs {0}: recover or write it off.").format(f"{owed:,.2f}") if owed > 0.005 else "")
	elif k == CAT:
		old, conn.consumer_category = conn.consumer_category, sr.new_category
		# the bill whose period contains the change is split at it (increment 22): by the visit's reading when one was taken
		holders.record(conn, holders.CATEGORY, day, old_value=old, new_value=sr.new_category, reading=last_field_reading(sr), sr=sr.name)
		conn.save(ignore_permissions=True)
		note = _("Category changed from {0} to {1}, effective {2}.").format(old, sr.new_category, day)
	elif k == LOAD:
		old, conn.sanctioned_load_kw = conn.sanctioned_load_kw, flt(sr.new_load_kw)
		conn.save(ignore_permissions=True)
		note = _("Sanctioned load changed from {0} kW to {1} kW, effective {2}.").format(flt(old), flt(sr.new_load_kw), day)
	elif k == OWNER:
		check_no_dues(sr)
		old = conn.consumer
		change_holder(conn, sr.new_consumer, day, role="Owner", sr=sr)
		note = _("Account transferred from {0} to {1} on {2}; its history stays with the account.").format(old, sr.new_consumer, day)
	elif k == T_IN:
		change_holder(conn, sr.new_consumer, day, role="Tenant", owner_liable=cint(sr.owner_liable), sr=sr)
		note = _("Bills go to tenant {0} from {1}.").format(sr.new_consumer, day)
	elif k == T_OUT:
		tenant = conn.consumer
		change_holder(conn, sr.new_consumer, day, role="Owner", leaving=tenant, sr=sr)
		note = _("Tenant {0} left on {1}; bills go to {2} again.").format(tenant, day, sr.new_consumer)
	elif k == NODUES:
		check_no_dues(sr)
		sr.certificate_no = f"NDC-{getdate(nowdate()).year}-{int(getseries(f'UB-NDC-{getdate(nowdate()).year}', 5)):05d}"
		sr.verification_code = secrets.token_hex(4).upper()
		note = _("No-dues certificate {0} issued; anyone can check it with code {1}.").format(sr.certificate_no, sr.verification_code)
	elif k == CONTACT:
		c = frappe.get_doc("Consumer", sr.consumer or conn.consumer)
		for src, dst in (("new_mobile", "mobile_no"), ("new_email", "email"), ("new_address", "address")):
			if sr.get(src):
				c.set(dst, sr.get(src))
		c.save(ignore_permissions=True)
		note = _("Contact details of {0} updated.").format(c.consumer_name)
	elif k == TEST:
		sr.adjustment, note = meter_test_adjustment(sr)
	elif k == DEPOSIT:
		if sr.deposit_change == "Refund":
			sr.security_deposit = new_deposit(sr, "Refunded", flt(sr.deposit_amount))
		note = _("Deposit {0} {1} drafted for approval.").format(_(sr.deposit_change or "").lower(), sr.security_deposit or "")
	elif k == DETECT:
		sr.adjustment = new_adjustment(sr, "Debit Note", flt(sr.assessed_amount), "DETECTION",
		                               _("Detection bill: {0} units assessed ({1})").format(flt(sr.assessed_units), sr.name))
		note = _("Detection bill of Rs {0} drafted as debit note {1} for approval.").format(f"{flt(sr.assessed_amount):,.0f}", sr.adjustment)
	sr.status, sr.completed_on, sr.result_note = "Completed", now_datetime(), note
	sr.flags.from_steps = True
	sr.save(ignore_permissions=True)


def change_holder(conn, new_consumer, day, role, owner_liable=1, leaving=None, sr=None):
	"""The account is billed to `new_consumer` from `day`; the property's occupants follow (FS-MST-03, FS-WF-27, 28)."""
	if not new_consumer:
		frappe.throw(_("Choose the new consumer."))
	prop = frappe.get_doc("Property", conn.property)
	before = add_days(day, -1)
	# a tenant leaving: the owner answers for what the tenant leaves unpaid when the tenancy said so (increment 22)
	owner_answers = leaving and any(r.occupant_role == "Owner" and not cint(r.is_billed) and cint(r.liable_for_arrears) and not r.to_date
	                                for r in prop.occupants)
	outgoing = conn.consumer
	for r in list(prop.occupants):
		if r.to_date and getdate(r.to_date) < day:
			continue
		ends = (role == "Owner" and r.occupant_role == "Owner" and not leaving) or (leaving and r.consumer == leaving) or (cint(r.is_billed) and r.consumer != new_consumer)
		if not ends:
			continue
		keep_owner = r.occupant_role == "Owner" and role == "Tenant"
		r.to_date = before if getdate(r.from_date) <= before else r.from_date
		if keep_owner:  # the owner stays owner, no longer billed
			prop.append("occupants", {"consumer": r.consumer, "occupant_role": "Owner", "from_date": day, "share_percent": r.share_percent,
			                          "is_billed": 0, "liable_for_arrears": owner_liable})
	existing_owner = next((r for r in prop.occupants if r.consumer == new_consumer and r.occupant_role == "Owner" and not r.to_date), None)
	if existing_owner:
		existing_owner.is_billed = 1
	else:
		prop.append("occupants", {"consumer": new_consumer, "occupant_role": role, "from_date": day, "share_percent": 100 if role == "Owner" else 0,
		                          "is_billed": 1, "liable_for_arrears": 0 if role == "Tenant" and owner_liable else 1})
	prop.save(ignore_permissions=True)
	conn.consumer = new_consumer
	conn.consumer_name = frappe.db.get_value("Consumer", new_consumer, "consumer_name")
	if outgoing and outgoing != new_consumer:  # the month is split into a final bill and the incoming holder's bill (increment 22)
		holders.record(conn, holders.HOLDER, day, old_value=frappe.db.get_value("Consumer", outgoing, "consumer_name"), new_value=conn.consumer_name,
		               old_consumer=outgoing, new_consumer=new_consumer, reading=last_field_reading(sr) if sr else None,
		               sr=sr.name if sr else None, owner_liable=1 if owner_answers else 0)
	conn.save(ignore_permissions=True)


def new_adjustment(sr, note_type, amount, reason, remarks):
	if amount <= 0:
		frappe.throw(_("There is no amount to adjust."))
	bucket = "Arrears"
	if note_type == "Credit Note":  # a credit note can't exceed the balance it reduces: take the larger of current bill and arrears
		bal = account.balances(sr.connection)
		bucket = "Current bill" if flt(bal["current"]) >= flt(bal["arrears"]) else "Arrears"
	a = frappe.get_doc({"doctype": "Account Adjustment", "note_type": note_type, "connection": sr.connection, "bucket": bucket,
	                    "amount": round(amount, 2), "reason_code": reason, "remarks": remarks})
	a.flags.ignore_permissions = True
	a.insert()
	return a.name


def meter_test_adjustment(sr):
	"""A meter found fast or slow: the energy of the last months' bills is corrected by the test result (FS-WF-32)."""
	pct = flt(sr.test_error_percent)
	if not pct:
		return None, _("The meter tested correct; nothing to revise.")
	months = cint(sr.months_to_revise) or 1
	bills = frappe.get_all("Utility Bill", filters={"connection": sr.connection, "docstatus": 1, "status": "Posted"},
	                       pluck="name", order_by="billing_month desc", limit=months)
	energy_type = frappe.db.get_value("Society", sr.society, "energy_charge_type")
	energy = sum(flt(x) for x in frappe.get_all("Bill Line", filters={"parent": ["in", bills or [""]], "charge_type": energy_type}, pluck="amount"))
	diff = round(energy * abs(pct) / (100 + pct), 2)  # what was billed beyond (or short of) the true energy
	kind = "Credit Note" if pct > 0 else "Debit Note"
	name = new_adjustment(sr, kind, diff, "METER-TEST", _("Meter test {0}%: energy of {1} bill(s) revised ({2})").format(pct, len(bills), sr.name))
	return name, _("Meter {0} by {1}%: {2} of Rs {3} drafted ({4}) for approval.").format(
		_("fast") if pct > 0 else _("slow"), abs(pct), _(kind).lower(), f"{diff:,.0f}", name)


# ---------------------------------------------------------------- certificates
@frappe.whitelist(allow_guest=True)
def verify_certificate(code: str):
	"""Anyone can check a no-dues certificate by its verification code (FS-WF-29)."""
	code = (code or "").strip().upper()
	if len(code) < 6:
		return {"valid": False}
	r = frappe.db.get_value("Service Request", {"verification_code": code, "kind": NODUES, "status": "Completed"},
	                        ["certificate_no", "connection", "consumer_name", "completed_on", "society"], as_dict=True)
	if not r:
		return {"valid": False}
	return {"valid": True, "certificate_no": r.certificate_no, "account": r.connection, "consumer": r.consumer_name,
	        "issued_on": str(getdate(r.completed_on)), "society": frappe.db.get_value("Society", r.society, "society_name")}


def tell_consumer(sr, what=None):
	"""The request's news for the consumer, in a line: received, a step done and the next one, completed, rejected or cancelled."""
	from utility_billing.utility_billing import notify  # noqa: PLC0415

	t = frappe.get_cached_value("Service Request Type", sr.request_type, "type_name") or sr.kind
	nxt = next((s.step_name for s in sr.steps if s.idx == cint(sr.current_step)), None)
	done = [s for s in sr.steps if s.status == "Done"]
	if sr.status == "Completed":
		text = _("{0} {1} is completed.").format(t, sr.name)
	elif sr.status == "Rejected":
		why = next((s.remarks for s in sr.steps if s.status == "Rejected"), "") or ""
		text = _("{0} {1} was not approved: {2}").format(t, sr.name, why)
	elif sr.status == "Cancelled":
		text = _("{0} {1} was cancelled: {2}").format(t, sr.name, sr.result_note or "")
	elif what:
		text = _("{0} {1} is {2}.").format(t, sr.name, what) + (" " + _("Next: {0}.").format(nxt) if nxt else "")
	else:
		text = _("{0} {1}: {2} done.").format(t, sr.name, done[-1].step_name if done else "") + (" " + _("Next: {0}.").format(nxt) if nxt else "")
	return notify.request_updated(sr, text.strip())
