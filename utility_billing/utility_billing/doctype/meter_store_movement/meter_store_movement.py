# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter Store Movement (gap 13, increment 23): meters received into the store, issued to an installer, returned to the store and
scrapped. Submitting moves the meters; installing one on an account (through a service request) takes it from the store or from
the installer it was issued to. Cancelling puts the meters back as they were, as long as none has moved on since."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

RECEIVED, ISSUED, RETURNED, SCRAPPED = "Received", "Issued", "Returned", "Scrapped"
IN_STORE = "In Store"
# the statuses a meter may have before each movement
FROM = {ISSUED: (IN_STORE,), RETURNED: ("Issued", "Removed", "Defective"), SCRAPPED: (IN_STORE, "Removed", "Defective")}


class MeterStoreMovement(Document):
	def validate(self):
		if not self.items:
			frappe.throw(_("List the meters."))
		seen = set()
		for r in self.items:
			key = r.meter or (r.serial_no or "").strip().upper()
			if not key:
				frappe.throw(_("Row {0}: choose the meter, or for meters received, enter the serial number.").format(r.idx))
			if key in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(r.idx, key))
			seen.add(key)
			if self.movement_type == RECEIVED:
				if r.meter:
					frappe.throw(_("Row {0}: a meter received is new to the store; enter its serial number instead.").format(r.idx))
				r.serial_no = (r.serial_no or "").strip()
				r.digits, r.multiplier = cint(r.digits) or 5, flt(r.multiplier) or 1
				continue
			if not r.meter:
				frappe.throw(_("Row {0}: choose the meter.").format(r.idx))
			m = frappe.db.get_value("Meter", r.meter, ["society", "utility", "status", "serial_no", "make", "meter_type"], as_dict=True)
			if m.society != self.society or m.utility != self.utility:
				frappe.throw(_("Row {0}: meter {1} is a {2} meter of {3}.").format(r.idx, r.meter, m.utility, m.society))
			if self.docstatus == 0 and m.status not in FROM[self.movement_type]:
				frappe.throw(_("Row {0}: meter {1} is {2}; it can't be {3} (only a meter that is {4}).").format(
					r.idx, r.meter, _(m.status), _(self.movement_type).lower(), " / ".join(_(s) for s in FROM[self.movement_type])))
			r.serial_no, r.make, r.meter_type = m.serial_no, m.make, m.meter_type
			r.status_before = m.status
		self.meter_count = len(self.items)

	def on_submit(self):
		for r in self.items:
			if self.movement_type == RECEIVED:
				meter = frappe.get_doc({"doctype": "Meter", "serial_no": r.serial_no, "society": self.society, "utility": self.utility,
				                        "make": r.make, "meter_type": r.meter_type or "Single Phase", "digits": r.digits,
				                        "multiplier": r.multiplier, "status": IN_STORE})
				meter.insert(ignore_permissions=True)  # the duplicate-number check applies (increment 21)
				r.db_set({"meter": meter.name, "status_before": "", "status_after": IN_STORE})
				frappe.db.set_value("Meter", meter.name, "last_movement", self.name, update_modified=False)
				continue
			if frappe.db.get_value("Meter", r.meter, "status") not in FROM[self.movement_type]:
				frappe.throw(_("Meter {0} moved since this was drafted; check it again.").format(r.meter))
			after = {ISSUED: "Issued", RETURNED: "Defective" if r.condition == "Defective" else IN_STORE, SCRAPPED: "Scrapped"}[self.movement_type]
			frappe.db.set_value("Meter", r.meter, {"status": after, "issued_to": self.issued_to if after == "Issued" else None,
			                                       "last_movement": self.name})
			r.db_set("status_after", after)

	def before_cancel(self):
		for r in self.items:
			now = frappe.db.get_value("Meter", r.meter, ["status", "last_movement"], as_dict=True) if r.meter else None
			if not now:
				continue
			if now.last_movement != self.name or now.status != r.status_after:
				frappe.throw(_("Meter {0} has moved on since ({1}); cancel the later movement first.").format(r.meter, _(now.status)))

	def on_cancel(self):
		for r in self.items:
			if not r.meter:
				continue
			if self.movement_type == RECEIVED:
				frappe.delete_doc("Meter", r.meter, ignore_permissions=True, force=True)
				continue
			frappe.db.set_value("Meter", r.meter, {"status": r.status_before, "issued_to": None, "last_movement": None})


def check_installable(meter):
	"""A scrapped meter can't be installed again (increment 23)."""
	if frappe.db.get_value("Meter", meter, "status") == "Scrapped":
		frappe.throw(_("Meter {0} is scrapped and can't be installed.").format(meter))
