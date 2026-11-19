# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Batches loaded before the performance step kept one log row per record created. Records now carry their batch (the
`migration_batch` field) and the batch keeps a count per kind: move the old log rows onto the records, then replace them."""

import frappe

STAMPED = ("Feeder", "Transformer", "Meter", "Property", "Consumer", "Connection")


def execute():
	rows = frappe.get_all("Migration Record", filters={"record_name": ["is", "set"], "record_type": ["in", STAMPED]},
	                      fields=["parent", "record_type", "record_name"])
	for r in rows:
		frappe.db.set_value(r.record_type, r.record_name, "migration_batch", r.parent, update_modified=False)
	for batch in {r.parent for r in frappe.get_all("Migration Record", fields=["parent"])}:
		if not frappe.db.exists("Migration Batch", batch):
			continue
		opening = frappe.db.get_value("Migration Batch", batch, "opening_import")
		frappe.db.delete("Migration Record", {"parent": batch})
		idx = 0
		for dt in (*STAMPED, "Legacy Bill"):
			n = frappe.db.count(dt, {"migration_batch": batch})
			if n:
				idx += 1
				frappe.get_doc({"doctype": "Migration Record", "parent": batch, "parenttype": "Migration Batch", "parentfield": "records",
				                "idx": idx, "record_type": dt, "record_count": n}).db_insert()
		if opening:
			frappe.get_doc({"doctype": "Migration Record", "parent": batch, "parenttype": "Migration Batch", "parentfield": "records",
			                "idx": idx + 1, "record_type": "Opening Balance Import", "record_name": opening, "record_count": 1}).db_insert()
