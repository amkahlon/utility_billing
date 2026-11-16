# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Report PDFs (the report's menu › PDF) on servers whose wkhtmltopdf is built without patched Qt.

Frappe puts the report's page footer in the PDF with --footer-html, which only a wkhtmltopdf with patched Qt supports; the Ubuntu
package is unpatched, so every report PDF failed with "Could not generate PDF" (bug report 2026-11-16, Security Deposits Held).
On such a server the header and footer are left out and the report itself prints. Bills and receipts don't use them, so they were
never affected. The lasting fix is the wkhtmltopdf build with patched Qt on the server."""

import functools
import subprocess

import frappe
from frappe.utils import print_format


@functools.cache
def patched_qt() -> bool:
	try:
		out = subprocess.run(["wkhtmltopdf", "--version"], capture_output=True, text=True, timeout=10).stdout
	except Exception:  # noqa: BLE001 - no wkhtmltopdf at all: leave the error to Frappe, as before
		return True
	return "patched qt" in out.lower()


def without_header_footer(html: str) -> str:
	from bs4 import BeautifulSoup  # noqa: PLC0415

	soup = BeautifulSoup(html, "html.parser")
	for el in soup.select("#header-html, #footer-html"):
		el.decompose()
	return str(soup)


@frappe.whitelist()
def report_to_pdf(html: str, orientation: str = "Landscape"):
	if not patched_qt():
		html = without_header_footer(html)
	return print_format.report_to_pdf(html, orientation)
