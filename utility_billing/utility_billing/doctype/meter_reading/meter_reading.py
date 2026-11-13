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

from utility_billing.utility_billing import reading_engine as engine
from utility_billing.utility_billing import readings

INPUTS = ("present_reading", "reading_date", "exception_code")


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
			(flt(before.get(f)) != flt(self.get(f))) if f == "present_reading" else (before.get(f) != self.get(f))
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
		self.checks = "\n".join(result.flags) or None
		avg, low, high = result.expected or (0, 0, 0)
		self.expected_average, self.expected_low, self.expected_high = flt(avg), flt(low), flt(high)
		if result.needs_review:
			self.status = "Approved" if self.flags.approve_as_is else "Flagged"
		else:
			self.status = "Valid"

	def reset_result(self):
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
		if treatment == "Bill zero":
			units, note = 0, _("Not read ({0}): billed zero.").format(self.exception_code)
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
		self.consumption_type = "Estimated"
		self.status = "Estimated"
		self.checks = note
		self.flags.estimated = True
