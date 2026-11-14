# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Add the two default refund reasons (increment 12) on sites installed before they existed."""

from utility_billing.install import create_default_reason_codes


def execute():
	create_default_reason_codes()
