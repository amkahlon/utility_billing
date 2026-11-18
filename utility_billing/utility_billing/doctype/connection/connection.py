# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Connection: one utility supply to a property, identified by its permanent consumer reference
number (FS-MST-04, 05, 07). Holds the meter installation history."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import flt, formatdate, getdate

from utility_billing.utility_billing.reference import make_reference

OPEN_END = getdate("9999-12-31")


class Connection(Document):
	def before_insert(self):
		# The ID is the reference number, so it must exist before naming (which runs before validate).
		if not self.reference_no:
			self.reference_no = self.new_reference_no()

	def validate_legacy_numbers(self):
		"""An old account or collection number from the previous system belongs to one account only (increment 29)."""
		for f in ("legacy_account_no", "legacy_collection_no"):
			v = (self.get(f) or "").strip() or None
			self.set(f, v)
			if v:
				other = frappe.db.sql("""select name from `tabConnection` where name != %(me)s and society = %(s)s
				                         and (legacy_account_no = %(v)s or legacy_collection_no = %(v)s) limit 1""",
				                      {"me": self.name or "", "s": self.society, "v": v})
				if other:
					frappe.throw(_("Old number {0} already belongs to account {1}.").format(v, other[0][0]))

	def new_reference_no(self) -> str:
		code = frappe.db.get_value("Utility Type", self.utility, "reference_code")
		if not code:
			frappe.throw(_("Set a Reference Code on Utility Type {0} first.").format(self.utility))
		serial = int(getseries(f"UB-REF-{int(code)}", 8))
		return make_reference(int(code), serial)

	def validate(self):
		self.validate_reference_unchanged()
		self.validate_legacy_numbers()
		self.validate_request_only()
		self.validate_links()
		self.validate_route()
		self.validate_meter_history()
		self.set_title()

	def on_update(self):
		self.sync_meters()
		self.sync_route()

	def sync_route(self):
		"""Choosing a route on the account puts it at the end of that Reading Route (and off the others of this utility), since the
		route's stops are what the reading sheet follows (bug report 2026-11-16: the route was typed, not chosen)."""
		before = self.get_doc_before_save()
		if (before.route if before else None) == self.route or self.flags.from_route:
			return
		from utility_billing.utility_billing.doctype.reading_route.reading_route import move_account  # noqa: PLC0415

		move_account(self, self.route)

	# ----- validation -----
	def validate_reference_unchanged(self):
		if self.is_new():
			return
		stored = frappe.db.get_value("Connection", self.name, "reference_no")
		if stored and self.reference_no != stored:
			frappe.throw(_("The reference number is permanent and can't be changed."))

	def validate_request_only(self):
		"""FS-MST-16: on an active account, category, load, holder and meters change only through a service request (society setting,
		on by default). The Administrator may still correct data; Frappe's version log keeps what changed."""
		if self.is_new() or frappe.flags.via_service_request or frappe.session.user == "Administrator":
			return
		before = self.get_doc_before_save()
		if not before or before.status != "Active":
			return
		if not frappe.utils.cint(frappe.db.get_value("Society", self.society, "changes_through_requests")):
			return
		changed = [label for field, label in (("consumer_category", _("category")), ("sanctioned_load_kw", _("load")), ("consumer", _("holder")))
		           if str(self.get(field) or "") != str(before.get(field) or "") and not (field == "sanctioned_load_kw"
		                                                                              and flt(self.get(field)) == flt(before.get(field)))]

		def meters(doc):
			return sorted((r.meter, str(r.installed_on or ""), str(r.removed_on or ""), flt(r.start_reading), flt(r.end_reading)) for r in doc.meters)

		if meters(self) != meters(before):
			changed.append(_("meters"))
		if self.status != before.status:
			changed.append(_("status"))
		if changed:
			frappe.throw(_("The {0} of an active account change only through a service request (category change, load change, ownership or "
			               "tenant, meter change, disconnection …). Raise one from the account.").format(", ".join(changed)),
			             title=_("Use a service request"))

	def validate_route(self):
		if self.route and not frappe.db.exists("Reading Route", {"society": self.society, "utility": self.utility, "route_code": self.route}):
			frappe.throw(_("There is no reading route {0} for {1} {2}. Choose one from the list, or create the route first.").format(
				self.route, self.society, self.utility))

	def validate_links(self):
		cat_utility = frappe.db.get_value("Consumer Category", self.consumer_category, "utility")
		if cat_utility and cat_utility != self.utility:
			frappe.throw(
				_("Consumer Category {0} belongs to {1}, not {2}.").format(
					self.consumer_category, cat_utility, self.utility
				)
			)
		prop_society = frappe.db.get_value("Property", self.property, "society")
		if prop_society and prop_society != self.society:
			frappe.throw(_("Property {0} belongs to society {1}.").format(self.property, prop_society))
		if self.transformer:
			tx_society = frappe.db.get_value("Transformer", self.transformer, "society")
			if tx_society != self.society:
				frappe.throw(_("Transformer {0} belongs to society {1}.").format(self.transformer, tx_society))
		if flt(self.sanctioned_load_kw) < 0 or flt(self.security_deposit) < 0:
			frappe.throw(_("Load and security deposit can't be negative."))

	def validate_meter_history(self):
		rows = sorted(self.meters, key=lambda r: getdate(r.installed_on))
		for i, r in enumerate(rows):
			meter = frappe.db.get_value("Meter", r.meter, ["society", "utility"], as_dict=True)
			if meter and (meter.society != self.society or meter.utility != self.utility):
				frappe.throw(
					_("Meter {0} is a {1} meter of society {2}.").format(r.meter, meter.utility, meter.society)
				)
			if r.removed_on and getdate(r.removed_on) < getdate(r.installed_on):
				frappe.throw(_("Meter {0}: Removed On is before Installed On.").format(r.meter))
			if r.removed_on and r.end_reading is not None and flt(r.end_reading) < flt(r.start_reading) and not (
				r.removal_reason
			):
				frappe.throw(
					_("Meter {0}: End Reading is below Start Reading. Give a Removal Reason (for example roll-over).").format(
						r.meter
					)
				)
			if r.removed_on and flt(r.get("end_export_reading")) and flt(r.end_export_reading) < flt(r.get("start_export_reading")):
				frappe.throw(_("Meter {0}: End Export Reading is below Start Export Reading.").format(r.meter))
			if not r.removed_on and i != len(rows) - 1:
				frappe.throw(_("Meter {0} is still open. Only the latest meter can be without Removed On.").format(r.meter))
			if i > 0 and rows[i - 1].removed_on and getdate(r.installed_on) < getdate(rows[i - 1].removed_on):
				frappe.throw(
					_("Meter {0} is installed on {1}, before meter {2} was removed.").format(
						r.meter, formatdate(r.installed_on), rows[i - 1].meter
					)
				)
		open_rows = [r for r in rows if not r.removed_on]
		self.current_meter = open_rows[0].meter if open_rows else None
		if self.current_meter:
			from utility_billing.utility_billing.doctype.meter_store_movement.meter_store_movement import check_installable  # noqa: PLC0415

			check_installable(self.current_meter)
			elsewhere = frappe.db.get_value(
				"Connection Meter",
				{"meter": self.current_meter, "removed_on": ["is", "not set"], "parent": ["!=", self.name]},
				"parent",
			)
			if elsewhere:
				frappe.throw(_("Meter {0} is already installed on connection {1}.").format(self.current_meter, elsewhere))
			if frappe.db.get_value("Meter", self.current_meter, "status") == "Defective":
				frappe.msgprint(_("Meter {0} is marked Defective.").format(self.current_meter), alert=True)
		is_metered = frappe.db.get_value("Utility Type", self.utility, "is_metered")
		if self.status == "Active" and is_metered and not self.current_meter:
			frappe.throw(_("An active {0} connection needs an installed meter.").format(self.utility))

	def set_title(self):
		prop_no = frappe.db.get_value("Property", self.property, "property_no") or self.property
		self.connection_title = f"{prop_no} · {self.utility}"

	# ----- side effects -----
	def sync_meters(self):
		"""Keep each meter's status and current connection in step with this history."""
		for r in self.meters:
			current = frappe.db.get_value("Meter", r.meter, ["status", "current_connection"], as_dict=True)
			if not current:
				continue
			if not r.removed_on:
				if current.current_connection != self.name or current.status == "In Store":
					status = "Defective" if current.status == "Defective" else "Installed"
					frappe.db.set_value("Meter", r.meter, {"current_connection": self.name, "status": status, "issued_to": None})
			elif current.current_connection == self.name:
				status = "Defective" if current.status == "Defective" else "Removed"
				frappe.db.set_value("Meter", r.meter, {"current_connection": None, "status": status})
		# A meter deleted from the history altogether is released too.
		listed = {r.meter for r in self.meters}
		for m in frappe.get_all("Meter", filters={"current_connection": self.name}, pluck="name"):
			if m not in listed:
				frappe.db.set_value("Meter", m, {"current_connection": None, "status": "In Store"})
