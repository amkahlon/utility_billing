# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Create the default KPI dictionary (increment 9) on sites installed before it existed."""

from utility_billing.install import create_default_kpis


def execute():
	create_default_kpis()
