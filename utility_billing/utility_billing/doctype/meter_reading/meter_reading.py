# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter Reading: one connection's reading in one reading cycle (FS-RDG-04..11, 15).

Status: Pending (not read yet) -> Valid (checks passed) or Flagged (needs review) -> Approved
(accepted by a reviewer) | Re-read Requested | Estimated (unread, estimated at the estimate step).
A reading is re-checked only when its inputs change, so a reviewer's decision isn't undone by an
unrelated save."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from utility_billing.utility_billing import defective, readcache, readings
from utility_billing.utility_billing import reading_engine as engine

INPUTS = ("present_reading", "reading_date", "exception_code", "present_export_reading")
NUMBERS = ("present_reading", "present_export_reading")
READ_BY_HAND = ("present_reading", "present_export_reading", "reading_date", "mdi_kw")  # what a reader or the MDM gives


class MeterReading(Document):
	def validate(self):
		cycle = readcache.cycle_info(self.reading_cycle)  # a cycle's bulk work has it at hand
		if cycle.status == "Closed":
			frappe.throw(_("Reading cycle {0} is closed; its readings can't change.").format(self.reading_cycle))
		self.society, self.billing_month = cycle.society, cycle.billing_month
		self.check_connection(cycle)
		if not self.flags.in_bulk:  # bulk work takes each account's one reading of the cycle
			self.check_unique()
		if self.is_new() or not self.meter:
			self.load_context()
		self.check_smart_meter()
		self.evaluate()
		self.check_mdi()

	def check_smart_meter(self):
		"""A smart (AMI) meter's reading comes from the MDM and isn't typed, uploaded or corrected by hand (increment 47; user,
		2026-11-24: "meter reading fields should be editable only other than AMI/smart meters"). The exception: "in case if a smart
		meter is mute, then its reading will be captured by meter reader", marked Not Communicating and sent to review."""
		info = (readcache.meter_info(self.meter) if self.meter else None) or {}
		self.smart_meter = cint(info.get("smart_meter"))
		from_mdm = bool(self.flags.from_mdm)
		if not self.smart_meter:
			self.smart_meter_mute = 0
			if from_mdm:
				frappe.throw(_("Meter {0} isn't a smart meter: a reading from the MDM isn't taken for it.").format(self.meter))
			return
		if from_mdm:
			self.source, self.smart_meter_mute = "MDM", 0
			return
		if self.flags.estimated or self.flags.request_reread:
			return
		before = self.get_doc_before_save()
		if before:
			given = any((flt(before.get(f)) != flt(self.get(f))) if f != "reading_date" else (str(before.get(f) or "") != str(self.get(f) or ""))
			            for f in READ_BY_HAND)
		else:
			given = any(self.get(f) for f in READ_BY_HAND)
		if not given:
			return
		if not cint(self.smart_meter_mute):
			frappe.throw(_("Meter {0} is a smart meter: its reading comes from the MDM. If it isn't communicating, tick Not Communicating and enter the "
			               "meter reader's reading.").format(self.meter))
		if self.source == "MDM":
			self.source = "Office"

	def check_dial(self):
		"""A reading the meter's dial can't show is refused: it is a slip of the hand, or the meter's Dial Digits are wrong."""
		digits = cint(self.digits) or engine.DEFAULT_DIGITS
		for label, value in ((_("Present Reading"), self.present_reading), (_("Export Reading"), self.present_export_reading)):
			if flt(value) >= 10 ** digits:
				frappe.throw(_("{0} {1} has more digits than the dial of meter {2} ({3} digits). Check the reading, or correct Dial Digits on the meter.").format(
					label, f"{flt(value):g}", self.meter, digits))

	def check_mdi(self):
		"""The maximum demand (MDI) read from the meter: never negative; above the sanctioned load it is noted for a load extension."""
		if flt(self.mdi_kw) < 0:
			frappe.throw(_("The maximum demand can't be negative."))
		sanctioned = flt((readcache.connection_info(self.connection) or {}).get("sanctioned_load_kw"))
		prefix = _("Maximum demand")
		lines = [x for x in (self.checks or "").split("\n") if x and not x.startswith(prefix)]  # an earlier note is replaced
		if flt(self.mdi_kw) and sanctioned and flt(self.mdi_kw) > sanctioned:
			lines.append(_("{0} {1} kW is above the sanctioned load of {2} kW.").format(prefix, flt(self.mdi_kw), sanctioned))
		self.checks = "\n".join(lines) or None

	def on_update(self):
		"""Keep the connection's connected load (last MDI read) up to date."""
		if flt(self.mdi_kw) and self.reading_date:
			last = (readcache.connection_info(self.connection) or {}).get("last_mdi_date")
			if not last or getdate(self.reading_date) >= getdate(last):
				frappe.db.set_value("Connection", self.connection, {"last_mdi_kw": flt(self.mdi_kw), "last_mdi_date": self.reading_date},
				                    update_modified=False)

	def check_connection(self, cycle):
		conn = readcache.connection_info(self.connection)
		if not conn or conn.society != cycle.society or conn.utility != cycle.utility:
			frappe.throw(_("Connection {0} isn't a {1} connection of {2}.").format(self.connection, cycle.utility, cycle.society))

	def check_unique(self):
		other = frappe.db.get_value(
			"Meter Reading", {"reading_cycle": self.reading_cycle, "connection": self.connection, "name": ["!=", self.name]}
		)
		if other:
			frappe.throw(_("Connection {0} already has reading {1} in this cycle.").format(self.connection, other))

	def load_context(self):
		ctx = readings.reading_context(self.connection, self.billing_month)
		for key, value in ctx.items():
			self.set(key, value)

	def inputs_changed(self):
		before = self.get_doc_before_save()
		if not before:
			return True
		return any(
			(flt(before.get(f)) != flt(self.get(f))) if f in NUMBERS else (before.get(f) != self.get(f))
			for f in INPUTS
		)

	# ----- evaluation -----
	def evaluate(self):
		if self.flags.request_reread:
			self.status = "Re-read Requested"
			return
		if self.flags.estimated:
			return
		changed = self.inputs_changed()
		if not changed and self.status in ("Approved", "Estimated", "Re-read Requested") and not self.flags.approve_as_is:
			return  # a reviewer's decision or an estimate stays until the reading itself changes
		if self.exception_code:
			self.reset_result()
			self.status = "Pending"
			self.checks = _("Not read: {0}. It will be estimated at the estimate step.").format(self.exception_code)
			return
		if not self.reading_date:
			self.reset_result()
			self.status = "Pending"
			return
		if getdate(self.reading_date) < getdate(self.previous_reading_date or self.reading_date):
			frappe.throw(_("Reading Date can't be before the previous reading date."))
		self.check_dial()
		if not changed and self.status == "Flagged" and not self.flags.approve_as_is and (self.checks or "").startswith(_("Lower than previous reading (")):
			return  # taken as the same reading when it was entered: the note of what was entered stays until the reading changes
		society = readcache.society_info(self.society)
		result = engine.evaluate(
			self.previous_reading,
			self.present_reading,
			digits=self.digits,
			multiplier=self.multiplier,
			# a net meter's bills from the previous system hold its net units: they aren't set against its import register
			history=readings.monthly_history(self.connection, self.billing_month, 3, legacy=not cint(self.net_metering)),
			same_month_last_year=readings.same_month_last_year(self.connection, self.billing_month, legacy=not cint(self.net_metering)),
			tolerance_percent=society.reading_tolerance_percent if society.reading_tolerance_percent is not None else 50,
			estimated_since=self.estimated_since_units,
			old_meter_units=self.old_meter_units,
		)
		flags = list(result.flags)
		if engine.FLAG_LOWER in flags:
			# Lower than the previous reading and no roll-over: taken as the same reading (increment 50), so 0 units and next month
			# counts from the right place. What was entered is kept in the note, and a reviewer looks at it.
			flags[flags.index(engine.FLAG_LOWER)] = _("Lower than previous reading ({0} entered): taken as the same reading, 0 units").format(
				f"{flt(self.present_reading):g}")
			self.present_reading = self.previous_reading
		self.units, self.raw_units = flt(result.units), flt(result.raw_units)
		self.consumption_type = "Actual"
		export_flags = self.evaluate_export()
		# a smart meter read by hand is always looked at by a reviewer
		by_hand = [_("Smart meter not communicating: read by hand.")] if cint(self.smart_meter) and cint(self.smart_meter_mute) and self.source != "MDM" else []
		self.checks = "\n".join([*flags, *export_flags, *by_hand]) or None
		avg, low, high = result.expected or (0, 0, 0)
		self.expected_average, self.expected_low, self.expected_high = flt(avg), flt(low), flt(high)
		if result.needs_review or export_flags or by_hand:
			self.status = "Approved" if self.flags.approve_as_is else "Flagged"
		else:
			self.status = "Valid"

	def evaluate_export(self) -> list[str]:
		"""The export (solar) register of a net-metering meter (increment 28): units exported since the last actual reading. Returns
		what needs a reviewer's look: a register lower than before (not a roll-over), or no export reading entered."""
		self.export_units = 0
		if not frappe.utils.cint(self.net_metering):
			return []
		old = flt(self.old_meter_export_units)  # exported on a replaced meter after its last reading
		if not flt(self.present_export_reading) and flt(self.previous_export_reading):
			return [_("Enter the export reading: it is empty or zero, the previous one was {0}.").format(flt(self.previous_export_reading))]
		units, rolled, lower = engine.raw_units(self.previous_export_reading, self.present_export_reading, self.digits, self.multiplier)
		if lower:
			return [_("The export reading {0} is lower than the previous one ({1}).").format(flt(self.present_export_reading),
			                                                                               flt(self.previous_export_reading))]
		self.export_units = flt(units) + old
		return [_("Export register rolled over.")] if rolled else []

	def reset_result(self):
		self.defective = 0
		self.export_units = 0
		self.units = self.raw_units = 0
		self.consumption_type = None
		self.checks = None

	def apply_estimate(self, method):
		"""Estimated units for an unread meter (FS-RDG-10). An exception code whose treatment is
		'Bill zero' bills nothing; otherwise the society's estimation method applies."""
		treatment = (
			frappe.db.get_value("Reading Exception Code", self.exception_code, "billing_treatment")
			if self.exception_code
			else "Estimate"
		)
		self.defective = 0
		if treatment == "Bill zero":
			units, note = 0, _("Not read ({0}): billed zero.").format(self.exception_code)
		elif treatment == defective.TREATMENT:  # FS-WF-08: the society's defective-meter method, the meter marked, a change order raised
			units, note = defective.units_for(self)
			note = _("Not read: {0}. ").format(self.exception_code) + note
			self.defective = 1
			defective.mark_meter(self)
		else:
			est = engine.estimate(
				method,
				readings.monthly_history(self.connection, self.billing_month, 6),
				readings.same_month_last_year(self.connection, self.billing_month),
			)
			units = flt(est) if est is not None else 0
			note = (
				_("Estimated: {0} units ({1}).").format(units, method)
				if est is not None
				else _("No reading history: estimated 0 units.")
			)
			if self.exception_code:
				note = _("Not read: {0}. ").format(self.exception_code) + note
		self.units, self.raw_units = units, 0
		if frappe.utils.cint(self.net_metering):  # exports are never estimated: the register counts them when it is next read
			self.export_units = 0
			note += " " + _("Export not estimated: it is counted at the next actual reading.")
		self.consumption_type = "Estimated"
		self.status = "Estimated"
		self.checks = note
		self.flags.estimated = True
