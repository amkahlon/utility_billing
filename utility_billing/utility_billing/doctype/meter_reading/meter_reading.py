# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Meter Reading: one connection's reading in one reading cycle (FS-RDG-04..11, 15).

Status: Pending (not read yet) -> Valid (checks passed) or Flagged (needs review) -> Approved
(accepted by a reviewer) | Re-read Requested | Estimated (unread, estimated at the estimate step).
A reading is re-checked only when its inputs change, so a reviewer's decision isn't undone by an
unrelated save."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from utility_billing.utility_billing import defective, readings
from utility_billing.utility_billing import reading_engine as engine

INPUTS = ("present_reading", "reading_date", "exception_code", "present_export_reading")
NUMBERS = ("present_reading", "present_export_reading")


class MeterReading(Document):
	def validate(self):
		cycle = frappe.db.get_value(
			"Reading Cycle", self.reading_cycle, ["society", "utility", "billing_month", "status"], as_dict=True
		)
		if cycle.status == "Closed":
			frappe.throw(_("Reading cycle {0} is closed; its readings can't change.").format(self.reading_cycle))
		self.society, self.billing_month = cycle.society, cycle.billing_month
		self.check_connection(cycle)
		self.check_unique()
		if self.is_new() or not self.meter:
			self.load_context()
		self.evaluate()
		self.check_mdi()

	def check_mdi(self):
		"""The maximum demand (MDI) read from the meter: never negative; above the sanctioned load it is noted for a load extension."""
		if flt(self.mdi_kw) < 0:
			frappe.throw(_("The maximum demand can't be negative."))
		sanctioned = flt(frappe.db.get_value("Connection", self.connection, "sanctioned_load_kw"))
		prefix = _("Maximum demand")
		lines = [x for x in (self.checks or "").split("\n") if x and not x.startswith(prefix)]  # an earlier note is replaced
		if flt(self.mdi_kw) and sanctioned and flt(self.mdi_kw) > sanctioned:
			lines.append(_("{0} {1} kW is above the sanctioned load of {2} kW.").format(prefix, flt(self.mdi_kw), sanctioned))
		self.checks = "\n".join(lines) or None

	def on_update(self):
		"""Keep the connection's connected load (last MDI read) up to date."""
		if flt(self.mdi_kw) and self.reading_date:
			last = frappe.db.get_value("Connection", self.connection, "last_mdi_date")
			if not last or getdate(self.reading_date) >= getdate(last):
				frappe.db.set_value("Connection", self.connection, {"last_mdi_kw": flt(self.mdi_kw), "last_mdi_date": self.reading_date},
				                    update_modified=False)

	def check_connection(self, cycle):
		conn = frappe.db.get_value("Connection", self.connection, ["society", "utility"], as_dict=True)
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
		society = frappe.db.get_value(
			"Society", self.society, ["reading_tolerance_percent", "estimation_method"], as_dict=True
		)
		result = engine.evaluate(
			self.previous_reading,
			self.present_reading,
			digits=self.digits,
			multiplier=self.multiplier,
			history=readings.monthly_history(self.connection, self.billing_month, 3),
			same_month_last_year=readings.same_month_last_year(self.connection, self.billing_month),
			tolerance_percent=society.reading_tolerance_percent if society.reading_tolerance_percent is not None else 50,
			estimated_since=self.estimated_since_units,
			old_meter_units=self.old_meter_units,
		)
		self.units, self.raw_units = flt(result.units), flt(result.raw_units)
		self.consumption_type = "Actual"
		export_flags = self.evaluate_export()
		self.checks = "\n".join([*result.flags, *export_flags]) or None
		avg, low, high = result.expected or (0, 0, 0)
		self.expected_average, self.expected_low, self.expected_high = flt(avg), flt(low), flt(high)
		if result.needs_review or export_flags:
			self.status = "Approved" if self.flags.approve_as_is else "Flagged"
		else:
			self.status = "Valid"

	def evaluate_export(self) -> list[str]:
		"""The export (solar) register of a net-metering meter (increment 28): units exported since the last actual reading. Returns
		what needs a reviewer's look: a register lower than before (not a roll-over), or no export reading entered."""
		self.export_units = 0
		if not frappe.utils.cint(self.net_metering):
			return []
		if not flt(self.present_export_reading) and flt(self.previous_export_reading):
			return [_("Enter the export reading: it is empty or zero, the previous one was {0}.").format(flt(self.previous_export_reading))]
		units, rolled, lower = engine.raw_units(self.previous_export_reading, self.present_export_reading, self.digits, self.multiplier)
		if lower:
			return [_("The export reading {0} is lower than the previous one ({1}).").format(flt(self.present_export_reading),
			                                                                               flt(self.previous_export_reading))]
		self.export_units = flt(units)
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
