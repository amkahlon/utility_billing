# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Create the default reason codes (increment 7) on sites installed before they existed."""

from utility_billing.install import create_default_reason_codes


def execute():
	create_default_reason_codes()
