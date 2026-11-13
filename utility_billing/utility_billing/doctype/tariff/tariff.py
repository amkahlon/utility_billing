# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tariff: energy pricing for one society, utility and consumer category over a period.

Submitted = approved (FS-CFG-06). A submitted tariff is locked (FS-CFG-08); a change is a new
tariff with a later effective date, which closes the previous open-ended version automatically.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, formatdate, getdate

from utility_billing.utility_billing.tariff_engine import FLAT, calculate_energy, slab_errors

OPEN_END = getdate("9999-12-31")


class Tariff(Document):
	def validate(self):
		self.validate_category()
		self.validate_dates()
		self.validate_pricing()
		self.validate_charges()
		self.set_title()

	def before_submit(self):
		self._predecessor = self.find_overlap_or_predecessor()

	def on_submit(self):
		prev = getattr(self, "_predecessor", None)
		if prev:
			new_end = add_days(self.effective_from, -1)
			frappe.db.set_value("Tariff", prev, "effective_to", new_end)
			frappe.msgprint(
				_("Tariff {0} now ends on {1}.").format(prev, formatdate(new_end)), alert=True
			)

	# ----- validation -----
	def validate_category(self):
		cat_utility = frappe.db.get_value("Consumer Category", self.consumer_category, "utility")
		if cat_utility and cat_utility != self.utility:
			frappe.throw(
				_("Consumer Category {0} belongs to {1}, not {2}.").format(
					self.consumer_category, cat_utility, self.utility
				)
			)

	def validate_dates(self):
		if self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
			frappe.throw(_("Effective To can't be before Effective From."))

	def validate_pricing(self):
		if self.pricing_method == FLAT:
			if (self.flat_rate or 0) <= 0:
				frappe.throw(_("Enter a Rate per Unit greater than 0."))
			self.slabs = []
		else:
			errors = slab_errors(self.slabs)
			if errors:
				frappe.throw("<br>".join(errors), title=_("Slab table is not valid"))
		if (self.minimum_charge or 0) < 0:
			frappe.throw(_("Minimum Charge can't be negative."))

	def validate_charges(self):
		seen = set()
		for row in self.charges:
			if row.charge_type in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.charge_type))
			seen.add(row.charge_type)
			if (row.rate or 0) < 0:
				frappe.throw(_("Row {0}: rate can't be negative.").format(row.idx))

	def set_title(self):
		category = frappe.db.get_value("Consumer Category", self.consumer_category, "category_name")
		self.tariff_title = f"{category or self.consumer_category} from {formatdate(self.effective_from)}"

	def find_overlap_or_predecessor(self):
		"""Refuse overlapping approved tariffs, except an open-ended older version, which is returned
		so on_submit can close it the day before this one starts."""
		start = getdate(self.effective_from)
		end = getdate(self.effective_to) if self.effective_to else OPEN_END
		others = frappe.get_all(
			"Tariff",
			filters={
				"docstatus": 1,
				"society": self.society,
				"utility": self.utility,
				"consumer_category": self.consumer_category,
				"name": ["!=", self.name],
			},
			fields=["name", "effective_from", "effective_to"],
		)
		predecessor = None
		for o in others:
			o_start = getdate(o.effective_from)
			o_end = getdate(o.effective_to) if o.effective_to else OPEN_END
			if o_start > end or o_end < start:
				continue
			if not o.effective_to and o_start < start and predecessor is None:
				predecessor = o.name
				continue
			frappe.throw(
				_("Tariff {0} ({1} to {2}) already covers part of this period.").format(
					o.name,
					formatdate(o_start),
					formatdate(o.effective_to) if o.effective_to else _("open-ended"),
				),
				title=_("Overlapping tariff"),
			)
		return predecessor

	# ----- calculation -----
	def calculate_energy(self, units):
		return calculate_energy(
			units,
			self.pricing_method,
			flat_rate=self.flat_rate,
			slabs=self.slabs,
			minimum_charge=self.minimum_charge,
		)


def get_applicable_tariff(society: str, utility: str, consumer_category: str, on_date) -> str | None:
	"""Name of the approved tariff in force on `on_date` (FS-CFG-05), or None."""
	d = getdate(on_date)
	rows = frappe.get_all(
		"Tariff",
		filters={
			"docstatus": 1,
			"society": society,
			"utility": utility,
			"consumer_category": consumer_category,
			"effective_from": ["<=", d],
		},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", d]],
		fields=["name"],
		order_by="effective_from desc",
		limit=1,
	)
	return rows[0].name if rows else None


@frappe.whitelist()
def preview_energy(tariff: str, units: float):
	"""Energy charge for `units` under a tariff, for the form's quick check."""
	doc = frappe.get_doc("Tariff", tariff)
	doc.check_permission("read")
	result = doc.calculate_energy(units)
	return {
		"units": float(result.units),
		"amount": float(result.amount),
		"minimum_applied": result.minimum_applied,
		"lines": [
			{k: (float(v) if v is not None and not isinstance(v, str) else v) for k, v in line.items()}
			for line in result.lines
		],
	}
