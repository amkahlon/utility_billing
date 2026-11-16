# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society isolation for every request (review 2026-11-16, finding 1).

A user limited to some societies (User Access, kept as User Permissions on Society) is held to them by Frappe in lists and forms,
but the app's reports, pages and searches query the tables directly and used to trust the society the browser sent. This check
runs before each request: a call to one of the app's methods, or to any report, that names a society outside the user's own is
refused. Pages and reports also default to and list only the user's societies (access.visible_societies, mis.society_of)."""

import frappe

PREFIXES = ("/api/method/", "/api/v1/method/", "/api/v2/method/")
CHECKED = ("utility_billing.", "frappe.desk.query_report.")


def command() -> str | None:
	path = getattr(frappe.local.request, "path", "") or ""
	for prefix in PREFIXES:
		if path.startswith(prefix):
			return path[len(prefix):]
	return frappe.form_dict.get("cmd")


def _filters() -> dict:
	f = frappe.form_dict.get("filters")
	if isinstance(f, str):
		try:
			f = frappe.parse_json(f)
		except Exception:  # noqa: BLE001 - not JSON: the method itself will refuse it
			return {}
	return f if isinstance(f, dict) else {}


def societies_named() -> list[str]:
	out = []
	for value in (frappe.form_dict.get("society"), _filters().get("society")):
		if isinstance(value, str) and value.startswith("["):
			value = frappe.parse_json(value)
		if isinstance(value, (list, tuple)):
			out += [v for v in value if isinstance(v, str)]
		elif isinstance(value, str) and value:
			out.append(value)
	return out


def guard():
	if not getattr(frappe.local, "request", None) or frappe.session.user in ("Guest", "Administrator"):
		return  # guest endpoints check their own token
	cmd = command()
	if not cmd or not cmd.startswith(CHECKED):
		return
	named = societies_named()
	if named:
		from utility_billing.utility_billing import access  # noqa: PLC0415

		access.check_society(named)
