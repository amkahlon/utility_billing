# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill run wizard (page ub-billrun), designed after the mock-up "Bill run wizard" (2026-11-16): one month's run as six steps (cycle,
readings, calculate, review exceptions, approve and post, print and notify), the figures that matter at each, and the bills that need a
look before approval: very high, zero use, estimated, defective meter, and accounts that couldn't be billed. Each is accepted, held (left
out of the run, billed later in a supplementary run) or corrected at its reading. The run is the ordinary Bill Run, so calculation,
maker-checker approval and posting are unchanged (FS-RUN-01 to 06)."""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from utility_billing.utility_billing import billing

HIGH_FACTOR, HIGH_MIN_UNITS = 2.0, 100


def _run(name):
	run = frappe.get_doc("Bill Run", name)
	run.check_permission("read")
	return run


def review_of(run) -> dict:
	try:
		return json.loads(run.get("exception_review") or "{}")
	except ValueError:
		return {}


def exceptions(run) -> list[dict]:
	"""The run's bills that need a look, each with why, and the accounts the run couldn't bill."""
	bills = frappe.get_all("Utility Bill", filters={"bill_run": run.name, "docstatus": ["<", 2]},
	                       fields=["name", "connection", "consumer_name", "property", "units", "current_amount", "consumption_type", "meter_reading"])
	conns = [b.connection for b in bills] or [""]
	history = {}
	for h in frappe.db.sql("""select connection, units from `tabUtility Bill` where connection in %s and docstatus = 1 and billing_month < %s
	                          order by billing_month desc""", (conns, getdate(run.billing_month)), as_dict=True):
		history.setdefault(h.connection, [])
		if len(history[h.connection]) < 3:
			history[h.connection].append(flt(h.units))
	readings = {r.name: r for r in frappe.get_all("Meter Reading", filters={"name": ["in", [b.meter_reading for b in bills if b.meter_reading] or [""]]},
	                                            fields=["name", "status", "defective"])}
	houses = {p.name: p.property_no for p in frappe.get_all("Property", filters={"name": ["in", [b.property for b in bills] or [""]]},
	                                                     fields=["name", "property_no"])}
	metered = billing.is_metered(run.utility)
	out = []
	for b in bills:
		avg = round(sum(history.get(b.connection, [])) / len(history[b.connection]), 1) if history.get(b.connection) else None
		r = readings.get(b.meter_reading)
		kind = None
		if r and cint(r.defective):
			kind = "Defective meter"
		elif (r and r.status == "Estimated") or b.consumption_type == "Estimated":
			kind = "Estimated"
		elif metered and flt(b.units) == 0 and (avg or 0) > 0:
			kind = "Zero use"
		elif avg and flt(b.units) >= HIGH_MIN_UNITS and flt(b.units) > HIGH_FACTOR * avg:
			kind = "Very high bill"
		if kind:
			out.append({"key": b.name, "kind": kind, "bill": b.name, "connection": b.connection, "consumer_name": b.consumer_name,
			            "house": houses.get(b.property) or b.property, "units": flt(b.units), "average": avg, "amount": flt(b.current_amount),
			            "reading": b.meter_reading})
	for p in run.problems:
		out.append({"key": f"problem:{p.connection}", "kind": "Couldn't bill", "connection": p.connection, "problem": p.problem,
		            "consumer_name": frappe.db.get_value("Connection", p.connection, "consumer_name"), "house": None, "units": None,
		            "average": None, "amount": None})
	return out


@frappe.whitelist()
def load(name: str):
	run = _run(name)
	cycle = frappe.db.get_value("Reading Cycle", run.reading_cycle, ["name", "status", "total_readings", "pending_readings", "flagged_readings",
	                                                                "estimated_readings", "valid_readings", "reading_from", "reading_to"], as_dict=True) \
		if run.reading_cycle else None
	defective = frappe.db.count("Meter Reading", {"reading_cycle": run.reading_cycle, "defective": 1}) if run.reading_cycle else 0
	groups = frappe.db.sql("""select ifnull(l.charge_group, 'Other') grp, sum(l.amount) amount from `tabBill Line` l join `tabUtility Bill` b
	                          on b.name = l.parent where b.bill_run = %s and b.docstatus < 2 group by l.charge_group order by amount desc""", run.name, as_dict=True)
	metered = billing.is_metered(run.utility)
	review = review_of(run)
	ex = exceptions(run) if run.calculation_status == "Calculated" else []
	for e in ex:
		e["decision"] = review.get(e["key"])
	left = sum(1 for e in ex if not e["decision"])
	posted = run.docstatus == 1 and (run.get("posting_status") or "Posted") == "Posted"
	notified = posted and frappe.db.exists("Message Log", {"reference_doctype": "Utility Bill", "event": "bill.issued",
	                                                       "reference_name": ["in", frappe.get_all("Utility Bill", filters={"bill_run": run.name}, pluck="name", limit=50) or [""]]})
	steps = [
		# a flat-rate utility (maintenance, for example) has no readings, so its first two steps don't apply
		{"label": _("Select cycle"), "done": bool(cycle) or not metered},
		{"label": _("Check readings"), "done": bool(cycle and cycle.status == "Closed") or not metered},
		{"label": _("Calculate"), "done": run.calculation_status == "Calculated"},
		{"label": _("Review exceptions"), "done": run.calculation_status == "Calculated" and left == 0},
		{"label": _("Approve and post"), "done": posted},
		{"label": _("Print and notify"), "done": bool(notified)},
	]
	from frappe.model.workflow import get_transitions  # noqa: PLC0415

	try:
		actions = sorted({x.get("action") for x in get_transitions(run)})
	except Exception:  # noqa: BLE001
		actions = []
	return {
		"name": run.name, "title": run.get("run_title") or run.name, "society": run.society, "utility": run.utility,
		"month": getdate(run.billing_month).strftime("%B %Y"), "issue_date": str(run.issue_date), "due_date": str(run.due_date),
		"owner": frappe.db.get_value("User", run.owner, "full_name") or run.owner, "state": run.workflow_state or ("Approved" if run.docstatus == 1 else "Draft"),
		"docstatus": run.docstatus, "calculation_status": run.calculation_status, "posting_status": run.get("posting_status"),
		"calculated_on": str(run.calculated_on) if run.get("calculated_on") else None,
		"cycle": cycle, "defective": defective, "bill_count": cint(run.bill_count), "total_current": flt(run.total_current),
		"total_payable": flt(run.total_payable), "groups": [{"group": g.grp, "amount": flt(g.amount)} for g in groups],
		"exceptions": ex, "left": left, "steps": steps, "actions": actions,
		"can_review": run.docstatus == 0 and bool(frappe.has_permission("Bill Run", "write", run)),
		"can_calculate": run.docstatus == 0 and run.workflow_state in (None, "", "Draft", "Rejected") and frappe.has_permission("Bill Run", "write", run),
		"reversed": reversed_info(run) if run.docstatus == 2 else None,
	}


def next_run(run):
	"""The month's run that replaces a reversed one (started again), if there is one."""
	return frappe.db.get_value("Bill Run", {"society": run.society, "utility": run.utility, "billing_month": run.billing_month,
	                                        "supplementary": cint(run.get("supplementary")), "docstatus": ["<", 2]}, "name")


