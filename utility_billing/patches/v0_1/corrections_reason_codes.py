# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Add the default reasons for due date extensions, bulk adjustments and disputes (increment 13)."""

from utility_billing.install import create_default_reason_codes


def execute():
	create_default_reason_codes()
