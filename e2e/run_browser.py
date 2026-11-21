# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Browser tests of the main screens (review of 2026-11-18, step 3).

A real Chromium (Playwright) signs in through the login form and uses the screens with a real mouse and keyboard: this is what the
HTTP suite and the unit tests can't see (a box that takes no typing, a drag that doesn't drop, a script error on a page).
Nothing is saved: forms are opened and typed in, not submitted.

Run after the HTTP suite, which leaves a society with data on the QA site (inside WSL, with the QA server running):
    ~/e2e-venv/bin/python apps/utility_billing/e2e/run_browser.py
Environment: E2E_BROWSER_URL (default http://qa.localhost:8001; *.localhost resolves to this machine in Chromium),
BROWSER_SHOTS (folder for a screenshot of each failed case, default ~/browser-shots). The admin password is read from
QA_ADMIN_PASSWORD in ~/.dev-secrets. Never run against ub.localhost. Exit code 0 = all passed.
"""

from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

URL = os.environ.get("E2E_BROWSER_URL", "http://qa.localhost:8001").rstrip("/")
SHOTS = Path(os.environ.get("BROWSER_SHOTS", Path.home() / "browser-shots"))
if "ub.localhost" in URL:
	raise SystemExit("Never against ub.localhost: it holds the demo data.")

# script errors that aren't ours to fix: none known yet; add a pattern here only with the reason beside it
IGNORED_ERRORS: tuple[str, ...] = ()

PAGES = (  # route, what must be on it
	("ub-dashboard", ".ub-card"), ("ub-consumer-360", ".ub-c360-q"), ("ub-cash", ".ub-card"), ("ub-billrun", ".ub-card, .ub-empty"),
	("ub-readings", ".ub-card"), ("ub-review", ".ub-card, .ub-empty"), ("ub-recovery", ".ub-card, .ub-empty"),
	("ub-approvals", ".ub-root"), ("ub-rights", ".ub-root, .ub-card"), ("ub-tariff", ".ub-root, .ub-card"), ("ub-users", ".ub-root, .ub-card"),
	("ub-bill-designer", ".bd-blank"), ("ub-ask", ".ub-q"),
)
FORMS = (  # doctype route, a field to type in
	("consumer", "consumer_name"), ("property", None), ("meter", None), ("migration-profile", "profile_name"),
	("reason-code", None), ("tariff", None), ("connection", None), ("bill-run", None), ("payment-receipt", None), ("migration-batch", None),
	("parallel-run-month", None),
)
LISTS = ("utility-bill", "connection", "consumer", "meter-reading", "payment-receipt", "customer-ledger-entry", "bill-run", "tariff")
REPORTS = ("Bill Register", "Arrears Ageing", "Collections", "Net Metering Register")


def secret(key: str) -> str:
	for line in (Path.home() / ".dev-secrets").read_text().splitlines():
		if line.startswith(key + "="):
			return line.split("=", 1)[1]
	raise SystemExit(f"{key} missing in ~/.dev-secrets")


class Session:
	def __init__(self, page: Page):
		self.page = page
		self.errors: list[str] = []
		page.on("pageerror", lambda e: self.errors.append(str(e)))
		page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" and "Uncaught" in m.text else None)

	def go(self, route: str):
		self.page.goto(f"{URL}/desk/{route}", wait_until="domcontentloaded")

	def idle(self):
		"""The desk has finished its requests (as far as it tells us)."""
		self.page.wait_for_function("window.frappe && frappe.request && !frappe.request.ajax_count && document.readyState === 'complete'",
		                            timeout=30000)

	def call(self, method: str, args: dict | None = None):
		return self.page.evaluate("([m, a]) => frappe.xcall(m, a)", [method, args or {}])

	def take_errors(self) -> list[str]:
		found = [e for e in self.errors if not any(re.search(p, e) for p in IGNORED_ERRORS)]
		self.errors.clear()
		return found


# ---------------------------------------------------------------- the cases
def t_sign_in(s: Session):
	p = s.page
	p.goto(f"{URL}/login", wait_until="domcontentloaded")
	p.fill("#login_email", "Administrator")
	p.fill("#login_password", secret("QA_ADMIN_PASSWORD"))
	p.locator("form.form-login button[type=submit], form.form-login .btn-login, .form-login button").first.click()
	p.wait_for_url(re.compile(r"/desk"), timeout=30000)
	s.idle()
	assert p.evaluate("frappe.session.user") == "Administrator", "not signed in"


def t_sidebar(s: Session):
	s.go("ub-dashboard")
	s.idle()
	text = s.page.locator("body").inner_text()
	for label in ("Consumer 360", "Bill Run", "Cash Counter", "Migration Batch", "Parallel Run Month", "Net Metering Register"):
		assert label in text, f"sidebar has no '{label}'"


def t_pages(s: Session):
	missing = []
	for route, selector in PAGES:
		s.go(route)
		try:
			s.page.locator(selector).first.wait_for(state="visible", timeout=20000)
			s.idle()
		except Exception:
			missing.append(route)
		errors = s.take_errors()
		if errors:
			missing.append(f"{route}: script error: {errors[0][:160]}")
	assert not missing, "; ".join(missing)


def t_dashboard(s: Session):
	s.go("ub-dashboard")
	s.page.locator(".ub-card").first.wait_for(timeout=20000)
	s.idle()
	assert s.page.locator(".ub-card").count() >= 3, "fewer than 3 cards on the dashboard"
	assert s.page.locator(".ub-card .num").count() >= 1, "no figures on the dashboard"


def t_consumer_360(s: Session):
	s.go("ub-consumer-360")
	box = s.page.locator(".ub-c360-q")
	box.wait_for(timeout=20000)
	s.idle()
	rows = s.page.evaluate("""() => frappe.db.get_list('Utility Bill', {fields: ['connection'], filters: {docstatus: 1}, order_by: 'creation desc', limit: 1})""")
	assert rows, "the QA site has no posted bill: run the HTTP suite first"
	account = rows[0]["connection"]
	box.click()
	s.page.keyboard.type(account, delay=20)
	expect(box).to_have_value(account)
	s.page.get_by_text("to move, Enter to open").wait_for(timeout=20000)  # the results are listed
	s.page.keyboard.press("Enter")
	tab = s.page.locator(".ub-c360-tab").first
	tab.wait_for(timeout=20000)
	assert account in s.page.locator("body").inner_text(), "the account isn't shown"
	for i in range(s.page.locator(".ub-c360-tab").count()):  # every tab opens without a script error
		s.page.locator(".ub-c360-tab").nth(i).click()
		s.idle()


def t_cash_counter(s: Session):
	s.go("ub-cash")
	s.page.locator(".ub-card").first.wait_for(timeout=20000)
	s.idle()
	ref = s.page.locator(".ub-cash-ref")
	if ref.count():  # a day is open: the scan box takes typing
		ref.click()
		s.page.keyboard.type("1000000016", delay=20)
		expect(ref).to_have_value("1000000016")
	else:
		expect(s.page.locator(".ub-cash-open")).to_be_visible()


def t_ask_box_takes_typing(s: Session):
	"""Bug report of 2026-11-17: the Ask Your Data box took no typing."""
	s.go("ub-ask")
	box = s.page.locator(".ub-q")
	box.wait_for(timeout=20000)
	s.idle()
	box.click()
	s.page.keyboard.type("which blocks collected least last month?", delay=15)
	expect(box).to_have_value("which blocks collected least last month?")


def t_designer_drag(s: Session):
	"""Increment 27: dragging a block from the palette onto the page passed simulated tests but failed in the user's hands."""
	p = s.page
	s.go("ub-bill-designer")
	p.locator(".bd-blank").wait_for(timeout=20000)
	s.idle()
	p.locator(".bd-blank").click()
	pal = p.locator(".bd-pal").first
	pal.wait_for(timeout=20000)
	frame = p.locator("iframe.bd-frame")
	frame.wait_for(timeout=20000)
	p.wait_for_timeout(1500)  # the preview is drawn in the frame
	before = p.locator(".bd-chip").count()
	a, b = pal.bounding_box(), frame.bounding_box()
	assert a and b, "palette or page not on screen"
	p.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
	p.mouse.down()
	tx, ty = b["x"] + b["width"] / 2, b["y"] + min(b["height"] / 3, 200)
	p.mouse.move(tx - 40, ty - 40, steps=12)
	p.mouse.move(tx, ty, steps=8)
	p.mouse.up()
	p.wait_for_timeout(800)
	after = p.locator(".bd-chip").count()
	assert after == before + 1, f"the dropped block isn't in the outline (blocks before {before}, after {after})"
	label = pal.inner_text().strip()
	assert label in p.locator(".bd-tree").inner_text(), f"'{label}' not in the outline"


def t_new_forms(s: Session):
	bad = []
	for route, field in FORMS:
		s.go(f"{route}/new")
		try:
			s.page.locator(".form-layout").first.wait_for(state="visible", timeout=20000)
			s.idle()
			if field:
				box = s.page.locator(f'.frappe-control[data-fieldname="{field}"] input').first
				box.click()
				s.page.keyboard.type("Browser test", delay=10)
				expect(box).to_have_value("Browser test")
		except Exception as e:
			bad.append(f"{route}: {str(e).splitlines()[0][:120]}")
		errors = s.take_errors()
		if errors:
			bad.append(f"{route}: script error: {errors[0][:160]}")
		s.page.evaluate("() => { if (window.cur_frm) cur_frm.doc.__unsaved = 0; window.onbeforeunload = null; }")  # leave without the prompt
	assert not bad, "; ".join(bad)


def t_lists(s: Session):
	bad = []
	for route in LISTS:
		s.go(route)
		try:
			s.page.locator(".frappe-list, .list-view-container, .result").first.wait_for(state="visible", timeout=20000)
			s.idle()
		except Exception:
			bad.append(route)
		errors = s.take_errors()
		if errors:
			bad.append(f"{route}: script error: {errors[0][:160]}")
	assert not bad, "; ".join(bad)


def t_open_a_bill(s: Session):
	rows = s.page.evaluate("""() => frappe.db.get_list('Utility Bill', {fields: ['name'], filters: {docstatus: 1}, order_by: 'creation desc', limit: 1})""")
	assert rows, "no posted bill on the QA site"
	s.go(f"utility-bill/{rows[0]['name']}")
	s.page.locator(".form-layout").first.wait_for(state="visible", timeout=20000)
	s.idle()
	assert rows[0]["name"] in s.page.locator("body").inner_text(), "the bill didn't open"


def t_reports(s: Session):
	bad = []
	for name in REPORTS:
		s.go(f"query-report/{name}")
		try:
			s.page.locator(".page-form, .report-wrapper, .datatable").first.wait_for(state="visible", timeout=25000)
			s.idle()
		except Exception:
			bad.append(name)
		errors = s.take_errors()
		if errors:
			bad.append(f"{name}: script error: {errors[0][:160]}")
	assert not bad, "; ".join(bad)


def t_phone_width(s: Session):
	p = s.page
	p.set_viewport_size({"width": 390, "height": 844})
	try:
		wide = []
		for route in ("ub-dashboard", "ub-consumer-360", "ub-cash"):
			s.go(route)
			p.locator(".ub-card, .ub-c360-q").first.wait_for(timeout=20000)
			s.idle()
			over = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
			if over > 4:
				wide.append(f"{route} scrolls sideways by {over}px")
		assert not wide, "; ".join(wide)
	finally:
		p.set_viewport_size({"width": 1366, "height": 850})


def t_one_banner_per_message(s: Session):
	"""User, 2026-11-21: the Court Hold form showed its banner twice (Frappe adds a banner each time a form refreshes)."""
	rows = s.page.evaluate("""() => frappe.db.get_list('Court Hold', {fields: ['name'], order_by: 'creation desc', limit: 1})""")
	assert rows, "the QA site has no court hold: run the HTTP suite first"
	s.go(f"court-hold/{rows[0]['name']}")
	s.page.locator(".form-layout").first.wait_for(state="visible", timeout=20000)
	s.idle()
	shown = "() => [...cur_frm.layout.message[0].querySelectorAll('.form-message')].map(x => x.innerText.trim())"
	texts = s.page.evaluate(f"""() => {{
		cur_frm.dashboard.set_headline_alert('Browser test banner', 'blue');
		cur_frm.dashboard.set_headline_alert('Browser test banner', 'blue');
		cur_frm.dashboard.set_headline_alert('Another browser test banner', 'orange');
		return ({shown})();
	}}""")
	assert texts.count("Browser test banner") == 1, f"the same banner is shown {texts.count('Browser test banner')} times"
	assert "Another browser test banner" in texts, "a different banner was removed"
	texts = s.page.evaluate(f"async () => {{ await cur_frm.refresh(); await cur_frm.refresh(); return ({shown})(); }}")
	assert len(texts) == len(set(texts)), f"a banner is repeated after two refreshes: {texts}"


CASES = (
	("BR-01", "Sign in through the login form", t_sign_in),
	("BR-02", "Sidebar lists the main screens, Migration and the Net Metering Register", t_sidebar),
	("BR-03", "Every custom page opens with its content and no script error", t_pages),
	("BR-04", "Dashboard shows its cards and figures", t_dashboard),
	("BR-05", "Consumer 360: typing an account finds it; every tab opens", t_consumer_360),
	("BR-06", "Cash counter: the scan box takes typing (or the day can be opened)", t_cash_counter),
	("BR-07", "Ask Your Data: the question box takes typing", t_ask_box_takes_typing),
	("BR-08", "Bill Designer: a block dragged from the palette lands on the page", t_designer_drag),
	("BR-09", "New forms open, take typing and raise no script error", t_new_forms),
	("BR-10", "List views open with no script error", t_lists),
	("BR-11", "A posted bill opens", t_open_a_bill),
	("BR-12", "Reports open with no script error", t_reports),
	("BR-13", "Phone width: the main pages don't scroll sideways", t_phone_width),
	("BR-14", "A form shows each banner once, however often it refreshes", t_one_banner_per_message),
)


def main():
	SHOTS.mkdir(parents=True, exist_ok=True)
	results = []
	with sync_playwright() as pw:
		browser = pw.chromium.launch()
		page = browser.new_context(viewport={"width": 1366, "height": 850}).new_page()
		page.set_default_timeout(20000)
		s = Session(page)
		for case, title, fn in CASES:
			try:
				fn(s)
				errors = s.take_errors()
				assert not errors, f"script error: {errors[0][:200]}"
				results.append((case, title, "PASS", ""))
			except Exception as e:
				detail = str(e) if isinstance(e, AssertionError) else traceback.format_exc(limit=1)
				results.append((case, title, "FAIL", detail.strip().replace("\n", " ")[:400]))
				s.take_errors()
				try:
					page.screenshot(path=str(SHOTS / f"{case}.png"))
				except Exception:
					pass
				if case == "BR-01":
					break  # nothing else can run without a session
		browser.close()
	width = max(len(r[1]) for r in results)
	for case, title, status, detail in results:
		print(f"{status}  {case}  {title.ljust(width)}  {detail}")
	failed = [r for r in results if r[2] == "FAIL"]
	print(f"\nbrowser url={URL}  {len(results) - len(failed)} passed, {len(failed)} failed")
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	main()
