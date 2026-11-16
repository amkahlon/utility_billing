# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""PDFs that work on any server this app runs on (bug report 2026-11-16: "Could not generate PDF").

Two server gaps broke every PDF Frappe makes (a report's PDF, any record's Print › PDF, a PDF attached to an e-mail); the app's
own bills and receipts were not affected:

- Frappe 16 prints records with headless Chromium by default, and Chromium can't start without its system libraries (libnss3,
  libnspr4, libasound2). Where it can't start, wkhtmltopdf is used instead.
- wkhtmltopdf from the Ubuntu package is built without patched Qt and can't print a page header or footer (--header-html,
  --footer-html), which Frappe adds to reports and prints. Where it is unpatched, the header and footer are left out.

The lasting fix is on the server: Chromium's libraries, or the wkhtmltopdf build with patched Qt. These fallbacks are applied once
per process, from hooks.py."""

import functools
import re
import os
import subprocess

import frappe


@functools.cache
def patched_qt() -> bool:
	try:
		out = subprocess.run(["wkhtmltopdf", "--version"], capture_output=True, text=True, timeout=10).stdout
	except Exception:  # noqa: BLE001 - no wkhtmltopdf at all: nothing to work around
		return True
	return "patched qt" in out.lower()


@functools.cache
def chrome_works() -> bool:
	"""Whether Frappe's headless Chromium starts on this server."""
	try:
		from frappe.utils.pdf_generator.browser import get_chromium_path  # noqa: PLC0415

		path = get_chromium_path()
	except Exception:  # noqa: BLE001 - Frappe looks for it its own way; try the bench's copy
		path = os.path.join(frappe.utils.get_bench_path(), "chromium", "chrome-linux", "headless_shell")
	if not path or not os.path.exists(path):
		return False
	try:
		r = subprocess.run([path, "--headless", "--no-sandbox", "--dump-dom", "about:blank"], capture_output=True, timeout=20)
	except Exception:  # noqa: BLE001
		return False
	return r.returncode == 0 and b"error while loading shared libraries" not in r.stderr


def without_header_footer(html: str) -> str:
	from bs4 import BeautifulSoup  # noqa: PLC0415

	soup = BeautifulSoup(html, "html.parser")
	for el in soup.select("#header-html, #footer-html"):
		el.decompose()
	return str(soup)


LINK = re.compile(r"""<link\b[^>]*\bhref=["'](?:https?://[^/"']+)?(/assets/[^"'?#]+\.css)(?:\?[^"']*)?["'][^>]*>""", re.IGNORECASE)


def inline_stylesheets(html: str) -> str:
	"""The site's own stylesheets put into the page from disk. wkhtmltopdf fetches a <link> by the site's address, and when the
	server can't resolve its own name (ub.localhost in WSL; a server behind a proxy) the whole PDF fails with HostNotFoundError."""
	assets = os.path.join(frappe.utils.get_bench_path(), "sites")

	def inline(m):
		# normpath, not realpath: assets/<app> are symlinks into the apps; ".." can't climb out of sites/assets
		path = os.path.normpath(os.path.join(assets, m.group(1).lstrip("/")))
		if not path.startswith(os.path.join(assets, "assets") + os.sep) or not os.path.isfile(path):
			return m.group(0)
		with open(path, encoding="utf-8") as f:
			return f"<style>{f.read()}</style>"

	return LINK.sub(inline, html) if html and "<link" in html else html


def apply():
	"""Patch Frappe's PDF functions once in this process."""
	from frappe.utils import pdf as fpdf  # noqa: PLC0415
	from frappe.utils import print_utils  # noqa: PLC0415

	if getattr(fpdf, "_ub_patched", False):
		return
	fpdf._ub_patched = True

	read_options = fpdf.read_options_from_html

	def read_options_from_html(html):
		html, options = read_options(html)
		if not patched_qt():
			for key in ("header-html", "footer-html"):
				path = options.pop(key, None)
				if path and isinstance(path, str) and os.path.exists(path):
					os.remove(path)
		return html, options

	fpdf.read_options_from_html = read_options_from_html

	# a stylesheet or image the PDF engine can't fetch (the site's own address unknown to the server, as with *.localhost) must not
	# fail the whole PDF: Frappe's report PDF already says so; record prints didn't
	prepare = fpdf.prepare_options

	def prepare_options(html, options):
		html, options = prepare(inline_stylesheets(html), options)
		options.setdefault("load-error-handling", "ignore")
		options.setdefault("load-media-error-handling", "ignore")
		return html, options

	fpdf.prepare_options = prepare_options

	resolve = print_utils.resolve_pdf_generator

	def resolve_pdf_generator(print_format=None, pdf_generator=None):
		generator = resolve(print_format=print_format, pdf_generator=pdf_generator)
		return "wkhtmltopdf" if generator == "chrome" and not chrome_works() else generator

	print_utils.resolve_pdf_generator = resolve_pdf_generator

	# a print asked for Chrome by name (the request's pdf_generator) also falls back: returning nothing lets Frappe use wkhtmltopdf
	chrome_pdf = fpdf.get_chrome_pdf

	def get_chrome_pdf(print_format, html, options, output, pdf_generator=None):
		if pdf_generator == "chrome" and not chrome_works():
			return None
		return chrome_pdf(print_format, html, options, output, pdf_generator=pdf_generator)

	fpdf.get_chrome_pdf = get_chrome_pdf
