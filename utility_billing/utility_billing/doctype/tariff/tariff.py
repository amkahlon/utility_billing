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

from utility_billing.utility_billing import approval
from utility_billing.utility_billing.tariff_engine import FLAT, calculate_energy, slab_errors

OPEN_END = getdate("9999-12-31")
PENDING = approval.PENDING
APPROVED = approval.APPROVED
REJECTED = approval.REJECTED
WORKFLOW = "Tariff Approval"
APPROVER_ROLE = "General Manager"


def workflow_active() -> bool:
	return approval.workflow_active("Tariff")


class Tariff(Document):
	def validate(self):
		self.validate_category()
		self.validate_dates()
		self.validate_pricing()
		self.validate_charges()
		self.set_title()
		self.check_overlap_early()
		self.validate_approval()

	def check_overlap_early(self):
		"""Validate early and late (user's request, 2026-11-13): warn while editing a draft, block at
		Submit for Approval (against approved AND pending tariffs), and check again at approval."""
		if not workflow_active():
			return
		before = None if self.is_new() else self.get_doc_before_save()
		old = before.get("workflow_state") if before else None
		submitting = self.workflow_state == PENDING and old != PENDING
		_predecessor, conflict = self.overlap_status(include_pending=True)
		if not conflict:
			return
		message = self.overlap_message(conflict)
		if submitting:
			frappe.throw(message, title=_("Overlapping tariff"))
		elif self.workflow_state in (approval.DRAFT, REJECTED, None, ""):
			frappe.msgprint(
				message + "<br><br>" + _("You can save this draft, but it can't be submitted for approval until this is resolved."),
				title=_("Overlapping tariff"),
				indicator="orange",
			)

	def before_submit(self):
		if workflow_active() and self.workflow_state != APPROVED:
			frappe.throw(_("A tariff is approved through the Tariff Approval workflow, not submitted directly."))
		self._predecessor = self.find_overlap_or_predecessor()

	# ----- approval (maker-checker, shared rules in approval.py) -----
	def validate_approval(self):
		approval.validate_transition(self)

	def on_update(self):
		approval.notify(self, APPROVER_ROLE)

	def on_cancel(self):
		if workflow_active():
			approval.log(self, APPROVED, self.workflow_state or approval.CANCELLED)
			self.db_update_all()
			self.flags.approval_change = (APPROVED, approval.CANCELLED)
			approval.notify(self, APPROVER_ROLE)

	def on_submit(self):
		approval.notify(self, APPROVER_ROLE)
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
		"""At approval (final check): refuse overlapping approved tariffs, except an open-ended older
		version, which is returned so on_submit can close it the day before this one starts."""
		predecessor, conflict = self.overlap_status(include_pending=False)
		if conflict:
			frappe.throw(self.overlap_message(conflict), title=_("Overlapping tariff"))
		return predecessor

	def overlap_status(self, include_pending: bool):
		"""(predecessor, conflict) for this tariff's period. `predecessor` is an approved open-ended
		tariff that starts earlier (it will be closed automatically); `conflict` is any other approved
		tariff, or with include_pending any tariff pending approval, covering part of the period."""
		if not (self.society and self.utility and self.consumer_category and self.effective_from):
			return None, None
		start = getdate(self.effective_from)
		end = getdate(self.effective_to) if self.effective_to else OPEN_END
		base = {
			"society": self.society,
			"utility": self.utility,
			"consumer_category": self.consumer_category,
			"name": ["!=", self.name or ""],
		}
		fields = ["name", "effective_from", "effective_to", "flat_rate", "pricing_method", "workflow_state", "docstatus"]
		others = frappe.get_all("Tariff", filters={**base, "docstatus": 1}, fields=fields)
		if include_pending:
			others += frappe.get_all("Tariff", filters={**base, "docstatus": 0, "workflow_state": PENDING}, fields=fields)
		predecessor = None
		for o in others:
			o_start = getdate(o.effective_from)
			o_end = getdate(o.effective_to) if o.effective_to else OPEN_END
			if o_start > end or o_end < start:
				continue
			if o.docstatus == 1 and not o.effective_to and o_start < start and predecessor is None:
				predecessor = o.name
				continue
			return predecessor, o
		return predecessor, None

	def overlap_message(self, o) -> str:
		category = frappe.db.get_value("Consumer Category", self.consumer_category, "category_name") or self.consumer_category
		period = _("{0} to {1}").format(
			formatdate(o.effective_from), formatdate(o.effective_to) if o.effective_to else _("open-ended")
		)
		rate = _("Rs {0} per unit").format(o.flat_rate) if o.pricing_method == FLAT else _(o.pricing_method)
		if o.docstatus == 0:
			return _(
				"Tariff {0} for {1} ({2}, {3}) is already waiting for approval for this period. "
				"Only one tariff per category and period can be pending; ask for it to be decided or withdrawn first."
			).format(o.name, category, rate, period)
		return _(
			"Tariff {0} for {1} ({2}, {3}) is already approved and covers part of this period.<br><br>"
			"To change it, either set Effective From to a date after {4}: {0} will then end the day before "
			"and this becomes the new version; or ask the General Manager to cancel {0} and amend it."
		).format(o.name, category, rate, period, formatdate(o.effective_from))

	# ----- calculation -----
	def calculate_energy(self, units):
		return calculate_energy(
			units,
			self.pricing_method,
			flat_rate=self.flat_rate,
			slabs=self.slabs,
			minimum_charge=self.minimum_charge,
		)


assert_not_maker = approval.assert_not_maker


@frappe.whitelist()
def set_rejection_reason(tariff: str, reason: str):
	"""Store the approver's reason just before the Reject action is applied."""
	approval.set_rejection_reason("Tariff", tariff, reason)


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
