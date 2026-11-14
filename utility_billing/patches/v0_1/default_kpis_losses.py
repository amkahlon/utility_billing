# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Add the line loss, AT&C and DISCO KPIs (increment 10) to the KPI dictionary of sites that already have it."""

from utility_billing.install import create_default_kpis


def execute():
	create_default_kpis()
