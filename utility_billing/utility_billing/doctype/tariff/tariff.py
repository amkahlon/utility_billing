# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Tariff: energy pricing for one society, utility and consumer category over a period.

Approval (FS-CFG-06, FS-ACC-04/05): the "Tariff Approval" workflow moves a tariff
Draft -> Pending Approval (Billing Manager) -> Approved (General Manager, submits it) or Rejected.
Maker-checker is enforced here on the server for every user, Administrator included: whoever
submitted the tariff for approval, or created it, can't approve or reject it.

An approved tariff is locked (FS-CFG-08); a change is a new tariff with a later effective date,
which closes the previous open-ended version automatically.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, formatdate, getdate, now_datetime

from utility_billing.utility_billing.tariff_engine import FLAT, calculate_energy, slab_errors

OPEN_END = getdate("9999-12-31")
PENDING = "Pending Approval"
APPROVED = "Approved"
REJECTED = "Rejected"
WORKFLOW = "Tariff Approval"


def workflow_active() -> bool:
	return bool(frappe.db.get_value("Workflow", {"document_type": "Tariff", "is_active": 1}, "name"))


class Tariff(Document):
	def validate(self):
		self.validate_category()
		self.validate_dates()
		self.validate_pricing()
		self.validate_charges()
		self.set_title()
		self.validate_approval()

	def before_submit(self):
		if workflow_active() and self.workflow_state != APPROVED:
			frappe.throw(_("A tariff is approved through the Tariff Approval workflow, not submitted directly."))
		self._predecessor = self.find_overlap_or_predecessor()

	# ----- approval (maker-checker) -----
	def validate_approval(self):
		if not workflow_active() or self.is_new():
			return
		before = self.get_doc_before_save()
		old = before.get("workflow_state") if before else None
		new = self.workflow_state
		user = frappe.session.user
		if old == PENDING and new == PENDING:
			frappe.throw(
				_("This tariff is waiting for approval and can't be changed. The approver can reject it so it can be edited."),
				title=_("Pending approval"),
			)
		if new == PENDING and old != PENDING:
			self.requested_by = user
			self.requested_on = now_datetime()
			self.rejection_reason = None
		if old == PENDING and new in (APPROVED, REJECTED):
			assert_not_maker(self, user)
			if new == APPROVED:
				self.approved_by = user
				self.approved_on = now_datetime()
			elif not (self.rejection_reason or "").strip():
				frappe.throw(_("Give a reason for rejecting this tariff."))

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
			if flt(self.flat_rate) <= 0:
				frappe.throw(_("Enter a Rate per Unit greater than 0."))
			self.slabs = []
		else:
			errors = slab_errors(self.slabs)
			if errors:
				frappe.throw("<br>".join(errors), title=_("Slab table is not valid"))
		if flt(self.minimum_charge) < 0:
			frappe.throw(_("Minimum Charge can't be negative."))

	def validate_charges(self):
		seen = set()
		for row in self.charges:
			if row.charge_type in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.charge_type))
			seen.add(row.charge_type)
			if flt(row.rate) < 0:
				frappe.throw(_("Row {0}: rate can't be negative.").format(row.idx))

	def set_title(self):
		self.tariff_title = tariff_title(self.consumer_category, self.effective_from)

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


def assert_not_maker(doc, user: str):
	"""FS-ACC-05: the maker can't approve or reject their own transaction, at any level."""
	if user in {doc.get("requested_by"), doc.owner}:
		frappe.throw(
			_("You submitted or created this tariff, so another user must approve or reject it (maker-checker)."),
			frappe.PermissionError,
			title=_("Not allowed"),
		)


@frappe.whitelist()
def set_rejection_reason(tariff: str, reason: str):
	"""Store the approver's reason just before the Reject action is applied."""
	doc = frappe.get_doc("Tariff", tariff)
	if doc.workflow_state != PENDING:
		frappe.throw(_("Only a tariff that is pending approval can be rejected."))
	if "General Manager" not in frappe.get_roles():
		frappe.throw(_("Only a General Manager can reject a tariff."), frappe.PermissionError)
	assert_not_maker(doc, frappe.session.user)
	reason = (reason or "").strip()
	if len(reason) < 5:
		frappe.throw(_("Write a reason of at least 5 characters."))
	doc.db_set("rejection_reason", reason, update_modified=False)
	doc.add_comment("Comment", _("Rejected: {0}").format(reason))


def tariff_title(consumer_category: str, effective_from) -> str:
	"""Stored title, e.g. "Residential from 01 Jul 2026". Uses a fixed date format so the stored
	title doesn't change with a site's date-format setting (found in QA, 2026-11-13)."""
	category = frappe.db.get_value("Consumer Category", consumer_category, "category_name")
	return f"{category or consumer_category} from {getdate(effective_from).strftime('%d %b %Y')}"


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
