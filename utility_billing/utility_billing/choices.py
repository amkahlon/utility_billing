# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Values a form offers in a drop-down instead of free typing (bug report 2026-11-16: "saved reading routes should be shown as a
drop-down"; the same holds for every block, sector and office box). A society's blocks, sectors and offices come from its
Offices, Counters and Blocks list and from what its properties already use; a new block can still be typed on a property."""

import frappe
from frappe import _

KINDS = {"block": ("Block", "block"), "sector": ("Sector", "sector"), "office": ("Office", None)}


@frappe.whitelist()
def values(kind: str, society: str | None = None) -> list[str]:
	if kind not in KINDS:
		frappe.throw(_("Unknown list {0}.").format(kind))
	office_type, property_field = KINDS[kind]
	types = ["Office", "Collection Counter"] if kind == "office" else [office_type]
	filters = {"parenttype": "Society", "office_type": ["in", types]}
	if society:
		filters["parent"] = society
	out = set(frappe.get_all("Society Office", filters=filters, pluck="office_name"))
	if property_field:
		pf = {property_field: ["is", "set"]}
		if society:
			pf["society"] = society
		out |= set(frappe.get_all("Property", filters=pf, pluck=property_field, distinct=True))
	return sorted(x for x in out if x)


def default_counter(user: str | None = None) -> str | None:
	"""The counter a cashier opens a day at by default: the one of their last day, else their only counter in User Access."""
	user = user or frappe.session.user
	last = frappe.db.get_value("Cashier Day", {"cashier": user}, "cash_counter", order_by="creation desc")
	if last and frappe.db.get_value("Cash Counter", last, "enabled"):
		return last
	mine = frappe.get_all("Access Counter", filters={"parenttype": "User Access", "parent": user}, pluck="cash_counter")
	return mine[0] if len(mine) == 1 else None


@frappe.whitelist()
def my_counter() -> str | None:
	return default_counter()
