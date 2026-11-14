# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Property: a house, plot, apartment or shop in a society, with its owners and tenants over
time (FS-MST-01, 03)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate, getdate

from utility_billing.utility_billing import network

OPEN_END = getdate("9999-12-31")


def _period(row):
	return getdate(row.from_date), getdate(row.to_date) if row.to_date else OPEN_END


def _overlap(a, b) -> bool:
	a_start, a_end = _period(a)
	b_start, b_end = _period(b)
	return a_start <= b_end and b_start <= a_end


class Property(Document):
	def before_insert(self):
		# The ID "<society>-<property no>" is built before validate(), so tidy the number here.
		self.normalise_property_no()

	def normalise_property_no(self):
		self.property_no = " ".join((self.property_no or "").split())

	def validate(self):
		self.normalise_property_no()
		if flt(self.area) < 0:
			frappe.throw(_("Area can't be negative."))
		self.validate_occupants()
		network.check_location(self)  # FS-MST-27

	def validate_occupants(self):
		rows = self.occupants
		for r in rows:
			if r.to_date and getdate(r.to_date) < getdate(r.from_date):
				frappe.throw(_("Occupants row {0}: To date is before From date.").format(r.idx))
			if r.occupant_role != "Owner":
				r.share_percent = 0
		for i, a in enumerate(rows):
			for b in rows[i + 1 :]:
				if not _overlap(a, b):
					continue
				if a.consumer == b.consumer and a.occupant_role == b.occupant_role:
					frappe.throw(
						_("Occupants rows {0} and {1}: {2} is listed twice as {3} for the same period.").format(
							a.idx, b.idx, a.consumer_name or a.consumer, a.occupant_role
						)
					)
				if a.is_billed and b.is_billed:
					frappe.throw(
						_("Occupants rows {0} and {1}: only one occupant can be billed at a time.").format(
							a.idx, b.idx
						)
					)
		# Owners' shares can't exceed 100% on any date. Shares only change at a row's start date.
		owners = [r for r in rows if r.occupant_role == "Owner"]
		for r in owners:
			day = getdate(r.from_date)
			total = sum(flt(o.share_percent) for o in owners if _period(o)[0] <= day <= _period(o)[1])
			if total > 100.0001:
				frappe.throw(
					_("Owners' shares add up to {0}% on {1}. They can't exceed 100%.").format(
						round(total, 2), formatdate(day)
					)
				)

	def get_billed_occupant(self, on_date=None):
		"""The occupant billed on a date (default today), or None."""
		d = getdate(on_date)
		for r in self.occupants:
			start, end = _period(r)
			if r.is_billed and start <= d <= end:
				return r
		return None
