# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Increment 23: the Merge and Split request types for societies whose request types are already set up."""

import frappe


def execute():
	from utility_billing.utility_billing.servicereq import ensure_types

	for society in frappe.get_all("Service Request Type", distinct=True, pluck="society"):
		ensure_types(society)
