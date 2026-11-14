# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Run snapshot (FS-RUN-13): what a bill run is about to post, kept before it is posted.

The snapshot holds the draft bills of the run and the balances of every account they will touch. Restoring it reverses
the run through the normal reversal (so the same rules apply: nothing else may have been posted to those accounts after the
bills) and then checks every account is back to the balance it had before posting.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from utility_billing.utility_billing import account

RESTORERS = {"System Manager", "General Manager", "Billing Manager"}


def take(run):
	"""Called just before a bill run posts its bills. Never blocks posting if it can't be written."""
	bills = frappe.get_all("Utility Bill", filters={"bill_run": run.name, "docstatus": 0},
	                       fields=["name", "connection", "current_amount", "arrears", "payable_within_due"])
	balances = {}
	for b in bills:
		bal = account.balances(b.connection)
		balances[b.connection] = {k: flt(v) for k, v in bal.items()}
	owed = sum(sum(v for k, v in bal.items() if k != "credit") - bal.get("credit", 0) for bal in balances.values())
	doc = frappe.get_doc({
		"doctype": "Run Snapshot", "bill_run": run.name, "society": run.society, "taken_on": now_datetime(), "taken_by": frappe.session.user,
		"bill_count": len(bills), "total_payable": sum(flt(b.payable_within_due) for b in bills), "accounts": len(balances),
		"owed_before": owed, "status": "Kept",
		"data": json.dumps({"run": run.name, "bills": [dict(b) for b in bills], "balances": balances}, default=str),
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def latest(run):
	return frappe.db.get_value("Run Snapshot", {"bill_run": run, "status": "Kept"}, "name", order_by="creation desc")


@frappe.whitelist(methods=["POST"])
def restore(snapshot: str, reason: str):
	"""Reverse the run and compare every account with its balance before posting."""
	if not set(frappe.get_roles()) & RESTORERS:
		raise frappe.PermissionError(_("Only a manager can restore a bill run snapshot."))
	snap = frappe.get_doc("Run Snapshot", snapshot)
	if snap.status == "Restored":
		frappe.throw(_("Snapshot {0} was already restored.").format(snap.name))
	run = frappe.get_doc("Bill Run", snap.bill_run)
	if run.docstatus != 1:
		frappe.throw(_("Bill run {0} isn't posted, so there is nothing to restore.").format(run.name))
	run.cancellation_reason = reason
	run.flags.ignore_permissions = True
	run.cancel()
	data = json.loads(snap.data or "{}")
	different = []
	for connection, before in (data.get("balances") or {}).items():
		now = {k: flt(v) for k, v in account.balances(connection).items()}
		if any(abs(now.get(k, 0) - flt(before.get(k))) > 0.005 for k in before):
			different.append(connection)
	checked = len(data.get("balances") or {})
	result = _("Run {0} reversed; {1} of {2} accounts are back to their balance before posting.").format(run.name, checked - len(different), checked)
	if different:
		result += " " + _("Differences: {0}.").format(", ".join(different[:10]) + (f" (+{len(different) - 10} more)" if len(different) > 10 else ""))
	snap.db_set({"status": "Restored", "restored_on": now_datetime(), "restored_by": frappe.session.user, "restore_result": result})
	return {"result": result, "different": different}
