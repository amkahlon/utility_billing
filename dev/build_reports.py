"""Developer tool (not part of the installed app). Creates the Script Reports from the registry in
utility_billing/utility_billing/mis.py, writes their .py/.js files, and rebuilds the module sidebar.
Run it after adding or changing a report, from ~/frappe-bench/sites on the dev site (developer mode):
    ../env/bin/python ../apps/utility_billing/dev/build_reports.py
It also creates the KPI Definition DocType if the site doesn't have it yet."""
import json
import re
from pathlib import Path

import frappe

MODULE = "Utility Billing"
APP = Path.home() / "frappe-bench/apps/utility_billing/utility_billing/utility_billing"
ROLES = ["Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Meter Reader",
         "Cashier", "Recovery Officer", "Accounts Officer", "Management", "Billing Auditor"]
NONE = dict(read=0, write=0, create=0, delete=0, submit=0, cancel=0, amend=0, report=0, export=0,
            print=0, email=0, share=0, **{"import": 0})
EDIT = dict(NONE, read=1, write=1, create=1, report=1, export=1, print=1)
READ = dict(NONE, read=1, report=1, export=1, print=1)
SM = dict(NONE, read=1, write=1, create=1, delete=1, report=1, export=1, print=1, email=1, share=1)


def perms(editors):
	return [dict(SM, role="System Manager")] + [dict(EDIT if r in editors else READ, role=r) for r in ROLES]


def f(fieldname, fieldtype, label=None, **kw):
	d = {"fieldname": fieldname, "fieldtype": fieldtype}
	if label is not None:
		d["label"] = label
	d.update(kw)
	return d


KPI = {"name": "KPI Definition", "autoname": "field:kpi_code", "track_changes": 1, "title_field": "kpi_name",
       "show_title_field_in_link": 1, "search_fields": "kpi_name,area", "permissions": perms({"Society Admin", "Billing Manager"}),
       "fields": [
	       f("kpi_code", "Data", "Code", reqd=1, unique=1, in_list_view=1),
	       f("kpi_name", "Data", "KPI", reqd=1, in_list_view=1),
	       f("cb1", "Column Break"),
	       f("area", "Select", "Area", options="Billing\nCollections\nReceivables\nReadings\nControl", reqd=1, in_list_view=1,
	         in_standard_filter=1),
	       f("refresh", "Select", "Refresh", options="Live\nDaily", default="Live", in_list_view=1),
	       f("definition_section", "Section Break", "Definition"),
	       f("definition", "Small Text", "What It Means", reqd=1),
	       f("formula", "Small Text", "Formula", reqd=1),
	       f("cb2", "Column Break"),
	       f("data_source", "Small Text", "Data Source", reqd=1),
	       f("dimensions", "Data", "Can Be Split By"),
	       f("drill_section", "Section Break", "Drill-down"),
	       f("drill_down", "Small Text", "Drill-down Path"),
	       f("report", "Link", "Report", options="Report"),
       ]}


def build_kpi():
	if frappe.db.exists("DocType", "KPI Definition"):
		print("exists: KPI Definition")
		return
	doc = frappe.get_doc({"doctype": "DocType", "module": MODULE, "custom": 0, **KPI})
	doc.allow_import = 1
	doc.insert()
	print("created: KPI Definition")


def js_of(spec):
	filters = json.dumps(spec["filters"], indent=1)
	tokens = {"@today": "frappe.datetime.get_today()", "@month_start": "frappe.datetime.month_start()",
	          "@year_start": "frappe.datetime.year_start()"}
	for k, v in tokens.items():
		filters = filters.replace(json.dumps(k), v)
	return ("// Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.\n"
	        "// Generated from utility_billing/utility_billing/mis.py by build_mis.py; edit the filters there.\n\n"
	        f'frappe.query_reports["{spec["name"]}"] = {{\n\tfilters: {filters},\n}};\n')


def py_of(spec):
	return ("# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.\n"
	        f'"""{spec["name"]}: see mis.py."""\n\n'
	        "from utility_billing.utility_billing import mis\n\n\n"
	        "def execute(filters=None):\n"
	        f'\treturn mis.run("{spec["key"]}", filters)\n')


