# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Writing a bill run's documents many at a time (FS-RUN-07: 20,000 bills in under 10 minutes; the performance step).

A document saved the usual way costs a dozen statements or more: its number, link checks, one insert per row, the reload before a
save. A run writes tens of thousands of documents it has itself worked out, so it builds them in memory exactly as an insert would
(defaults, owner, timestamps, numbers from the same series) and writes them a few hundred rows per statement.

Nothing else may use this: it runs no validation, hooks or notifications. `plain()` says whether that is safe on this site."""

import frappe
from frappe.model.naming import parse_naming_series
from frappe.utils import cint, now

ROWS_PER_STATEMENT = 200


def plain(*doctypes) -> bool:
	"""True when nothing on the site reacts to these documents being saved (a Notification, Webhook or Server Script set up on
	them): then a run may write them in bulk. Otherwise it saves them one by one, so those still fire."""
	doctypes = list(doctypes)
	for dt, field, extra in (("Notification", "document_type", {"enabled": 1}), ("Webhook", "webhook_doctype", {"enabled": 1}),
	                         ("Server Script", "reference_doctype", {"disabled": 0, "script_type": "DocType Event"})):
		if frappe.db.exists(dt, {field: ["in", doctypes], **extra}):
			return False
	return True


def _series(doctype):
	"""(the series key in tabSeries, digits, the name with a marker where the number goes) of the doctype's naming series today."""
	df = frappe.get_meta(doctype).get_field("naming_series")
	series = (df.default or (df.options or "").split("\n")[0]).strip()
	seen = {}

	def marker(key, digits):
		seen.update(key=key, digits=digits)
		return "\x00"

	template = parse_naming_series(series.split("."), number_generator=marker)
	return seen["key"], seen["digits"], template


def release_names(doctype, names):
	"""Give back the numbers of deleted documents that are the last ones of the series, so the documents that replace them take the
	same numbers and the series has no gap (deleting documents one by one does the same, through Frappe)."""
	key, _digits, template = _series(doctype)
	head, _sep, tail = template.partition("\x00")
	numbers = set()
	for n in names:
		if n.startswith(head) and (not tail or n.endswith(tail)):
			middle = n[len(head):len(n) - len(tail)] if tail else n[len(head):]
			if middle.isdigit():
				numbers.add(int(middle))
	if not numbers:
		return
	current = frappe.db.sql("select `current` from `tabSeries` where name = %s for update", key)
	if not current or current[0][0] is None:
		return
	value = start = cint(current[0][0])
	while value in numbers:
		value -= 1
	if value != start:
		frappe.db.sql("update `tabSeries` set `current` = %s where name = %s", (value, key))


def reserve_names(doctype, count) -> list:
	"""The next `count` numbers of the doctype's naming series, taken in one step (the series row stays locked to the commit, as when
	documents are numbered one by one)."""
	if not count:
		return []
	key, digits, template = _series(doctype)
	seen = {"key": key, "digits": digits}
	current = frappe.db.sql("select `current` from `tabSeries` where name = %s for update", seen["key"])
	if current and current[0][0] is not None:
		start = cint(current[0][0])
		frappe.db.sql("update `tabSeries` set `current` = `current` + %s where name = %s", (count, seen["key"]))
	else:
		start = 0
		frappe.db.sql("insert into `tabSeries` (name, `current`) values (%s, %s)", (seen["key"], count))
	return [template.replace("\x00", str(start + i).zfill(seen["digits"])) for i in range(1, count + 1)]


class Inserter:
	"""Collects new documents and writes them, with their child rows, a few hundred rows per statement."""

	def __init__(self):
		self.docs = []

	def add(self, fields: dict):
		doc = frappe.get_doc(fields)
		doc.set("__islocal", True)
		doc._set_defaults()
		self.docs.append(doc)
		return doc

	def flush(self):
		"""Number the documents and insert them. Returns them (now with their names)."""
		docs, self.docs = self.docs, []
		if not docs:
			return docs
		stamp, user = now(), frappe.session.user
		by_doctype = {}
		for d in docs:
			by_doctype.setdefault(d.doctype, []).append(d)
		tables = {}
		for doctype, group in by_doctype.items():
			for doc, name in zip(group, reserve_names(doctype, len(group)), strict=True):
				doc.name = name
				doc.creation = doc.modified = stamp
				doc.owner = doc.modified_by = user
				doc.docstatus = cint(doc.docstatus)
				tables.setdefault(doctype, []).append(doc)
				for child in doc.get_all_children():
					child.name = frappe.generate_hash(length=10)
					child.parent, child.parenttype = name, doctype
					child.creation = child.modified = stamp
					child.owner = child.modified_by = user
					child.docstatus = doc.docstatus
					tables.setdefault(child.doctype, []).append(child)
		for doctype, rows in tables.items():
			insert_rows(doctype, [r.get_valid_dict(convert_dates_to_str=True, ignore_virtual=True) for r in rows])
		return docs


def insert_rows(doctype, rows):
	if not rows:
		return
	columns = list(rows[0])
	head = f"insert into `tab{doctype}` ({', '.join(f'`{c}`' for c in columns)}) values "
	one = "(" + ", ".join(["%s"] * len(columns)) + ")"
	for k in range(0, len(rows), ROWS_PER_STATEMENT):
		part = rows[k:k + ROWS_PER_STATEMENT]
		frappe.db.sql(head + ", ".join([one] * len(part)), [r.get(c) for r in part for c in columns])


def in_chunks(items, size):
	items = list(items)
	for k in range(0, len(items), size):
		yield items[k:k + size]
