# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Connection flags and what they change (FS-MST-14). A flag is on a connection between its dates; its type says whether it holds the
bill, the late payment surcharge, or recovery. Billing, the surcharge job and recovery runs ask here."""

import frappe
from frappe.utils import getdate

HOLD_BILLING, NO_SURCHARGE, HOLD_RECOVERY = "hold_billing", "no_surcharge", "hold_recovery"

DEFAULTS = (
	("STAFF", "Staff", "A member of staff of the society or the client. Recorded for reports; billed as usual.", 0, 0, 0),
	("EXEMPT", "Exempt", "Exempt from late payment surcharge and kept out of recovery.", 0, 1, 1),
	("DISPUTE", "Under dispute", "The consumer disputes the account; kept out of recovery while it is decided.", 0, 0, 1),
	("COURT", "Court case", "A court case is open; no surcharge and kept out of recovery.", 0, 1, 1),
	("BILL-HOLD", "Bill on hold", "The bill is held back from bill runs until the flag is removed.", 1, 0, 0),
)


def create_default_flags():
	for code, name, text, hold_bill, no_sur, hold_rec in DEFAULTS:
		if not frappe.db.exists("Connection Flag Type", code):
			frappe.get_doc({"doctype": "Connection Flag Type", "flag_code": code, "flag_name": name, "description": text, "hold_billing": hold_bill,
			                "no_surcharge": no_sur, "hold_recovery": hold_rec}).insert(ignore_permissions=True)


def active(connection, start, end=None) -> list[dict]:
	"""Flags on the connection at any time between `start` and `end` (default: that one day): [{flag, name, hold_billing, no_surcharge, hold_recovery}]."""
	start, end = getdate(start), getdate(end or start)
	return frappe.db.sql(
		"""select r.flag, t.flag_name name, t.hold_billing, t.no_surcharge, t.hold_recovery
		from `tabConnection Flag Row` r join `tabConnection Flag Type` t on t.name = r.flag
		where r.parent = %s and r.parenttype = 'Connection' and t.enabled = 1 and r.from_date <= %s and (r.to_date is null or r.to_date >= %s)
		order by r.idx""", (connection, end, start), as_dict=True)


def effect(connection, effect_name, start, end=None):
	"""The name of the first flag with this effect on the connection in the period, or None."""
	for f in active(connection, start, end):
		if f.get(effect_name):
			return f.name
	return None


def held(society, effect_name, on_date) -> dict[str, str]:
	"""{connection: flag name} for every connection of the society with a flag of this effect on the date (recovery runs use it in bulk)."""
	rows = frappe.db.sql(
		f"""select r.parent connection, t.flag_name from `tabConnection Flag Row` r
		join `tabConnection Flag Type` t on t.name = r.flag join `tabConnection` c on c.name = r.parent
		where r.parenttype = 'Connection' and c.society = %s and t.enabled = 1 and t.{effect_name} = 1
		and r.from_date <= %s and (r.to_date is null or r.to_date >= %s) order by r.idx""", (society, getdate(on_date), getdate(on_date)), as_dict=True)
	out = {}
	for r in rows:
		out.setdefault(r.connection, r.flag_name)
	return out
