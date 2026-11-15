# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 23: fill Unique Key from the data already there, so the duplicate-number check is one indexed lookup."""


def execute():
	from utility_billing.install import ensure_indexes
	from utility_billing.utility_billing.uniqueness import rebuild_keys

	rebuild_keys()
	ensure_indexes()
