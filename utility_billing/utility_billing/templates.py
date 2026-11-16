# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""New society from a template (FS-CFG-27): a Pakistan housing society starts with its categories, charges, fee schedule (switched off
until the society sets the amounts), reasons, flags, the standard service requests and sensible settings. Tariffs, the chart of accounts
and bank details are the society's own and are listed as what is left to do. GCC compound templates belong to the country packages
(release 3)."""

import frappe
from frappe import _
from utility_billing.utility_billing import rights

TEMPLATES = ("Pakistan housing society",)
CATEGORIES = ("Residential", "Commercial", "Common Area", "Temporary / Construction")
CHARGES = (  # name, group, basis, order, label
	("Energy Charges", "Electricity", "Per Unit", 10, "Energy Charges"),
	("Fuel Price Adjustment", "Electricity", "Per Unit", 20, "FPA"),
	("Quarterly Tariff Adjustment", "Electricity", "Per Unit", 25, "QTA"),
	("Fixed Charges", "Electricity", "Fixed", 30, "Fixed Charges"),
	("Maintenance Charges", "Society", "Fixed", 40, "Maintenance Charges"),
)
FEES = ("Connection fee", "Reconnection fee", "Duplicate bill", "Meter testing fee", "Meter cost")
CREATORS = {"System Manager", "Society Admin"}


def ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
	return name


@frappe.whitelist(methods=["POST"])
def new_society(society_name: str, short_name: str, template: str = TEMPLATES[0]):
	"""Make a society from the template and return it with the setup still to do."""
	if not rights.can("templates.manage"):
		raise frappe.PermissionError(_("Only a Society Admin sets up a new society."))
	if template not in TEMPLATES:
		frappe.throw(_("Choose a template: {0}.").format(", ".join(TEMPLATES)))
	from utility_billing.install import create_default_reason_codes, create_default_utilities  # noqa: PLC0415
	from utility_billing.utility_billing import flags, servicereq  # noqa: PLC0415

	create_default_utilities()
	create_default_reason_codes()
	flags.create_default_flags()
	for name, group, basis, order, label in CHARGES:
		ensure("Charge Type", name, {"charge_name": name, "charge_group": group, "calculation_basis": basis, "bill_order": order, "bill_label": label})
	for cat in CATEGORIES:
		ensure("Consumer Category", f"Electricity-{cat}", {"category_name": cat, "utility": "Electricity"})
	soc = frappe.get_doc({"doctype": "Society", "society_name": society_name.strip(), "short_name": short_name.strip().upper(),
	                      "energy_charge_type": "Energy Charges", "charges_fpa": 1, "surcharge_rate": 10, "days_to_due_date": 14})
	soc.insert(ignore_permissions=True)
	fees = {}
	for fee in FEES:
		fees[fee] = ensure("Fee Type", f"{soc.name}-{fee}", {"fee_name": fee, "society": soc.name, "amount": 0, "enabled": 0,
		                                                     "description": _("Set the amount and switch it on.")})
	soc.reload()
	soc.duplicate_bill_fee = fees["Duplicate bill"]
	soc.reconnection_fee_type = fees["Reconnection fee"]
	soc.save(ignore_permissions=True)
	servicereq.ensure_types(soc.name)
	for code, fee in (("NEW", "Connection fee"), ("TEMP", "Connection fee"), ("TEST", "Meter testing fee"), ("METER", "Meter cost")):
		frappe.db.set_value("Service Request Type", f"{soc.name}-{code}", "fee_type", fees[fee])
	return {"society": soc.name, "to_do": [
		_("Approve a tariff for each category ({0}).").format(", ".join(CATEGORIES)),
		_("Set the fee amounts and switch the fees on ({0}).").format(", ".join(FEES)),
		_("Enter the chart of accounts and map it on the Society (receivables, income, payments, deposits, write-off)."),
		_("Add the bank accounts and biller codes, cash counters and messaging providers."),
		_("Enter or import properties, consumers, meters and connections, and give users their access."),
	]}