def reversed_info(run) -> dict:
	"""What the wizard says about a reversed run (increment 59): it used to show the run as if it were waiting at "Approve and post"."""
	when = frappe.db.sql("""select max(e.posted_on) from `tabCustomer Ledger Entry` e join `tabUtility Bill` b on b.name = e.voucher_no
		where e.voucher_type = 'Utility Bill' and e.entry_type = 'Reversal' and b.bill_run = %s""", run.name)[0][0]
	return {
		"on": str(when or run.modified), "by": frappe.db.get_value("User", run.modified_by, "full_name") or run.modified_by,
		"reason": run.get("cancellation_reason"),
		"bills": frappe.db.count("Utility Bill", {"bill_run": run.name, "docstatus": 2, "status": "Cancelled"}),
		"journal": run.get("gl_journal"), "reversal_journal": run.get("reversal_journal"),
		"messages_cancelled": frappe.db.count("Message Log", {"reference_doctype": "Utility Bill", "status": "Cancelled",
		                                                      "reference_name": ["in", frappe.get_all("Utility Bill", filters={"bill_run": run.name},
		                                                                                              pluck="name") or [""]]}),
		"next_run": next_run(run),
		"can_start_again": bool(frappe.has_permission("Bill Run", "create")),
	}


@frappe.whitelist(methods=["POST"])
def start_again(name: str):
	"""A reversed run's month billed afresh: a new draft run amending it (same society, utility, month, reading cycle, issue date and
	exclusions), ready to calculate. If the month already has a run again, that one is returned."""
	run = _run(name)
	if run.docstatus != 2:
		frappe.throw(_("Bill run {0} isn't reversed.").format(run.name))
	if not frappe.has_permission("Bill Run", "create"):
		raise frappe.PermissionError(_("You can't start bill runs."))
	existing = next_run(run)
	if existing:
		return {"name": existing, "new": False}
	new = frappe.copy_doc(run, ignore_no_copy=False)  # as the form's Amend: nothing of the reversed run's state, totals or journals
	new.docstatus, new.amended_from = 0, run.name
	new.insert()
	return {"name": new.name, "new": True}


