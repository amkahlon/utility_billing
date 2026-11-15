# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society onboarding checklist (FS-CLT-10, gap 4): what has to be in place before a society goes live.

The checks below are worked out from the society's setup each time the checklist is opened; the tasks only people can confirm (the
taxes agreed, staff trained, sample bill approved...) are ticked by hand on the Society Onboarding record, which keeps who and when."""

import frappe
from frappe import _
from frappe.utils import cint

DEFAULT_TASKS = (
	"Taxes that apply agreed with the society (per utility)",
	"Tariffs and charges checked against the society's approved schedule",
	"Sample bill approved by the society",
	"Opening balances reconciled with the previous system's totals",
	"Payment channels (banks, 1Link, Kuickpay) signed up and tested",
	"Second-language bill labels reviewed by a native speaker",
	"Staff trained (billing, cashiers, meter readers)",
)


def _check(area, label, done, detail="", route=None):
	return {"area": area, "check": label, "done": bool(done), "detail": detail, "route": route}


def status(society) -> list[dict]:
	s = frappe.get_doc("Society", society)
	count = lambda dt, **f: frappe.db.count(dt, {"society": society, **f})  # noqa: E731
	sform = ["Form", "Society", society]
	gaps = [label for value, label in ((s.society_name, _("name")), (s.address, _("address")), (s.phone, _("phone"))) if not value]
	out = [
		_check("Society", _("Name, address and phone"), not gaps, _("Missing: {0}").format(", ".join(gaps)) if gaps else "", sform),
		_check("Society", _("Logo"), s.logo, route=sform),
		_check("Society", _("Signatory for bills"), any(cint(r.for_bill) for r in s.get("signatories") or []), route=sform),
		_check("Society", _("Bank accounts printed on the bill"), any(cint(b.print_on_bill) for b in s.bank_accounts),
		       route=sform),
		_check("Society", _("Bill notices or payment instructions"), s.bill_notices or s.payment_instructions, route=sform),
	]
	tariffs = count("Tariff", docstatus=1)
	out.append(_check("Billing", _("Tariffs approved"), tariffs, _("{0} approved").format(tariffs), ["List", "Tariff"]))
	used = set(frappe.db.sql_list("""select distinct c.charge_type from `tabTariff Charge` c join `tabTariff` t on t.name = c.parent
	                                 where t.society = %s and t.docstatus = 1""", society))
	if s.energy_charge_type:
		used.add(s.energy_charge_type)
	unmapped = sorted(c for c in used if c and not frappe.db.get_value("Charge Type", c, "account_code"))
	out.append(_check("Accounting", _("Charge types mapped to income accounts"), used and not unmapped,
	                  _("Not mapped: {0}").format(", ".join(unmapped)) if unmapped else "", ["List", "Charge Type"]))
	missing = [label for field, label in (("receivables_account", _("receivables")), ("surcharge_income_account", _("surcharge income")),
	                                      ("other_income_account", _("other income")), ("opening_balance_account", _("opening balances")))
	           if not s.get(field)]
	out.append(_check("Accounting", _("Society accounts mapped"), not missing, _("Missing: {0}").format(", ".join(missing)) if missing else "", sform))
	counters = count("Cash Counter", enabled=1)
	out.append(_check("Payments", _("Cash counters"), counters, _("{0} enabled").format(counters), ["List", "Cash Counter"]))
	staff = frappe.db.sql("""select count(distinct u.name) from `tabUser Access` u left join `tabAccess Society` a on a.parent = u.name
	                         where u.all_societies = 1 or a.society = %s""", society)[0][0]
	out.append(_check("People", _("Staff given access to the society"), staff, _("{0} users").format(staff), ["List", "User Access"]))
	providers = frappe.db.count("Messaging Provider", {"enabled": 1, "society": ["in", [society, ""]]}) + frappe.db.count(
		"Messaging Provider", {"enabled": 1, "society": ["is", "not set"]})
	out.append(_check("Messages", _("A messaging provider is enabled"), providers, route=["List", "Messaging Provider"]))
	conns = count("Connection", status="Active")
	out.append(_check("Consumers", _("Consumers and connections entered"), conns, _("{0} active connections").format(conns), ["List", "Connection"]))
	routes = count("Reading Route")
	out.append(_check("Readings", _("Reading routes"), routes, (_("{0} route") if routes == 1 else _("{0} routes")).format(routes), ["List", "Reading Route"]))
	out.append(_check("Go-live", _("Opening balances posted"), s.get("go_live_date"),
	                  _("Live since {0}").format(frappe.utils.formatdate(s.go_live_date)) if s.get("go_live_date") else "",
	                  ["List", "Opening Balance Import"]))
	return out


@frappe.whitelist()
def checklist(society: str) -> dict:
	frappe.has_permission("Society Onboarding", "read", throw=True)  # found by e2e QA-81: a cashier could call it
	frappe.has_permission("Society", "read", society, throw=True)
	checks = status(society)
	return {"checks": checks, "done": sum(c["done"] for c in checks), "total": len(checks)}


@frappe.whitelist(methods=["POST"])
def open_for(society: str) -> str:
	"""The society's onboarding record, made with the standard tasks the first time."""
	frappe.has_permission("Society Onboarding", "read", throw=True)
	frappe.has_permission("Society", "read", society, throw=True)
	name = frappe.db.get_value("Society Onboarding", {"society": society})
	if name:
		return name
	doc = frappe.get_doc({"doctype": "Society Onboarding", "society": society, "tasks": [{"task": t} for t in DEFAULT_TASKS]})
	doc.insert()
	return doc.name
