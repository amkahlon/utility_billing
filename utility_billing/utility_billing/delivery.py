# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Bill delivery tracking (FS-RUN-10): printed, handed to a distributor, delivered, sent by message.

The latest status sits on the bill; every change is also a row of its delivery history. Printing a run's bills and a
message that goes out record themselves; hand-over to a distributor and delivery are recorded by staff for a whole
run, block or route, or for chosen bills.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime, nowdate

PRINTED, HANDED, DELIVERED, MESSAGE, RETURNED, NOT_DELIVERED = (
	"Printed", "Handed to Distributor", "Delivered", "Sent by Message", "Returned", "Not Delivered")
STATUSES = (PRINTED, HANDED, DELIVERED, MESSAGE, RETURNED, NOT_DELIVERED)
STAFF = {"System Manager", "Billing Manager", "General Manager", "Billing Officer"}
# a later status of the same kind doesn't undo a stronger one (a reprint after delivery isn't "Printed" again)
RANK = {"": 0, PRINTED: 1, HANDED: 2, MESSAGE: 3, NOT_DELIVERED: 3, RETURNED: 3, DELIVERED: 4}


def record(bills, status, distributor=None, remarks=None, user=None, keep_stronger=False):
	"""Set the delivery status of posted bills (names). Returns how many changed."""
	if status not in STATUSES:
		frappe.throw(_("Unknown delivery status {0}.").format(status))
	now, changed = now_datetime(), 0
	for name in bills:
		b = frappe.db.get_value("Utility Bill", name, ["docstatus", "delivery_status", "distributor"], as_dict=True)
		if not b or b.docstatus != 1:
			continue
		if keep_stronger and RANK.get(b.delivery_status or "", 0) >= RANK[status]:
			continue
		if status == b.delivery_status and (distributor or "") == (b.distributor or ""):
			continue
		count = frappe.db.count("Bill Delivery Event", {"parent": name, "parentfield": "delivery_log"})
		frappe.get_doc({"doctype": "Bill Delivery Event", "parent": name, "parenttype": "Utility Bill", "parentfield": "delivery_log", "idx": count + 1,
		                "status": status, "on": now, "by_user": user or frappe.session.user, "distributor": distributor, "remarks": remarks}).db_insert()
		values = {"delivery_status": status, "delivery_date": getdate(nowdate())}
		if distributor:
			values["distributor"] = distributor
		frappe.db.set_value("Utility Bill", name, values, update_modified=False)
		changed += 1
	return changed


def _bills(run, block, route, only=None):
	from utility_billing.utility_billing import bill_print  # noqa: PLC0415

	rows = bill_print.run_bills(run, block or None, route or None)
	names = [r.name for r in rows]
	if only:
		names = [n for n in names if n in set(only)]
	return names


@frappe.whitelist(methods=["POST"])
def mark(run: str, status: str, block: str | None = None, route: str | None = None, distributor: str | None = None,
         remarks: str | None = None, bills: str | None = None):
	"""Record a delivery status for the posted bills of a run, optionally one block or route or chosen bills (a JSON list)."""
	if not set(frappe.get_roles()) & STAFF:
		raise frappe.PermissionError(_("You can't record bill delivery."))
	frappe.get_doc("Bill Run", run).check_permission("read")
	only = frappe.parse_json(bills) if bills else None
	if status == HANDED and not (distributor or "").strip():
		frappe.throw(_("Name the distributor the bills are handed to."))
	names = _bills(run, block, route, only)
	if not names:
		frappe.throw(_("No bills match."))
	return {"bills": len(names), "changed": record(names, status, distributor, remarks, keep_stronger=status == PRINTED)}


@frappe.whitelist()
def summary(run: str):
	"""Delivery status counts for a run."""
	frappe.get_doc("Bill Run", run).check_permission("read")
	rows = frappe.db.sql(
		"""select ifnull(nullif(b.delivery_status, ''), 'Not recorded') status, count(*) bills
		from `tabUtility Bill` b where b.bill_run = %s and b.docstatus = 1 group by 1""", run, as_dict=True)
	total = sum(cint(r.bills) for r in rows)
	return {"total": total, "by_status": {r.status: cint(r.bills) for r in rows}}