def build_reports():
	from utility_billing.utility_billing import mis

	for spec in mis.REPORTS.values():
		roles = [{"role": r} for r in spec["roles"]]
		if frappe.db.exists("Report", spec["name"]):
			rep = frappe.get_doc("Report", spec["name"])
			rep.set("roles", roles)
			rep.ref_doctype = spec["ref_doctype"]
			rep.save()
			print("updated:", spec["name"])
		else:
			frappe.get_doc({"doctype": "Report", "report_name": spec["name"], "ref_doctype": spec["ref_doctype"],
			                "report_type": "Script Report", "is_standard": "Yes", "module": MODULE, "roles": roles}).insert()
			print("created:", spec["name"])
		folder = APP / "report" / frappe.scrub(spec["name"])
		folder.mkdir(parents=True, exist_ok=True)
		(folder / "__init__.py").touch()
		(folder / f"{frappe.scrub(spec['name'])}.py").write_text(py_of(spec), encoding="utf-8")
		(folder / f"{frappe.scrub(spec['name'])}.js").write_text(js_of(spec), encoding="utf-8")


GROUPS = [
	("Approvals", [("Page", "ub-approvals", "Approval Inbox"), ("DocType", "Approval Delegation"), ("DocType", "User Access")]),
	("Consumers", [("Page", "ub-consumer-360", "Consumer 360"), ("DocType", "Consumer"), ("DocType", "Property"), ("DocType", "Connection"),
	               ("DocType", "Security Deposit")]),
	("Meter Readings", [("DocType", "Reading Cycle"), ("DocType", "Meter Reading"), ("DocType", "Reading Route")]),
	("Billing", [("DocType", "Bill Run"), ("DocType", "Utility Bill"), ("DocType", "Tariff Adjustment")]),
	("Payments", [("DocType", "Cashier Day"), ("DocType", "Payment Receipt"), ("DocType", "Fee Charge"),
	              ("DocType", "Account Adjustment"), ("DocType", "Refund"), ("DocType", "Settlement File"),
	              ("DocType", "Suspense Payment"), ("DocType", "Customer Ledger Entry")]),
	("Messages", [("DocType", "Message Log"), ("DocType", "Broadcast"), ("DocType", "Notification Template"), ("DocType", "Messaging Provider")]),
	("Service Requests", [("DocType", "Service Request"), ("DocType", "Service Request Type")]),
	("Corrections", [("DocType", "Bill Revision"), ("DocType", "Due Date Extension"), ("DocType", "Bill Dispute"), ("DocType", "Bulk Adjustment"),
	               ("DocType", "Installment Plan"), ("DocType", "Write Off")]),
	("Recovery", [("Page", "ub-recovery", "Defaulters and Recovery"), ("DocType", "Recovery Case"), ("DocType", "Recovery Run"),
	              ("DocType", "Recovery Notice"), ("DocType", "Recovery Target")]),
	("Period Control", [("DocType", "Billing Period"), ("DocType", "Year End Close"), ("DocType", "Run Snapshot")]),
	("Accounting", [("DocType", "GL Journal"), ("DocType", "Receivables Reconciliation"), ("DocType", "Ledger Account"),
	                ("DocType", "Journal Export Layout"), ("DocType", "Revenue Target")]),
	("Meters and Network", [("DocType", "Meter"), ("DocType", "Grid Station"), ("DocType", "Feeder"), ("DocType", "Line Section"),
	                        ("DocType", "Transformer"), ("DocType", "Common Area Facility"), ("DocType", "Network Asset"), ("DocType", "DISCO Bill")]),
	("Reports", []),  # filled from the registry (scheduled report e-mails, FS-RPT-44, are parked by the user)
	("Setup", [("DocType", "Society"), ("DocType", "Tariff"), ("DocType", "Charge Type"), ("DocType", "Tax"), ("DocType", "Consumer Category"),
	           ("DocType", "Fee Type"), ("DocType", "Cash Counter"), ("DocType", "Reason Code"),
	           ("DocType", "Reading Exception Code"), ("DocType", "Connection Flag Type"), ("DocType", "Utility Type"), ("DocType", "KPI Definition")]),
]


def build_sidebar():
	from utility_billing.utility_billing import mis

	doc = frappe.get_doc("Sidebar", "Utility Management System")  # named by its title: renaming it would change every desk address
	doc.items = []
	doc.append("items", {"type": "Link", "label": "Dashboard", "link_type": "Page", "link_to": "ub-dashboard", "child": 0})
	for section, links in GROUPS:
		if section == "Reports":
			links = [("Report", s["name"]) for s in mis.REPORTS.values()]
		doc.append("items", {"type": "Section Break", "label": section, "collapsible": 1})
		for link_type, name, *label in links:
			doc.append("items", {"type": "Link", "label": label[0] if label else name, "link_type": link_type, "link_to": name, "child": 1})
	for n, i in enumerate(doc.items, start=1):
		i.idx = n
	doc.save()
	print("sidebar sections:", [s for s, _l in GROUPS], "items:", len(doc.items))


frappe.init(site="ub.localhost", sites_path=".")
frappe.connect()
build_kpi()
frappe.db.commit()
build_reports()
build_sidebar()
frappe.db.commit()
frappe.destroy()
