# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Migration Profile: how one society's previous system writes its data (increment 29). See migration.py."""

import re

import frappe
from frappe import _
from frappe.model.document import Document

from utility_billing.utility_billing import migration


class MigrationProfile(Document):
	def validate(self):
		if self.house_pattern:
			try:
				rx = re.compile(self.house_pattern)
			except re.error as e:
				frappe.throw(_("The house number pattern isn't a valid pattern: {0}.").format(e))
			if "house" not in rx.groupindex:
				frappe.throw(_("The house number pattern needs a part named house, written (?P<house>...)."))
		seen = set()
		for c in self.columns:
			c.target = (c.target or "").strip()
			if c.target not in migration.TARGETS.get(c.sheet, []):
				frappe.throw(_("Row {0}: {1} isn't a column of our {2} sheet. Our columns: {3}.").format(
					c.idx, c.target, c.sheet, ", ".join(migration.TARGETS.get(c.sheet, []))))
			k = (c.sheet, migration._key(c.source_column))
			if k in seen:
				frappe.throw(_("Row {0}: column {1} of the {2} sheet is mapped twice.").format(c.idx, c.source_column, c.sheet))
			seen.add(k)
		for v in self.value_maps:
			v.target = (v.target or "").strip()
			if v.target not in migration.MAPPED + ("kind", "mode"):
				frappe.throw(_("Row {0}: values can be mapped for {1}.").format(v.idx, ", ".join(migration.MAPPED + ("kind", "mode"))))


@frappe.whitelist(methods=["POST"])
def fill_sap_isu(name: str) -> int:
	"""Add SAP IS-U's standard column names to the profile's column map (rows already there are kept)."""
	doc = frappe.get_doc("Migration Profile", name)
	doc.check_permission("write")
	have = {(c.sheet, migration._key(c.source_column)) for c in doc.columns}
	added = 0
	for sheet, source, target in migration.SAP_ISU:
		if (sheet, migration._key(source)) not in have:
			doc.append("columns", {"sheet": sheet, "source_column": source, "target": target})
			added += 1
	if not doc.source_system:
		doc.source_system = "SAP IS-U"
	doc.save()
	return added
