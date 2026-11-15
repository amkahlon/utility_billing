# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Service Request: a consumer's or the society's request that changes a connection, done step by step (see servicereq.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from utility_billing.utility_billing import servicereq as sr_mod

NEEDS = {  # what each kind needs before it can be saved
	sr_mod.NEW: ("property", "consumer", "utility", "new_category"),
	sr_mod.TEMP: ("property", "consumer", "utility", "new_category", "expires_on"),
	sr_mod.CAT: ("new_category", "effective_date"),
	sr_mod.LOAD: ("new_load_kw",),
	sr_mod.OWNER: ("new_consumer",),
	sr_mod.T_IN: ("new_consumer",),
	sr_mod.T_OUT: ("new_consumer",),
	sr_mod.DEPOSIT: ("deposit_change", "deposit_amount"),
	sr_mod.DETECT: ("assessed_load_kw", "hours_per_day", "assessed_days", "rate_per_unit"),
	sr_mod.DISC: ("disconnection_reason",),
	sr_mod.MERGE: ("merge_into",),
}


class ServiceRequest(Document):
	def before_insert(self):
		self.status = "Open"
		sr_mod.prepare(self)

	def validate(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if before and before.status in ("Completed", "Rejected", "Cancelled") and not self.flags.from_steps:
			frappe.throw(_("{0} is {1} and can't be changed.").format(self.name, _(before.status).lower()))
		if before and not self.flags.from_steps:
			if self.status != before.status or self.current_step != before.current_step:
				frappe.throw(_("Steps are done with the step buttons, not by editing the request."))
		t = frappe.get_cached_doc("Service Request Type", self.request_type)
		if not t.enabled:
			frappe.throw(_("Request type {0} is not in use.").format(t.type_name))
		self.kind, self.society = t.kind, t.society
		if self.kind in sr_mod.CREATES_CONNECTION:
			if self.is_new():
				self.connection = None
		elif not self.connection:
			frappe.throw(_("Choose the account the request is for."))
		if self.connection:
			conn = frappe.db.get_value("Connection", self.connection, ["society", "consumer", "property", "status"], as_dict=True)
			if conn.society != self.society:
				frappe.throw(_("Account {0} belongs to another society.").format(self.connection))
			self.consumer = self.consumer or conn.consumer
			self.property = self.property or conn.property
			if self.is_new():
				self.check_status(conn.status)
		for field in NEEDS.get(self.kind, ()):
			if not self.get(field):
				frappe.throw(_("Enter {0} for a {1} request.").format(_(self.meta.get_label(field)), _(self.kind).lower()))
		if self.kind == sr_mod.DISC and self.disconnection_reason != "Safety":  # the notice period, except for safety (FS-CFG-18, WF-22)
			notice = frappe.utils.cint(frappe.db.get_value("Society", self.society, "disconnection_notice_days"))
			earliest = frappe.utils.add_days(self.requested_on or frappe.utils.nowdate(), notice)
			if not self.effective_date:
				self.effective_date = earliest
			elif frappe.utils.getdate(self.effective_date) < frappe.utils.getdate(earliest):
				frappe.throw(_("The society gives {0} days' notice: the disconnection can take effect on {1} at the earliest.").format(
					notice, frappe.utils.formatdate(earliest)))
		if self.kind == sr_mod.MERGE and self.connection:
			keep = frappe.db.get_value("Connection", self.merge_into, ["society", "utility", "status"], as_dict=True)
			mine = frappe.db.get_value("Connection", self.connection, "utility")
			if self.merge_into == self.connection or not keep or keep.society != self.society or keep.utility != mine:
				frappe.throw(_("Merge into another {0} account of the same society.").format(mine))
			if keep.status != "Active":
				frappe.throw(_("Account {0} is {1}; merge into an active account.").format(self.merge_into, _(keep.status)))
		if self.kind == sr_mod.DETECT:
			self.assessed_units = round(flt(self.assessed_load_kw) * flt(self.hours_per_day) * flt(self.assessed_days), 2)
			self.assessed_amount = round(self.assessed_units * flt(self.rate_per_unit), 2)
		if self.kind == sr_mod.TEST and abs(flt(self.test_error_percent)) >= 100:
			frappe.throw(_("A test error of 100% or more isn't a meter reading; check the figure."))
		self.consumer_name = frappe.db.get_value("Consumer", self.consumer, "consumer_name") if self.consumer else None
		self.request_title = f"{t.type_name} · {self.consumer_name or self.connection or ''}".strip(" ·")

	def check_status(self, status):
		k = self.kind
		if k == sr_mod.RECON and status != "Disconnected":
			frappe.throw(_("Only a disconnected account can be reconnected (this one is {0}).").format(_(status)))
		if k in (sr_mod.DISC, sr_mod.METER, sr_mod.CAT, sr_mod.LOAD, sr_mod.MERGE, sr_mod.SPLIT) and status != "Active":
			frappe.throw(_("Account {0} is {1}, not active.").format(self.connection, _(status)))
		if k == sr_mod.CLOSE and status == "Closed":
			frappe.throw(_("Account {0} is already closed.").format(self.connection))
		other = frappe.db.get_value("Service Request", {"connection": self.connection, "kind": k, "status": ["in", ["Open", "In Progress"]],
		                                                  "name": ["!=", self.name or ""]})
		if other:
			frappe.throw(_("{0} is already open for this account.").format(other))

	def after_insert(self):
		sr_mod.demand(self)
		sr_mod.tell_consumer(self, _("received"))

	def on_update(self):
		before = self.get_doc_before_save()
		if not before or self.flags.in_insert:
			return
		if self.status in ("Open", "In Progress") and not self.current_step:
			return  # the last step is done: the completion message follows
		if self.status != before.status or self.current_step != before.current_step:
			sr_mod.tell_consumer(self)

	def on_trash(self):
		if self.status != "Open" or any(s.status != "Pending" for s in self.steps):
			frappe.throw(_("A request that has started can't be deleted; cancel it with a reason."))