@frappe.whitelist(methods=["POST"])
def decide(name: str, key: str, decision: str):
	"""Accept an exception, or hold its account out of the run (it is billed later in a supplementary run; the run is calculated again)."""
	_decide(name, [key], decision)
	return load(name)


@frappe.whitelist(methods=["POST"])
def decide_many(name: str, keys: str | list, decision: str):
	"""The same decision for many exceptions at once (increment 53, the user: "do i need to review them one by one? must be available for
	bulk action"), e.g. the 432 estimated bills of the demo run. An account the run couldn't bill can be held, not accepted."""
	keys = json.loads(keys) if isinstance(keys, str) else list(keys or [])
	done = _decide(name, keys, decision)
	return {**load(name), "changed": done}


def _decide(name, keys, decision):
	run = frappe.get_doc("Bill Run", name)
	run.check_permission("write")
	if run.docstatus != 0:
		frappe.throw(_("The run is posted; its bills can't be held any more."))
	if decision not in ("Accepted", "Held", ""):
		frappe.throw(_("Unknown decision."))
	if decision == "Accepted":
		keys = [k for k in keys if not k.startswith("problem:")]  # nothing to accept: the account has no bill
	review = review_of(run)
	for key in keys:
		if decision:
			review[key] = decision
		else:
			review.pop(key, None)
	run.db_set("exception_review", json.dumps(review), update_modified=False)
	if decision == "Held":
		bills = [k for k in keys if not k.startswith("problem:")]
		of_bill = dict(frappe.get_all("Utility Bill", filters={"name": ["in", bills]}, fields=["name", "connection"], as_list=True)) if bills else {}
		held = {e.connection for e in run.exclusions}
		added = 0
		for key in keys:
			conn = key.split(":", 1)[1] if key.startswith("problem:") else of_bill.get(key)
			if conn and conn not in held:
				run.append("exclusions", {"connection": conn, "reason": _("Held at review")})
				held.add(conn)
				added += 1
		if added:
			run.flags.ignore_version = len(keys) > 50  # a long list of holds would make a large change-history record (increment 52)
			run.save()  # the run needs calculating again without them
	return len(keys)
