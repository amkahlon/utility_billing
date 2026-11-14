# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Create the default journal export layouts (increment 8) on sites installed before they existed."""

from utility_billing.install import create_default_export_layouts


def execute():
	create_default_export_layouts()
