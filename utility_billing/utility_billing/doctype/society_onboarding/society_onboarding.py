# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Society Onboarding: the go-live checklist of one society (FS-CLT-10). The system's checks are worked out live (onboarding.py);
people tick the tasks, and who and when is recorded."""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, nowdate

from utility_billing.utility_billing import onboarding


class SocietyOnboarding(Document):
	def validate(self):
		if self.is_new() and not self.tasks:
			self.set("tasks", [{"task": t} for t in onboarding.DEFAULT_TASKS])
		before = {r.name: r for r in (self.get_doc_before_save() or frappe._dict(tasks=[])).tasks}
		for r in self.tasks:
			was = before.get(r.name)
			if cint(r.done) and not (was and cint(was.done)):
				r.done_by, r.done_on = frappe.session.user, nowdate()
			elif not cint(r.done):
				r.done_by = r.done_on = None
		checks = onboarding.status(self.society)
		self.checks_done, self.checks_total = sum(c["done"] for c in checks), len(checks)
		tasks_done = sum(cint(r.done) for r in self.tasks)
		total = self.checks_total + len(self.tasks)
		self.percent_complete = round((self.checks_done + tasks_done) * 100 / total, 1) if total else 0
