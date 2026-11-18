# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Old account numbers from a society's previous system (increment 29, migration). User's decision, 2026-11-18: old numbers stay
usable for six months after go-live. Before go-live (during the parallel run) they are always accepted; after it, up to the society's
"Old Account Numbers Accepted Until" date, which is set to go-live + 6 months when the opening balances post."""

import frappe
from frappe.utils import add_months, getdate, nowdate

MONTHS_ACCEPTED = 6


def accepted(society, on=None) -> bool:
	until = frappe.get_cached_value("Society", society, "old_numbers_until") if society else None
	return not until or getdate(on or nowdate()) <= getdate(until)


def connection_of(number):
	"""The account an old account or collection number belongs to, while its society accepts old numbers; None if unknown, ambiguous
	(two societies of the site used the same number) or no longer accepted."""
	number = (number or "").strip()
	if not number:
		return None
	rows = frappe.db.sql("""select name, society from `tabConnection` where legacy_account_no = %(n)s or legacy_collection_no = %(n)s""",
	                     {"n": number}, as_dict=True)
	if len(rows) != 1:
		return None
	return rows[0].name if accepted(rows[0].society) else None


def set_window(society, go_live):
	"""Called when the opening balances post: old numbers stay accepted for six months after go-live."""
	frappe.db.set_value("Society", society, "old_numbers_until", add_months(getdate(go_live), MONTHS_ACCEPTED))
	frappe.clear_document_cache("Society", society)
