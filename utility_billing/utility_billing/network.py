# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Network master data (FS-MST-23 to 27): grid stations, line sections, common-area facilities, the asset register and map locations.

Locations are plain latitude and longitude on each record; `geojson` gives every located record of a society as a GeoJSON
FeatureCollection that a GIS (QGIS, ArcGIS, Google My Maps) can import. Common-area costs are shared by the method each facility
names; `allocation` works the shares out for a month from the facility's own bill (bills don't carry them yet: that belongs to the
billing-flexibility work)."""

import frappe
from frappe import _
from frappe.utils import flt, get_last_day, getdate

from utility_billing.utility_billing.readings import month_start
from utility_billing.utility_billing import rights

LOCATED = (  # doctype, label field, kind shown on the map
	("Property", "property_no", "Property"),
	("Grid Station", "station_name", "Grid station"),
	("Feeder", "feeder_name", "Feeder"),
	("Transformer", "transformer_code", "Transformer"),
	("Common Area Facility", "facility_name", "Common area"),
	("Network Asset", "asset_name", "Asset"),
)
VIEWERS = {"System Manager", "Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Management", "Billing Auditor", "Accounts Officer"}


def check_location(doc):
	"""Latitude and longitude come together and within range."""
	lat, lng = doc.get("latitude"), doc.get("longitude")
	if not lat and not lng:
		return
	if not (lat and lng):
		frappe.throw(_("Enter both latitude and longitude, or neither."))
	if not (-90 <= flt(lat) <= 90 and -180 <= flt(lng) <= 180):
		frappe.throw(_("Latitude must be between -90 and 90 and longitude between -180 and 180 (decimal degrees)."))


def check_society(doc, link_doctype, link_name, what):
	if link_name and frappe.db.get_value(link_doctype, link_name, "society") != doc.society:
		frappe.throw(_("{0} {1} belongs to another society.").format(_(what), link_name))


@frappe.whitelist()
def geojson(society: str):
	"""Every record of the society with a map location, as GeoJSON (FS-MST-27)."""
	if not rights.can("network.view"):
		raise frappe.PermissionError(_("You can't export the network map."))
	features = []
	for dt, label, kind in LOCATED:
		for r in frappe.get_all(dt, filters={"society": society, "latitude": ["!=", 0], "longitude": ["!=", 0]},
		                        fields=["name", label, "latitude", "longitude"]):
			features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [flt(r.longitude), flt(r.latitude)]},
			                 "properties": {"kind": kind, "doctype": dt, "name": r.name, "label": r.get(label) or r.name}})
	return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------- common areas (FS-MST-25)
SOCIETY, EQUAL, AREA, UNITS, CATEGORY = ("Society bears it", "Shared equally by all accounts", "Shared by area", "Shared by units used",
                                         "Charged to one category")


def facility_cost(facility, month):
	"""(units, amount) of the facility's own bill for the month, or (0, 0) when it has none."""
	if not facility.connection:
		return 0.0, 0.0
	b = frappe.db.get_value("Utility Bill", {"connection": facility.connection, "billing_month": month_start(month), "docstatus": 1},
	                        ["units", "current_amount"], as_dict=True)
	return (flt(b.units), flt(b.current_amount)) if b else (0.0, 0.0)


def sharers(society, month, method, category=None, exclude=()):
	"""The accounts that share a cost and the basis of each share: {connection: weight}."""
	month = month_start(month)
	rows = frappe.db.sql(
		"""select b.connection, b.units, b.consumer_category, ifnull(p.area, 0) area from `tabUtility Bill` b
		left join `tabProperty` p on p.name = b.property where b.society = %s and b.billing_month = %s and b.docstatus = 1""",
		(society, month), as_dict=True)
	out = {}
	for r in rows:
		if r.connection in exclude:
			continue
		if method == CATEGORY and r.consumer_category != category:
			continue
		out[r.connection] = {EQUAL: 1.0, CATEGORY: 1.0, AREA: flt(r.area), UNITS: flt(r.units)}.get(method, 0.0)
	return out


def allocation(society, month) -> list[dict]:
	"""For each facility in use: its units and cost in the month, how it is shared, over how many accounts and the share per account
	(per unit of area or of units for those methods)."""
	facilities = frappe.get_all("Common Area Facility", filters={"society": society, "enabled": 1}, fields=["*"], order_by="facility_name")
	own = {f.connection for f in facilities if f.connection}
	out = []
	for fac in facilities:
		units, amount = facility_cost(fac, month)
		row = {"facility": fac.name, "facility_name": fac.facility_name, "kind": fac.facility_type, "method": fac.allocation_method,
		       "units": units, "amount": amount, "accounts": 0, "basis": 0.0, "per_account": 0.0, "rate": 0.0}
		if fac.allocation_method != SOCIETY and amount:
			shares = sharers(society, month, fac.allocation_method, fac.allocation_category, exclude=own)
			basis = sum(shares.values())
			row.update(accounts=len(shares), basis=basis)
			if basis:
				row["rate"] = round(amount / basis, 4)
				row["per_account"] = round(amount / len(shares), 2) if shares else 0
		out.append(row)
	return out


# ---------------------------------------------------------------- assets (FS-MST-26)
def next_maintenance(doc):
	due = [getdate(r.next_due) for r in doc.maintenance if r.next_due]
	return max(due) if due else None


def maintenance_status(next_due, warranty_until, as_of):
	as_of = getdate(as_of)
	bits = []
	if next_due:
		bits.append(_("Overdue") if getdate(next_due) < as_of else _("Due soon") if (getdate(next_due) - as_of).days <= 30 else _("Planned"))
	if warranty_until:
		bits.append(_("In warranty") if getdate(warranty_until) >= as_of else _("Warranty over"))
	return ", ".join(bits)


def month_end(month):
	return get_last_day(month_start(month))
