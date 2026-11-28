# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""HTTP end-to-end QA suite for utility_billing.

Talks to a running bench over HTTP exactly like the desk does: it logs in, then saves and submits
through frappe.desk.form.save.savedocs with values as text, the way the browser form sends them.

Run (inside WSL, with `bench start` running):
    cd ~/frappe-bench && env/bin/python apps/utility_billing/e2e/run_e2e.py
Environment: E2E_SITE (default qa.localhost), E2E_URL (default http://127.0.0.1:8000),
the admin password is read from QA_ADMIN_PASSWORD in ~/.dev-secrets.
Exit code 0 = all passed.
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import time
import traceback
from pathlib import Path

import requests

SITE = os.environ.get("E2E_SITE", "qa.localhost")
URL = os.environ.get("E2E_URL", "http://127.0.0.1:8000")
RUN = f"{random.randint(1000, 9999)}"  # makes every run's records unique


def secret(key: str) -> str:
	for line in (Path.home() / ".dev-secrets").read_text().splitlines():
		if line.startswith(key + "="):
			return line.split("=", 1)[1]
	raise SystemExit(f"{key} missing in ~/.dev-secrets")


class ServerError(Exception):
	def __init__(self, status, message):
		super().__init__(f"HTTP {status}: {message}")
		self.status = status
		self.message = message


class Desk:
	def __init__(self):
		self.s = requests.Session()
		self.s.headers.update({"Host": SITE, "Accept": "application/json"})

	def login(self, user="Administrator", password=None):
		r = self.s.post(f"{URL}/api/method/login", data={"usr": user, "pwd": password or secret("QA_ADMIN_PASSWORD")})
		r.raise_for_status()

	def call(self, method, **params):
		r = self.s.post(f"{URL}/api/method/{method}", data={k: _enc(v) for k, v in params.items()})
		data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
		if r.status_code != 200:
			raise ServerError(r.status_code, f"{_message(data) or r.text[:300]} [{method}]")
		return data.get("message")

	def save(self, doc: dict, action="Save") -> dict:
		doc = {"docstatus": 0, "__islocal": 1, **doc} if "name" not in doc else doc
		return self.call("frappe.desk.form.save.savedocs", doc=json.dumps(doc), action=action) or self.last_doc(doc)

	def last_doc(self, doc):
		# savedocs returns the saved doc in docs[]; fetch it by name if not.
		return self.get(doc["doctype"], doc["name"])

	def get(self, doctype, name) -> dict:
		r = self.s.get(f"{URL}/api/resource/{doctype}/{requests.utils.quote(name)}")
		if r.status_code != 200:
			raise ServerError(r.status_code, f"{r.text[:300]} [get {doctype} {name}]")
		return r.json()["data"]

	def save_doc(self, doc: dict, action="Save") -> dict:
		"""Save through the desk endpoint and return the stored document.
		Like the browser, an unsaved record carries a temporary name "new-<doctype>-<random>"."""
		if "name" not in doc:
			slug = doc["doctype"].lower().replace(" ", "-")
			doc = {"docstatus": 0, "__islocal": 1, "__unsaved": 1,
			       "name": f"new-{slug}-{random.randint(10**9, 10**10)}", **doc}
		r = self.s.post(
			f"{URL}/api/method/frappe.desk.form.save.savedocs",
			data={"doc": json.dumps(doc), "action": action},
		)
		data = r.json()
		if r.status_code != 200:
			raise ServerError(r.status_code, f"{_message(data)} [save {doc.get('doctype')} {doc.get('name', '')}]")
		saved = data["docs"][0]
		return saved

	def workflow(self, doc: dict, action: str) -> dict:
		"""Apply a workflow action, as the desk's action menu does."""
		fresh = self.get(doc["doctype"], doc["name"])
		r = self.s.post(
			f"{URL}/api/method/frappe.model.workflow.apply_workflow",
			data={"doc": json.dumps(fresh), "action": action},
		)
		data = r.json()
		if r.status_code != 200:
			raise ServerError(r.status_code, _message(data))
		return data["message"]


USERS: dict[str, Desk] = {}
EMAIL: dict[str, str] = {}


def make_user(admin: Desk, key: str, roles: list[str]):
	"""A fixed QA user per role (Frappe allows only 60 new users per hour), given a fresh random
	password and exactly these roles each run; then a session is logged in as them."""
	import secrets

	email = f"qa.{key}@example.com"
	password = secrets.token_urlsafe(16) + "!9a"
	r = admin.s.get(f"{URL}/api/resource/User/{email}")
	if r.status_code == 200:
		user = r.json()["data"]
		user.update({"new_password": password, "enabled": 1, "roles": [{"role": x} for x in roles]})
		admin.save_doc(user)
	else:
		admin.save_doc({"doctype": "User", "email": email, "first_name": f"QA {key}", "send_welcome_email": 0,
		                "new_password": password, "roles": [{"role": x} for x in roles]})
	desk = Desk()
	desk.login(email, password)
	USERS[key] = desk
	EMAIL[key] = email
	return email


def _enc(v):
	return json.dumps(v) if isinstance(v, (dict, list)) else v


def _message(data) -> str:
	msgs = data.get("_server_messages")
	if msgs:
		try:
			return " | ".join(json.loads(m).get("message", "") for m in json.loads(msgs))
		except Exception:
			return str(msgs)
	return data.get("exception") or data.get("exc_type") or ""


# ---------------------------------------------------------------- test registry
CASES: list[tuple[str, str, object]] = []


def case(qa_id, requirement):
	def wrap(fn):
		CASES.append((qa_id, requirement, fn))
		return fn

	return wrap


def expect_error(fn, contains=""):
	try:
		fn()
	except ServerError as e:
		if contains and contains.lower() not in e.message.lower():
			raise AssertionError(f"error raised, but message was: {e.message}") from None
		return e.message
	import sys
	raise AssertionError(f"expected the server to refuse this, but it was accepted (line {sys._getframe(1).f_lineno})")


def eq(actual, expected, what=""):
	if actual != expected:
		raise AssertionError(f"{what}: expected {expected!r}, got {actual!r}")


# ---------------------------------------------------------------- shared state
S: dict = {}


# ---------------------------------------------------------------- increment 1
@case("QA-01", "Install: default utilities")
def t_utilities(d: Desk):
	for name, code in (("Electricity", 1), ("Water", 2), ("Gas", 3)):
		u = d.get("Utility Type", name)
		eq(u["reference_code"], code, f"{name} reference code")


@case("QA-02", "FS-CLT-01/02, FS-CFG-15/16: society saved as the form sends it")
def t_society(d: Desk):
	short = f"qa{RUN}"
	saved = d.save_doc({
		"doctype": "Society", "society_name": f"QA Society {RUN}", "short_name": short,
		"enable_electricity": 1, "enable_fixed_charges": 1, "billing_cycle": "Monthly",
		"reading_window_days": "5", "days_to_due_date": "14", "grace_days": "0",
		"rounding_rule": "Nearest rupee", "payment_posting_order": "Current bill, Arrears, Surcharge",
		"surcharge_basis": "Percentage of current bill", "surcharge_rate": "10",
		"website": "www.qa-society.example", "logo": "/files/qa-logo.png",
		"bank_accounts": [{"bank_name": "MBL", "account_title": "QA", "account_number": "1", "print_on_bill": 1}],
		"offices": [{"office_name": "Main", "office_type": "Office"}],
	})
	eq(saved["name"], short.upper(), "society ID")
	eq(saved["website"], "https://www.qa-society.example", "website")
	eq(float(saved["surcharge_rate"]), 10.0, "surcharge rate")
	S["society"] = saved["name"]


@case("QA-03", "FS-CFG-16: surcharge rate must be 0-100%")
def t_society_bad_rate(d: Desk):
	expect_error(lambda: d.save_doc({"doctype": "Society", "society_name": "Bad", "short_name": f"qb{RUN}",
	                                 "enable_electricity": 1, "days_to_due_date": "14",
	                                 "surcharge_basis": "Percentage of current bill", "surcharge_rate": "150"}),
	             "between 0 and 100")


@case("QA-04", "FS-CLT-02: logo must be an image")
def t_society_bad_logo(d: Desk):
	expect_error(lambda: d.save_doc({"doctype": "Society", "society_name": "Bad logo", "short_name": f"qc{RUN}",
	                                 "enable_electricity": 1, "days_to_due_date": "14",
	                                 "surcharge_basis": "Percentage of current bill", "surcharge_rate": "10",
	                                 "logo": "/files/logo.pdf"}), "logo")


@case("QA-05", "FS-CFG-02: categories per utility")
def t_categories(d: Desk):
	for cat in ("Residential", "Commercial"):
		saved = d.save_doc({"doctype": "Consumer Category", "category_name": f"{cat} {RUN}", "utility": "Electricity"})
		S[cat] = saved["name"]
	eq(S["Residential"], f"Electricity-Residential {RUN}", "category ID")
	S["water_cat"] = d.save_doc({"doctype": "Consumer Category", "category_name": f"Domestic {RUN}", "utility": "Water"})["name"]


@case("QA-06", "FS-CFG-03: charge types")
def t_charges(d: Desk):
	for name, group, basis in (("Service", "Society", "Fixed"), ("O&M", "Electricity", "Per Unit")):
		saved = d.save_doc({"doctype": "Charge Type", "charge_name": f"{name} {RUN}", "charge_group": group,
		                    "calculation_basis": basis, "bill_order": "10", "rounding_rule": "Nearest rupee"})
		S[name] = saved["name"]


def tariff(d, category, start, end=None, method="Flat rate", rate="70", slabs=None, charges=None, action="Submit"):
	"""Billing Manager creates the tariff; with action="Submit" it goes through the approval workflow
	(Billing Manager submits for approval, General Manager approves)."""
	doc = {"doctype": "Tariff", "naming_series": "TRF-.YYYY.-.####", "society": S["society"], "utility": "Electricity",
	       "consumer_category": category, "effective_from": start, "effective_to": end,
	       "pricing_method": method, "flat_rate": rate, "minimum_charge": "0",
	       "slabs": slabs or [], "charges": charges or []}
	saved = USERS["bm"].save_doc(doc)
	if action == "Submit":
		saved = USERS["bm"].workflow(saved, "Submit for Approval")
		saved = USERS["gm"].workflow(saved, "Approve")
	return saved


def preview(d, name, units):
	return d.call("utility_billing.utility_billing.doctype.tariff.tariff.preview_energy", tariff=name, units=units)


@case("QA-07", "FS-CFG-04/06, test case 1: flat Rs 70 tariff, 264 units = 18,480")
def t_tariff_flat(d: Desk):
	t = tariff(d, S["Residential"], "2026-07-01", charges=[{"charge_type": S["Service"], "rate": "1700"}])
	eq(t["docstatus"], 1, "submitted")
	eq(preview(d, t["name"], 264)["amount"], 18480.0, "energy charge")
	S["tariff_v1"] = t


@case("QA-08", "FS-CFG-08: an approved tariff is locked")
def t_tariff_locked(d: Desk):
	t = dict(S["tariff_v1"], flat_rate="75")
	expect_error(lambda: d.save_doc(t, action="Update"))


@case("QA-09", "FS-CFG-05: a new version closes the previous one")
def t_tariff_version(d: Desk):
	v2 = tariff(d, S["Residential"], "2027-01-01", rate="75")
	old = d.get("Tariff", S["tariff_v1"]["name"])
	eq(old["effective_to"], "2026-12-31", "old version end date")
	eq(preview(d, v2["name"], 100)["amount"], 7500.0, "new rate")


@case("QA-10", "FS-CFG-05: overlapping tariffs refused at Submit for Approval")
def t_tariff_overlap(d: Desk):
	expect_error(lambda: tariff(d, S["Residential"], "2026-09-01", "2026-09-30"), "already approved")


@case("QA-11", "FS-CFG-04: progressive and whole-bill slabs")
def t_slabs(d: Desk):
	slabs = [{"from_units": "0", "to_units": "100", "rate": "10"},
	         {"from_units": "100", "to_units": "200", "rate": "15"},
	         {"from_units": "200", "to_units": "0", "rate": "20"}]
	prog = tariff(d, S["Commercial"], "2026-07-01", "2026-12-31", method="Progressive slabs", rate="0", slabs=slabs)
	eq(preview(d, prog["name"], 250)["amount"], 3500.0, "progressive 250 units")
	whole = tariff(d, S["Commercial"], "2027-01-01", method="Whole-bill slabs", rate="0", slabs=slabs)
	eq(preview(d, whole["name"], 250)["amount"], 5000.0, "whole-bill 250 units")


@case("QA-12", "FS-CFG-04: invalid slabs and wrong-utility category refused")
def t_bad_tariffs(d: Desk):
	gap = [{"from_units": "0", "to_units": "100", "rate": "10"}, {"from_units": "120", "to_units": "0", "rate": "20"}]
	expect_error(lambda: tariff(d, S["Commercial"], "2030-01-01", method="Progressive slabs", rate="0", slabs=gap,
	                            action="Save"), "must start at 100")
	expect_error(lambda: tariff(d, S["water_cat"], "2030-01-01", action="Save"), "belongs to Water")


# ---------------------------------------------------------------- increment 2
@case("QA-13", "FS-MST-02: consumer CNIC formatted, duplicates refused")
def t_consumer(d: Desk):
	cnic = f"35202{RUN}{random.randint(100, 999)}1"
	c = d.save_doc({"doctype": "Consumer", "naming_series": "CON-.#####", "consumer_name": f"QA Owner {RUN}",
	                "consumer_type": "Individual", "id_type": "CNIC", "id_number": cnic, "mobile_no": "+923001234567",
	                "preferred_language": "Urdu", "notify_app": 1, "notify_sms": 1})
	eq(c["id_number"], f"{cnic[:5]}-{cnic[5:12]}-{cnic[12]}", "CNIC format")
	S["owner"] = c["name"]
	expect_error(lambda: d.save_doc({"doctype": "Consumer", "naming_series": "CON-.#####", "consumer_name": "Dup",
	                                 "id_type": "CNIC", "id_number": cnic}), "already belongs")
	S["tenant"] = d.save_doc({"doctype": "Consumer", "naming_series": "CON-.#####", "consumer_name": f"QA Tenant {RUN}"})["name"]


@case("QA-14", "FS-MST-01/03: property, owners and tenants")
def t_property(d: Desk):
	p = d.save_doc({"doctype": "Property", "society": S["society"], "property_no": " N  2308 ", "sector": "N",
	                "property_type": "House", "status": "Occupied", "area": "10", "area_unit": "Marla",
	                "occupants": [{"consumer": S["owner"], "occupant_role": "Owner", "from_date": "2020-01-01",
	                               "share_percent": "100", "is_billed": 1, "liable_for_arrears": 1}]})
	eq(p["name"], f"{S['society']}-N 2308", "property ID")
	S["property"] = p
	bad = dict(p, occupants=p["occupants"] + [{"consumer": S["tenant"], "occupant_role": "Tenant",
	                                           "from_date": "2026-01-01", "is_billed": 1}])
	expect_error(lambda: d.save_doc(bad), "only one occupant can be billed")


@case("QA-15", "FS-MST-08: feeder and transformer stay inside their society")
def t_network(d: Desk):
	f = d.save_doc({"doctype": "Feeder", "society": S["society"], "feeder_code": "F1", "feeder_name": "Feeder 1"})
	t = d.save_doc({"doctype": "Transformer", "society": S["society"], "transformer_code": "T1", "feeder": f["name"],
	                "kva_rating": "200"})
	S["transformer"] = t["name"]
	other = d.save_doc({"doctype": "Society", "society_name": f"Other {RUN}", "short_name": f"qo{RUN}", "enable_electricity": 1,
	                    "days_to_due_date": "14", "surcharge_basis": "Percentage of current bill", "surcharge_rate": "10"})
	expect_error(lambda: d.save_doc({"doctype": "Transformer", "society": other["name"], "transformer_code": "T9",
	                                 "feeder": f["name"]}), "belongs to society")


def meter(d, serial, status="In Store"):
	return d.save_doc({"doctype": "Meter", "serial_no": serial, "society": S["society"], "utility": "Electricity",
	                   "meter_type": "Single Phase", "status": status, "digits": "5", "multiplier": "1", "registers": "1"})


def connection(d, meters, status="Active"):
	return d.save_doc({"doctype": "Connection", "society": S["society"], "property": S["property"]["name"],
	                   "consumer": S["owner"], "utility": "Electricity", "consumer_category": S["Residential"],
	                   "status": status, "phase": "Single", "sanctioned_load_kw": "5", "transformer": S["transformer"],
	                   "meters": meters})


@case("QA-16", "FS-MST-06: meters; Installed only through a connection")
def t_meter(d: Desk):
	S["m1"] = meter(d, f"QA-{RUN}-M1")["name"]
	expect_error(lambda: meter(d, f"QA-{RUN}-MX", status="Installed"), "connection")


@case("QA-17", "FS-MST-04/05/07: connection gets a valid reference number; meter installed")
def t_connection(d: Desk):
	c = connection(d, [{"meter": S["m1"], "installed_on": "2026-07-01", "start_reading": "3641"}])
	ref = c["name"]
	eq(len(ref), 10, "reference length")
	eq(ref[0], "1", "electricity code")
	eq(c["current_meter"], S["m1"], "current meter")
	eq(c["feeder"], d.get("Transformer", S["transformer"])["feeder"], "feeder fetched from transformer")
	m = d.get("Meter", S["m1"])
	eq((m["status"], m["current_connection"]), ("Installed", ref), "meter status")
	found = d.call("frappe.desk.search.search_link", doctype="Connection", txt=ref)
	if not any(r.get("value") == ref for r in found or []):
		raise AssertionError(f"reference {ref} not found by search: {found}")
	S["conn"] = c


@case("QA-18", "FS-MST-07: a meter can't be on two connections")
def t_meter_twice(d: Desk):
	expect_error(lambda: connection(d, [{"meter": S["m1"], "installed_on": "2026-08-01"}]), "already installed")


@case("QA-19", "FS-MST-07: meter change")
def t_meter_change(d: Desk):
	m2 = meter(d, f"QA-{RUN}-M2")["name"]
	c = d.get("Connection", S["conn"]["name"])
	c["meters"][0].update({"removed_on": "2026-10-15", "end_reading": "3905", "removal_reason": "Faulty display"})
	c["meters"].append({"meter": m2, "installed_on": "2026-10-15", "start_reading": "0"})
	saved = d.save_doc(c)
	eq(saved["current_meter"], m2, "new current meter")
	eq(d.get("Meter", S["m1"])["status"], "Removed", "old meter status")
	eq(d.get("Meter", m2)["status"], "Installed", "new meter status")


@case("QA-20", "FS-MST-04: an active connection needs a meter")
def t_active_without_meter(d: Desk):
	expect_error(lambda: connection(d, [], status="Active"), "needs an installed meter")


@case("QA-21", "FS-MST-05: the reference number can't be changed")
def t_reference_permanent(d: Desk):
	c = d.get("Connection", S["conn"]["name"])
	c["reference_no"] = "1999999990"
	expect_error(lambda: d.save_doc(c), "permanent")


@case("QA-22", "Security: guests can't read billing data")
def t_guest(d: Desk):
	guest = requests.get(f"{URL}/api/resource/Society/{S['society']}", headers={"Host": SITE, "Accept": "application/json"})
	if guest.status_code not in (401, 403):
		raise AssertionError(f"guest got HTTP {guest.status_code}")


# ---------------------------------------------------------------- increment 3: roles and maker-checker
def pending_tariff(start):
	t = tariff(None, S["Residential"], start, f"{start[:4]}-12-31", action="Save")
	return USERS["bm"].workflow(t, "Submit for Approval")


@case("QA-23", "FS-ACC-05: whoever submitted for approval can't approve, even with the approver role")
def t_maker_checker(d: Desk):
	t = tariff(None, S["Residential"], "2031-01-01", "2031-12-31", action="Save")
	t = USERS["bmgm"].workflow(t, "Submit for Approval")
	expect_error(lambda: USERS["bmgm"].workflow(t, "Approve"), "another user")
	eq(d.get("Tariff", t["name"])["workflow_state"], "Pending Approval", "still pending")
	approved = USERS["gm"].workflow(t, "Approve")
	eq((approved["docstatus"], approved["approved_by"]), (1, EMAIL["gm"]), "approved by GM")


@case("QA-24", "FS-CFG-06: pending is locked; reject needs a reason; maker corrects and resubmits")
def t_reject_cycle(d: Desk):
	t = pending_tariff("2032-01-01")
	eq(t["requested_by"], EMAIL["bm"], "requested by")
	expect_error(lambda: USERS["bm"].save_doc(dict(USERS["bm"].get("Tariff", t["name"]), flat_rate="99")),
	             "waiting for approval")
	expect_error(lambda: USERS["gm"].workflow(t, "Reject"), "reason")
	USERS["gm"].call("utility_billing.utility_billing.doctype.tariff.tariff.set_rejection_reason",
	                 tariff=t["name"], reason="Rate must follow the July notice")
	rejected = USERS["gm"].workflow(t, "Reject")
	eq((rejected["workflow_state"], rejected["rejection_reason"]), ("Rejected", "Rate must follow the July notice"),
	   "rejected with reason")
	edited = USERS["bm"].save_doc(dict(USERS["bm"].get("Tariff", t["name"]), flat_rate="72"))
	again = USERS["bm"].workflow(edited, "Submit for Approval")
	# The API leaves out empty fields, so a cleared reason is simply absent.
	eq((again["workflow_state"], again.get("rejection_reason")), ("Pending Approval", None), "resubmitted")
	stored = USERS["bm"].get("Tariff", t["name"])
	history = [(r["action"], r.get("remarks")) for r in stored["approval_log"]]
	eq(history, [("Submit for Approval", None), ("Reject", "Rate must follow the July notice"),
	             ("Resubmit", "~ Rate per Unit: 70 → 72")], "approval history with change summary")
	eq(stored.get("changes_since_last_submission"), "~ Rate per Unit: 70 → 72", "changes shown to the approver")
	import time

	# notifications are created by a background job; during the regression gate the workers are busy, and the notice came after
	# 21 s on 2026-11-25 (and failed at 20 s on 2026-11-21), so up to 60 s
	for _ in range(60):
		notes = USERS["bm"].s.get(f"{URL}/api/resource/Notification Log", params={
			"filters": json.dumps([["document_name", "=", t["name"]]]), "fields": json.dumps(["subject"])}).json()["data"]
		if any("rejected" in n["subject"].lower() for n in notes):
			break
		time.sleep(1)
	else:
		raise AssertionError(f"maker not notified of rejection within 60 s: {notes}")


@case("QA-28", "Best practice: the submitter can withdraw a pending item; others can't")
def t_withdraw(d: Desk):
	t = pending_tariff("2034-01-01")
	expect_error(lambda: USERS["bmgm"].workflow(t, "Withdraw"), "only")
	back = USERS["bm"].workflow(t, "Withdraw")
	eq(back["workflow_state"], "Draft", "withdrawn to draft")
	edited = USERS["bm"].save_doc(dict(USERS["bm"].get("Tariff", t["name"]), flat_rate="73"))
	eq(float(edited["flat_rate"]), 73.0, "editable after withdraw")


@case("QA-25", "FS-ACC-06: the approver's inbox lists the pending tariff")
def t_inbox(d: Desk):
	import time

	t = pending_tariff("2033-01-01")
	gm_email = EMAIL["gm"]
	for _ in range(20):  # workflow actions are created by a background job
		r = USERS["gm"].s.get(f"{URL}/api/resource/Workflow Action", params={
			"filters": json.dumps([["reference_name", "=", t["name"]], ["status", "=", "Open"]]),
			"fields": json.dumps(["name", "reference_name"])})
		if r.status_code == 200 and r.json().get("data"):
			return
		time.sleep(1)
	raise AssertionError(f"no open Workflow Action for {t['name']} visible to {gm_email}")


@case("QA-26", "FS-ACC-03: roles only get the actions they need")
def t_role_rights(d: Desk):
	expect_error(lambda: USERS["bo"].save_doc({
		"doctype": "Tariff", "naming_series": "TRF-.YYYY.-.####", "society": S["society"], "utility": "Electricity",
		"consumer_category": S["Residential"], "effective_from": "2040-01-01", "pricing_method": "Flat rate",
		"flat_rate": "1"}))
	consumer = USERS["cashier"].get("Consumer", S["owner"])  # cashier can read consumers
	expect_error(lambda: USERS["cashier"].save_doc(dict(consumer, mobile_no="+923000000000")))
	expect_error(lambda: USERS["gm"].save_doc({
		"doctype": "Tariff", "naming_series": "TRF-.YYYY.-.####", "society": S["society"], "utility": "Electricity",
		"consumer_category": S["Residential"], "effective_from": "2041-01-01", "pricing_method": "Flat rate",
		"flat_rate": "1"}))  # the approver can't create (keeps maker and checker apart)


@case("QA-27", "FS-ACC-08: approval steps are in the audit trail")
def t_audit(d: Desk):
	name = S["tariff_v1"]["name"]
	r = d.s.get(f"{URL}/api/resource/Version", params={
		"filters": json.dumps([["ref_doctype", "=", "Tariff"], ["docname", "=", name]]),
		"fields": json.dumps(["name", "owner", "data"]), "limit_page_length": 50})
	versions = r.json()["data"]
	text = " ".join(v["data"] for v in versions)
	for word in ("Pending Approval", "Approved"):
		if word not in text:
			raise AssertionError(f"'{word}' missing from {len(versions)} versions of {name}")


# ---------------------------------------------------------------- increment 4: meter readings
RDG = "utility_billing.utility_billing.readings"


def my_reading(d):
	r = d.s.get(f"{URL}/api/resource/Meter Reading", params={
		"filters": json.dumps([["reading_cycle", "=", S["cycle"]], ["connection", "=", S["conn"]["name"]]]),
		"fields": json.dumps(["name"])})
	rows = r.json()["data"]
	if not rows:
		raise AssertionError("no reading for the test connection in the cycle")
	return d.get("Meter Reading", rows[0]["name"])


@case("QA-29", "FS-RDG-01: Billing Officer opens a reading cycle and creates the reading sheet")
def t_cycle(d: Desk):
	c = USERS["bo"].save_doc({"doctype": "Reading Cycle", "society": S["society"], "utility": "Electricity",
	                          "billing_month": "2026-11-10"})
	eq(c["name"], f"{S['society']}-Electricity-2026-11", "cycle name")
	eq(c["billing_month"], "2026-11-01", "month stored as the 1st")
	S["cycle"] = c["name"]
	res = USERS["bo"].call(f"{RDG}.create_reading_sheet", cycle=c["name"])
	if res["total"] < 1:
		raise AssertionError(f"no readings created: {res}")
	r = my_reading(USERS["bo"])
	eq((r["status"], r["meter"], float(r["previous_reading"])), ("Pending", f"QA-{RUN}-M2", 0.0), "pending reading on the new meter")


@case("QA-30", "FS-RDG-05/07: zero use is flagged; the reader can't approve; the officer corrects")
def t_reader_flag(d: Desk):
	r = my_reading(USERS["reader"])
	saved = USERS["reader"].save_doc(dict(r, present_reading="0", reading_date="2026-11-05"))
	eq(saved["status"], "Flagged", "zero use flagged")
	expect_error(lambda: USERS["reader"].call(f"{RDG}.approve_reading", name=r["name"], note="Looks fine to me"))
	USERS["bo"].call(f"{RDG}.correct_reading", name=r["name"], present_reading="264", reason="Reader entered 0 by mistake")
	fixed = USERS["bo"].get("Meter Reading", r["name"])
	eq((fixed["status"], float(fixed["units"]), float(fixed["corrected_from"])), ("Valid", 264.0, 0.0), "corrected")


@case("QA-31", "FS-RDG-04: readings uploaded from a CSV file get the same checks")
def t_upload(d: Desk):
	csv = "\n".join([
		"Reference No,Present Reading,Reading Date",
		f"{S['conn']['name']},300,2026-11-06",
		"1999999999,5,2026-11-06",
	]) + "\n"
	up = USERS["bo"].s.post(f"{URL}/api/method/upload_file", files={"file": ("readings.csv", csv.encode())},
	                        data={"is_private": 1, "doctype": "Reading Cycle", "docname": S["cycle"]})
	if up.status_code != 200:
		raise AssertionError(f"upload failed: HTTP {up.status_code} {up.text[:200]}")
	res = USERS["bo"].call(f"{RDG}.upload_readings", cycle=S["cycle"], file_url=up.json()["message"]["file_url"])
	eq((res["updated"], [e["row"] for e in res["errors"]]), (1, [3]), "one row applied, unknown reference reported")
	r = my_reading(USERS["bo"])
	eq((float(r["units"]), r["source"], r["status"]), (300.0, "Import", "Valid"), "imported reading")


@case("QA-32", "FS-RDG-01: closing the cycle locks its readings")
def t_close(d: Desk):
	USERS["bo"].call(f"{RDG}.close_cycle", cycle=S["cycle"])
	eq(USERS["bo"].get("Reading Cycle", S["cycle"])["status"], "Closed", "closed")
	r = my_reading(USERS["reader"])
	expect_error(lambda: USERS["reader"].save_doc(dict(r, present_reading="310")), "closed")


# ---------------------------------------------------------------- increment 5: adjustments, bills, bill runs
BIL = "utility_billing.utility_billing.billing"


@case("QA-33", "FS-CFG-09: FPA for November defaults to September units; GM approves")
def t_fpa(d: Desk):
	for name, basis, order in ((f"Energy {RUN}", "Per Unit", 10), (f"FPA {RUN}", "Per Unit", 20)):
		d.save_doc({"doctype": "Charge Type", "charge_name": name, "charge_group": "Electricity", "calculation_basis": basis,
		            "bill_order": str(order), "bill_label": name.split(" ")[0] + " Charges", "rounding_rule": "Nearest rupee"})
	soc = d.get("Society", S["society"])
	d.save_doc(dict(soc, energy_charge_type=f"Energy {RUN}", charges_fpa=1))
	# increment 52: before the FPA is approved, Calculate is refused once for the run, saying what to do (it used to list every account)
	early = USERS["bo"].save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": S["society"],
	                              "utility": "Electricity", "billing_month": "2026-11-01", "issue_date": "2026-12-01"})
	msg = expect_error(lambda: USERS["bo"].call(f"{BIL}.calculate_run", run=early["name"]), "No approved FPA for November 2026")
	eq(("a rate of 0" in msg, USERS["bo"].get("Bill Run", early["name"])["calculation_status"] != "Calculating"), (True, True),
	   "the refusal says how to go on, and the run isn't left calculating")
	d.s.post(f"{URL}/api/method/frappe.client.delete", data={"doctype": "Bill Run", "name": early["name"]}).raise_for_status()
	adj = USERS["bm"].save_doc({"doctype": "Tariff Adjustment", "naming_series": "ADJ-.YYYY.-.####", "adjustment_type": "FPA",
	                            "society": S["society"], "utility": "Electricity", "billing_month": "2026-11-15",
	                            "rate_per_unit": "1.30", "charge_type": f"FPA {RUN}"})
	eq((adj["billing_month"], adj["reference_month"]), ("2026-11-01", "2026-09-01"), "reference month = billing month - 2")
	adj = USERS["bm"].workflow(adj, "Submit for Approval")
	adj = USERS["gm"].workflow(adj, "Approve")
	eq((adj["docstatus"], adj["workflow_state"]), (1, "Approved"), "FPA approved")


@case("QA-34", "FS-RUN-01/02/05: Billing Officer calculates the November run")
def t_run_calculate(d: Desk):
	run = USERS["bo"].save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": S["society"],
	                            "utility": "Electricity", "billing_month": "2026-11-01", "issue_date": "2026-12-01"})
	eq(run["due_date"], "2026-12-15", "due date = issue + 14 days")
	USERS["bo"].call(f"{BIL}.calculate_run", run=run["name"])
	run = USERS["bo"].get("Bill Run", run["name"])
	eq((run["calculation_status"], run["bill_count"]), ("Calculated", 1), "one bill calculated")
	p = USERS["bo"].call(f"{BIL}.calculation_progress", run=run["name"])
	eq((p["state"], p["done"], p["total"]), ("Done", 1, 1), "the calculation's progress, as the form's bar reads it (increment 52)")
	S["run"] = run
	bills = USERS["bo"].s.get(f"{URL}/api/resource/Utility Bill", params={
		"filters": json.dumps([["bill_run", "=", run["name"]]]), "fields": json.dumps(["name"])}).json()["data"]
	bill = USERS["bo"].get("Utility Bill", bills[0]["name"])
	lines = [(l["label"], float(l["amount"])) for l in sorted(bill["lines"], key=lambda l: l["idx"])]
	# 300 units x 70; FPA on September units (no reading on the new meter that month -> 0, explained); Service 1,700
	# Lines follow each charge type's Order on Bill: Service was set to 10 (same as Energy) in QA-06, FPA is 20.
	eq(lines, [("Energy Charges", 21000.0), (f"Service {RUN}", 1700.0), ("FPA Charges", 0.0)], "bill lines")
	fpa = next(l for l in bill["lines"] if l["label"] == "FPA Charges")
	eq(fpa.get("note"), "No reading for September 2026", "FPA explained")
	eq((float(bill["current_amount"]), float(bill["surcharge_amount"]), float(bill["payable_after_due"])),
	   (22700.0, 2270.0, 24970.0), "totals with 10% surcharge on the current bill")
	S["bill"] = bill["name"]
	run = USERS["bo"].save_doc(dict(USERS["bo"].get("Bill Run", run["name"]), issue_date="2026-12-02"))
	eq(run["calculation_status"], "Not calculated", "changing the issue date needs recalculation")
	expect_error(lambda: USERS["bo"].workflow(run, "Submit for Approval"), "calculate")
	USERS["bo"].call(f"{BIL}.calculate_run", run=run["name"])


@case("QA-35", "FS-ACC-05: Billing Officer submits, Billing Manager approves, bills are posted")
def t_run_post(d: Desk):
	run = USERS["bo"].workflow(S["run"], "Submit for Approval")
	expect_error(lambda: USERS["bo"].workflow(run, "Approve"))
	run = USERS["bm"].workflow(run, "Approve")
	eq((run["docstatus"], run["workflow_state"]), (1, "Approved"), "run approved")
	bill = USERS["bm"].get("Utility Bill", S["bill"])
	eq((bill["docstatus"], bill["status"], float(bill["outstanding_amount"])), (1, "Posted", 22700.0), "bill posted")
	# increment 54: the posting's progress, as the form's progress bar asks for it
	p = USERS["bo"].call("utility_billing.utility_billing.doctype.bill_run.bill_run.posting_progress", run=run["name"])
	eq((p["state"], p["done"], p["total"]), ("Done", run["bill_count"], run["bill_count"]), "posting progress")


PRG = "utility_billing.utility_billing.progress"


def run_job(desk, job, args, title="e2e", wait=180):
	"""Start a background job the way the screen's button does, then ask how far it is every second, as the progress panel does."""
	started = desk.call(f"{PRG}.start", job=job, args=json.dumps(args), title=title)
	for _ in range(wait):
		p = desk.call(f"{PRG}.job_progress", job=job, record=started["record"])
		if p and p["state"] in ("Done", "Failed"):
			return p
		time.sleep(1)
	raise AssertionError(f"{job} still not finished after {wait} s: {p}")


# Placed after QA-35 so the run is posted (and not yet reversed) when its bills are printed.
@case("QA-105", "Increment 55: buttons that ran with a greyed screen run in the background with a progress bar: Print Bills, Reconcile")
def t_background_jobs(d: Desk):
	bm, run = USERS["bm"], S["run"]["name"]
	p = run_job(bm, "print_bills", {"run": run, "block": "", "route": ""}, "Printing")
	eq((p["state"], p["by"], p["result"]["bills"]), ("Done", EMAIL["bm"], 1), "printed in the background")
	pdf = bm.s.get(f"{URL}{p['result']['file_url']}")
	eq((pdf.status_code, pdf.content[:4]), (200, b"%PDF"), "the PDF opens for the user who printed it")
	guest = requests.Session()
	eq(guest.get(f"{URL}{p['result']['file_url']}").status_code in (403, 404), True, "the PDF is private")
	p = run_job(USERS["ao"], "gl_reconcile", {"society": S["society"]}, "Reconcile")
	eq((p["state"], "status" in p["result"]), ("Done", True), "reconciliation in the background")
	expect_error(lambda: bm.call(f"{PRG}.start", job="frappe.client.delete", args=json.dumps({"doctype": "User"})), "can't run in the background")
	p = run_job(USERS["cashier"], "gl_reconcile", {"society": S["society"]})
	eq((p["state"], "result" in p), ("Failed", False), "the function's own refusal reaches the screen")


@case("QA-36", "FS-BIL-10: a posted bill can't be changed or cancelled on its own")
def t_bill_locked(d: Desk):
	bill = d.get("Utility Bill", S["bill"])
	expect_error(lambda: d.save_doc(dict(bill, current_amount="1"), action="Update"))
	r = d.s.post(f"{URL}/api/method/frappe.client.cancel", data={"doctype": "Utility Bill", "name": S["bill"]})
	if r.status_code == 200:
		raise AssertionError("a posted bill was cancelled directly")
	eq(USERS["cashier"].get("Utility Bill", S["bill"])["status"], "Posted", "cashier can read the posted bill")


# ---------------------------------------------------------------- increment 6: bill print
PRT = "utility_billing.utility_billing.bill_print"


def get_pdf(desk, method, **params):
	"""GET a PDF the way the browser's Print buttons open it; returns (reader, text of all pages).
	pypdf returns the gaps between words as tabs or line breaks, so the text is joined with single spaces."""
	from pypdf import PdfReader

	r = desk.s.get(f"{URL}/api/method/{PRT}.{method}", params=params)
	if r.status_code != 200 or not r.content.startswith(b"%PDF"):
		data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
		raise ServerError(r.status_code, _message(data) or r.text[:300])
	reader = PdfReader(io.BytesIO(r.content))
	return reader, " ".join(" ".join(p.extract_text() for p in reader.pages).split())


@case("QA-37", "FS-CFG-19: bill layout settings saved from the form; history months limited to 1-24")
def t_bill_layout(d: Desk):
	soc = d.get("Society", S["society"])
	saved = d.save_doc(dict(soc, bill_history_months="6", print_office_copy="1", show_qr_code="1",
	                        bill_notices="Pay before the due date.\nLoad shedding schedule at the office.",
	                        payment_instructions="Pay at any bank branch or through 1Link."))
	eq((saved["bill_history_months"], saved["print_office_copy"]), (6, 1), "layout saved as numbers")
	expect_error(lambda: d.save_doc(dict(d.get("Society", S["society"]), bill_history_months="30")), "1 to 24")


@case("QA-38", "FS-BIL-08/16, FS-CLT-03: cashier downloads the posted bill as a one-page PDF with every part")
def t_bill_pdf(d: Desk):
	bill = d.get("Utility Bill", S["bill"])
	ref = d.get("Connection", bill["connection"])["reference_no"]
	reader, text = get_pdf(USERS["cashier"], "download_bill", name=S["bill"])
	eq(len(reader.pages), 1, "pages")
	for part in (ref, S["bill"], "Electricity Bill", "November 2026", "Consumer copy", "Office copy", "22,700", "24,970",
	             "Energy Charges", "21,000", "Payable within due date", "Payable after due date"):
		if part not in text:
			raise AssertionError(f"{part!r} missing from the bill PDF")
	if "DUPLICATE" in text or "DRAFT" in text:
		raise AssertionError("an original posted bill must not be marked")
	S["bill_text"] = text


@case("QA-39", "FS-BIL-17: a duplicate is issued (recorded), then printed marked DUPLICATE")
def t_duplicate(d: Desk):
	expect_error(lambda: get_pdf(USERS["cashier"], "download_bill", name=S["bill"], duplicate="1"), "issue the duplicate")
	r = USERS["cashier"].s.get(f"{URL}/api/method/{PRT}.issue_duplicate", params={"name": S["bill"]})
	if r.status_code == 200:
		raise AssertionError("issuing a duplicate must need a POST, not a link")
	n = USERS["cashier"].call(f"{PRT}.issue_duplicate", name=S["bill"])
	eq(n, 1, "first duplicate")
	_, text = get_pdf(USERS["cashier"], "download_bill", name=S["bill"], duplicate=str(n))
	if "DUPLICATE 1" not in text:
		raise AssertionError("duplicate not marked")
	eq(d.get("Utility Bill", S["bill"])["duplicate_count"], 1, "duplicate recorded on the bill")


@case("QA-40", "FS-RUN-08: Billing Officer prints the run's bills in one PDF, by block")
def t_bulk_print(d: Desk):
	run = S["run"]["name"]
	groups = USERS["bo"].call(f"{PRT}.run_print_groups", run=run)
	eq(groups["total"], 1, "bills in the run")
	reader, text = get_pdf(USERS["bo"], "print_run_bills", run=run)
	eq(len(reader.pages), 1, "one page per bill")
	block = next(iter(groups["blocks"]))
	get_pdf(USERS["bo"], "print_run_bills", run=run, block=block)
	expect_error(lambda: get_pdf(USERS["bo"], "print_run_bills", run=run, block="No Such Block"), "no bills")


@case("QA-41", "FS-RUN-09: after the society changes its details, the posted bill reprints exactly as issued")
def t_reprint_exact(d: Desk):
	soc = d.get("Society", S["society"])
	d.save_doc(dict(soc, society_name=f"Renamed QA Society {RUN}", bill_notices="A new notice", print_office_copy="0"))
	_, text = get_pdf(USERS["cashier"], "download_bill", name=S["bill"])
	eq(text, S["bill_text"], "reprint")


@case("QA-42", "Security: a visitor who isn't signed in can't download bills")
def t_guest_pdf(d: Desk):
	guest = Desk()
	expect_error(lambda: get_pdf(guest, "download_bill", name=S["bill"]))
	expect_error(lambda: get_pdf(guest, "print_run_bills", run=S["run"]["name"]))


# ---------------------------------------------------------------- increment 7: customer account and payments
ACC = "utility_billing.utility_billing.account"
DAY = "utility_billing.utility_billing.doctype.cashier_day.cashier_day"
APR = "utility_billing.utility_billing.account_print"


def owed(desk):
	return USERS["bo"].call(f"{ACC}.account_summary", connection=S["conn"]["name"])


def latest_bill(conn):
	"""The account's bill now: QA-69's due date extension and QA-71's installment plan reissue it (increment 37)."""
	return USERS["cashier"].call(f"{ACC}.enquiry", reference_no=USERS["bo"].get("Connection", conn)["reference_no"])["bill"]


@case("QA-43", "FS-CFG-24/26: fee schedule, duplicate bill fee, cash counter; default reason codes present")
def t_fee_setup(d: Desk):
	fee = d.save_doc({"doctype": "Fee Type", "society": S["society"], "fee_name": "Duplicate Bill", "amount": "100"})
	eq((fee["name"], float(fee["amount"])), (f"{S['society']}-Duplicate Bill", 100.0), "fee type")
	soc = d.get("Society", S["society"])
	d.save_doc(dict(soc, duplicate_bill_fee=fee["name"]))
	S["counter"] = f"Q{RUN}"
	c = d.save_doc({"doctype": "Cash Counter", "counter_code": S["counter"].lower(), "counter_name": "QA counter",
	                "society": S["society"]})
	eq(c["name"], S["counter"], "counter code upper-cased")
	codes = d.s.get(f"{URL}/api/resource/Reason Code", params={"limit_page_length": 50}).json()["data"]
	for code in ("CHQ-BOUNCE", "SUR-WAIVE", "FEE-ERROR"):
		if code not in {x["name"] for x in codes}:
			raise AssertionError(f"default reason code {code} missing")


@case("QA-44", "FS-PAY-03: the cashier opens a day at the counter; a second open day is refused")
def t_open_day(d: Desk):
	day = USERS["cashier"].save_doc({"doctype": "Cashier Day", "naming_series": "CDAY-.YYYY.-.#####",
	                                 "cash_counter": S["counter"], "opening_float": "500"})
	eq((day["status"], day["cashier"], float(day["opening_float"])), ("Open", EMAIL["cashier"], 500.0), "day opened")
	S["day"] = day["name"]
	expect_error(lambda: USERS["cashier"].save_doc({"doctype": "Cashier Day", "naming_series": "CDAY-.YYYY.-.#####",
	                                                "cash_counter": S["counter"]}), "already has an open day")


@case("QA-45", "FS-PAY-01/05/07/13: enquiry, part payment at the counter, numbered receipt, receipt PDF")
def t_counter_receipt(d: Desk):
	e = USERS["cashier"].call(f"{ACC}.enquiry", reference_no=S["conn"]["name"])
	eq((e["bill"], e["payable_within_due"], e["amount_due"]), (S["bill"], 22700.0, 22700.0), "enquiry")
	r = USERS["cashier"].save_doc({"doctype": "Payment Receipt", "payment_channel": "Counter",
	                               "reference_no": S["conn"]["name"], "amount": "20000", "mode": "Cash"})
	r = USERS["cashier"].save_doc(r, action="Submit")
	eq((r["name"], r["status"], r["cashier_day"]), (f"{S['counter']}-00001", "Posted", S["day"]), "receipt numbered per counter")
	eq((float(r["alloc_current"]), float(r["balance_after"])), (20000.0, 2700.0), "applied to the current bill")
	S["receipt1"] = r["name"]
	reader, text = get_pdf_at(USERS["cashier"], f"{APR}.download_receipt", name=r["name"])
	for part in ("Payment Receipt", r["name"], "20,000", S["conn"]["name"], "Twenty Thousand"):
		if part not in text:
			raise AssertionError(f"{part!r} missing from the receipt PDF")


@case("QA-46", "FS-PAY-02/07: cheque needs its number; an overpayment is kept as credit")
def t_cheque(d: Desk):
	base = {"doctype": "Payment Receipt", "payment_channel": "Counter", "reference_no": S["conn"]["name"],
	        "amount": "3000", "mode": "Cheque"}
	expect_error(lambda: USERS["cashier"].save_doc(dict(base)), "cheque number")
	r = USERS["cashier"].save_doc(dict(base, instrument_no="778899", bank_name="MBL"))
	r = USERS["cashier"].save_doc(r, action="Submit")
	eq((r["name"], float(r["alloc_current"]), float(r["alloc_credit"]), float(r["balance_after"])),
	   (f"{S['counter']}-00002", 2700.0, 300.0, -300.0), "cheque cleared the bill, Rs 300 credit")
	S["cheque"] = r["name"]


@case("QA-47", "FS-PAY-12: only a Billing Manager reverses a payment, with a reason of the right kind")
def t_reverse(d: Desk):
	args = dict(doctype="Payment Receipt", name=S["cheque"], reason_code="CHQ-BOUNCE", remarks="Cheque returned unpaid")
	expect_error(lambda: USERS["cashier"].call(f"{ACC}.reverse_document", **args))
	expect_error(lambda: USERS["bm"].call(f"{ACC}.reverse_document", **dict(args, reason_code="SUR-WAIVE")), "isn't an enabled reason")
	r = USERS["cashier"].s.post(f"{URL}/api/method/frappe.client.cancel", data={"doctype": "Payment Receipt", "name": S["cheque"]})
	if r.status_code == 200:
		raise AssertionError("the cashier cancelled a receipt directly")
	USERS["bm"].call(f"{ACC}.reverse_document", **args)
	rc = USERS["bm"].get("Payment Receipt", S["cheque"])
	eq((rc["status"], rc["reversal_reason"], rc["reversed_by"]), ("Reversed", "CHQ-BOUNCE", EMAIL["bm"]), "reversed")
	eq(owed(d)["balance"], 2700.0, "the reversed cheque is owed again")


@case("QA-48", "FS-PAY-06: a bank/channel transaction posts once; cashiers can't record channel payments")
def t_channel(d: Desk):
	from datetime import date

	base = {"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": S["conn"]["name"],
	        "amount": "700", "mode": "1Link", "payment_date": date.today().isoformat(), "channel_txn_id": f"1L-{RUN}"}
	expect_error(lambda: USERS["cashier"].save_doc(dict(base)))
	r = USERS["ao"].save_doc(dict(base))
	r = USERS["ao"].save_doc(r, action="Submit")
	eq((r["name"][:4], float(r["balance_after"])), ("BNK-", 2000.0), "channel payment posted")
	expect_error(lambda: USERS["ao"].save_doc(dict(base, amount="5")), "already posted")


@case("QA-49", "FS-PAY-03: close the day with cash count and deposit slip; no receipts after; supervisor approves")
def t_close_day(d: Desk):
	day = USERS["cashier"].get("Cashier Day", S["day"])
	eq((day["receipt_count"], float(day["cash_total"]), float(day["reversed_total"]), float(day["expected_cash"])),
	   (2, 20000.0, 3000.0, 20500.0), "day totals")
	USERS["cashier"].call(f"{DAY}.close_day", name=S["day"], cash_counted="20500", deposit_slip_no=f"DS-{RUN}")
	expect_error(lambda: USERS["cashier"].save_doc({"doctype": "Payment Receipt", "payment_channel": "Counter",
	                                                "reference_no": S["conn"]["name"], "amount": "10", "mode": "Cash"}),
	             "open your cashier day")
	expect_error(lambda: USERS["cashier"].call(f"{DAY}.approve_day", name=S["day"]))
	USERS["ao"].call(f"{DAY}.approve_day", name=S["day"])
	day = USERS["ao"].get("Cashier Day", S["day"])
	eq((day["status"], float(day["difference"]), day["approved_by"]), ("Approved", 0.0, EMAIL["ao"]), "day approved")


@case("QA-50", "FS-BIL-17/CFG-24: a duplicate bill charges the society's duplicate bill fee")
def t_duplicate_fee(d: Desk):
	USERS["cashier"].call(f"{PRT}.issue_duplicate", name=S["bill"])
	s = owed(d)
	eq((s["balances"]["fees"], s["balance"]), (100.0, 2100.0), "fee added to the account")


@case("QA-51", "FS-REC-16: a credit note waives the fee after General Manager approval")
def t_credit_note(d: Desk):
	note = USERS["bo"].save_doc({"doctype": "Account Adjustment", "note_type": "Credit Note", "connection": S["conn"]["name"],
	                             "against_bill": S["bill"], "bucket": "Fees", "amount": "100", "reason_code": "BILL-ERROR",
	                             "remarks": "Duplicate fee charged by mistake"})
	if not note["name"].startswith("CN-"):
		raise AssertionError(f"credit note numbered {note['name']}")
	eq(owed(d)["balances"]["fees"], 100.0, "nothing posted before approval")
	note = USERS["bo"].workflow(note, "Submit for Approval")
	expect_error(lambda: USERS["bo"].workflow(note, "Approve"))
	note = USERS["gm"].workflow(note, "Approve")
	eq((note["docstatus"], owed(d)["balances"]["fees"], owed(d)["balance"]), (1, 0.0, 2000.0), "fee waived")


@case("QA-52", "FS-REC-01/12/14: statement PDF with running balance; ledger entries can't be edited")
def t_statement(d: Desk):
	_, text = get_pdf_at(USERS["bo"], f"{APR}.download_statement", connection=S["conn"]["name"])
	for part in ("Account Statement", "Opening balance", f"{S['counter']}-00001", "Reversal", "Credit note",
	             "Closing balance", "2,000"):
		if part not in text:
			raise AssertionError(f"{part!r} missing from the statement")
	rows = d.s.get(f"{URL}/api/resource/Customer Ledger Entry", params={
		"filters": json.dumps([["connection", "=", S["conn"]["name"]]]), "fields": json.dumps(["name", "seq"]),
		"order_by": "seq asc", "limit_page_length": 50}).json()["data"]
	eq([r["seq"] for r in rows], list(range(1, len(rows) + 1)), "entries numbered in sequence")
	entry = d.get("Customer Ledger Entry", rows[-1]["name"])
	expect_error(lambda: d.save_doc(dict(entry, amount="1")))


def get_pdf_at(desk, method, **params):
	"""Like get_pdf, for any whitelisted method path."""
	from pypdf import PdfReader

	r = desk.s.get(f"{URL}/api/method/{method}", params=params)
	if r.status_code != 200 or not r.content.startswith(b"%PDF"):
		data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
		raise ServerError(r.status_code, _message(data) or r.text[:300])
	reader = PdfReader(io.BytesIO(r.content))
	return reader, " ".join(" ".join(p.extract_text() for p in reader.pages).split())


# ---------------------------------------------------------------- increment 8: accounting journals, export, reconciliation
GLM = "utility_billing.utility_billing.gl"


def acct(code):
	return f"Q{RUN}-{code}"


@case("QA-53", "FS-GL-01, CFG-03, CFG-39: chart of accounts, charge type and society mappings, operating model")
def t_accounts(d: Desk):
	for code, name, kind in (("1200", "Customer receivables", "Receivable"), ("1010", "Cash in hand", "Cash"),
	                         ("1020", "MBL current account", "Bank"), ("4100", "Electricity revenue", "Income"),
	                         ("4110", "FPA recovered", "Income"), ("4120", "Service charges", "Income"),
	                         ("4900", "Surcharge income", "Income"), ("4990", "Other billing income", "Income"),
	                         ("5100", "Bill adjustments and waivers", "Expense")):
		USERS["ao"].save_doc({"doctype": "Ledger Account", "account_code": acct(code), "account_name": name, "account_type": kind})
	expect_error(lambda: USERS["cashier"].save_doc({"doctype": "Ledger Account", "account_code": acct("9999"),
	                                                "account_name": "x", "account_type": "Other"}))
	for ct, code in ((f"Energy {RUN}", "4100"), (f"FPA {RUN}", "4110"), (S["Service"], "4120")):
		d.save_doc(dict(d.get("Charge Type", ct), account_code=acct(code)))
	expect_error(lambda: d.save_doc(dict(d.get("Charge Type", S["Service"]), account_code="NO-SUCH-ACCOUNT")))
	soc = d.get("Society", S["society"])
	soc = d.save_doc(dict(soc, receivables_account=acct("1200"), surcharge_income_account=acct("4900"),
	                      other_income_account=acct("4990"), adjustments_account=acct("5100"),
	                      operating_model="Cost allocation", licence_no=f"NEPRA-EX-{RUN}",
	                      payment_accounts=[{"mode": "Cash", "account": acct("1010")},
	                                        {"mode": "Cheque", "account": acct("1020")},
	                                        {"mode": "1Link", "account": acct("1020")}]))
	eq((soc["operating_model"], len(soc["payment_accounts"])), ("Cost allocation", 3), "society accounting settings")


@case("QA-54", "FS-GL-02: Accounts Officer makes journals: the November bill run and today's transactions")
def t_make_journals(d: Desk):
	expect_error(lambda: USERS["cashier"].call(f"{GLM}.make_journals", society=S["society"]))
	made = USERS["ao"].call(f"{GLM}.make_journals", society=S["society"])
	if len(made) != 2:
		raise AssertionError(f"expected the bill run journal and one daily journal, got {made}")
	run = USERS["ao"].get("Bill Run", S["run"]["name"])
	j = USERS["ao"].get("GL Journal", run["gl_journal"])
	lines = {l["account"]: float(l["debit"]) - float(l["credit"]) for l in j["lines"]}
	eq(lines, {acct("1200"): 22700.0, acct("4100"): -21000.0, acct("4120"): -1700.0}, "bill run journal")
	daily = USERS["ao"].get("GL Journal", [m for m in made if m != run["gl_journal"]][0])
	lines = {l["account"]: float(l["debit"]) - float(l["credit"]) for l in daily["lines"]}
	# cash 20,000 + 1Link 700 received (the cheque and its reversal cancel out); fee 100 and its credit note
	eq(lines, {acct("1010"): 20000.0, acct("1020"): 700.0, acct("1200"): -20700.0, acct("4990"): -100.0, acct("5100"): 100.0},
	   "daily journal")
	eq((daily["journal_type"], float(daily["total_debit"]), float(daily["total_credit"])),
	   ("Daily Transactions", 20800.0, 20800.0), "balanced")
	S["journals"] = made
	eq(USERS["ao"].call(f"{GLM}.make_journals", society=S["society"]), [], "nothing left to journal")


@case("QA-55", "FS-GL-04, REC-13: receivables in the journals match the customer accounts")
def t_reconcile(d: Desk):
	r = USERS["ao"].call(f"{GLM}.reconcile_now", society=S["society"])
	eq((r["status"], r["customer_accounts_total"], r["receivables_posted"], r["unexplained"]),
	   ("Matched", 2000.0, 2000.0, 0.0), "reconciliation")
	eq(USERS["ao"].get("Receivables Reconciliation", r["name"])["status"], "Matched", "recorded")


def export(desk, layout, **kw):
	from datetime import date

	r = desk.s.post(f"{URL}/api/method/{GLM}.export_journals", data={
		"society": S["society"], "from_date": "2026-01-01", "to_date": f"{date.today().year + 1}-12-31", "layout": layout, **kw})
	if r.status_code != 200 or r.headers.get("content-type", "").startswith("application/json"):
		data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
		raise ServerError(r.status_code, _message(data) or r.text[:300])
	return r


@case("QA-56", "FS-GL-03: journals exported as CSV and Excel in the standard, QuickBooks and Sage 50 layouts")
def t_export(d: Desk):
	import csv as _csv

	rows = list(_csv.reader(io.StringIO(export(USERS["ao"], "Standard CSV").text)))
	eq(rows[0], ["Journal No", "Date", "Account Code", "Account Name", "Debit", "Credit", "Narration"], "standard header")
	eq(len(rows) - 1, 3 + 5, "one row per journal line")
	sage = list(_csv.reader(io.StringIO(export(USERS["ao"], "Sage 50 / Peachtree general journal (CSV)").text)))
	eq(round(sum(float(r[5]) for r in sage[1:]), 2), 0.0, "Sage signed amounts balance")
	qb = list(_csv.reader(io.StringIO(export(USERS["ao"], "QuickBooks journal (CSV)").text)))
	if "Electricity revenue" not in [r[2] for r in qb[1:]]:
		raise AssertionError("QuickBooks export lacks account names")
	xl = export(USERS["ao"], "Standard Excel")
	if not xl.content.startswith(b"PK"):
		raise AssertionError("Excel export isn't an xlsx file")
	expect_error(lambda: export(USERS["cashier"], "Standard CSV"))
	j = USERS["ao"].get("GL Journal", S["journals"][0])
	eq((j["export_count"], j["last_exported_by"]), (4, EMAIL["ao"]), "exports recorded")


@case("QA-57", "FS-REC-20: journals and ledger entries can't be changed")
def t_journal_locked(d: Desk):
	j = d.get("GL Journal", S["journals"][0])
	expect_error(lambda: d.save_doc(dict(j, narration="edited")))
	r = d.s.post(f"{URL}/api/method/frappe.client.delete", data={"doctype": "GL Journal", "name": j["name"]})
	if r.status_code == 200:
		raise AssertionError("a journal was deleted")


# ---------------------------------------------------------------- increment 9: management reports and dashboard
WIDE = {"from_date": "2026-01-01", "to_date": "2030-12-31"}


def run_report(desk, name, **filters):
	"""Run a report the way the browser does and return the whole result (columns, result rows, summary, chart)."""
	return desk.call("frappe.desk.query_report.run", report_name=name, filters={"society": S["society"], **filters})


def rows_of(result):
	return [r for r in result["result"] if isinstance(r, dict)]


@case("QA-58", "FS-RPT-01: Management opens the dashboard: 13 months, summary cards, chart, drill-down links")
def t_dashboard(d: Desk):
	res = run_report(USERS["mgmt"], "Management Dashboard", month="2026-11-15")
	rows = rows_of(res)
	eq(len(rows), 13, "13 months")
	nov = rows[-1]
	eq((nov["month"], nov["bills"], float(nov["units"]), float(nov["billed"]), float(nov["payable"])), ("Nov 2026", 1, 300.0, 22700.0, 22700.0), "November")
	cards = {c["label"]: c for c in res["report_summary"]}
	for label in ("Billed this month", "Collected this month", "Collection rate this month (%)", "Total owed by consumers",
	              "Of which arrears, surcharge and fees", "Accounts in arrears", "Credit held for consumers"):
		if label not in cards:
			raise AssertionError(f"dashboard card {label!r} missing")
	eq(float(cards["Billed this month"]["value"]), 22700.0, "billed card")
	eq(len(res["chart"]["data"]["labels"]), 13, "chart months")
	if "/desk/query-report/" not in (res["message"] or ""):
		raise AssertionError("dashboard has no drill-down links")


@case("QA-59", "FS-RPT-04/05: billing summary and bill register match the November bill (22,700)")
def t_billing_reports(d: Desk):
	res = run_report(USERS["bo"], "Billing Summary by Cycle", from_date="2026-11-01", to_date="2026-11-30")
	by = {r["item"]: r for r in rows_of(res)}
	eq((float(by["Energy Charges"]["amount"]), float(by["Energy Charges"]["units"]), float(by["Total"]["amount"])), (21000.0, 300.0, 22700.0), "by charge type")
	cat = rows_of(run_report(USERS["bo"], "Billing Summary by Cycle", from_date="2026-11-01", to_date="2026-11-30", group_by="Category"))
	eq((cat[-1]["item"], float(cat[-1]["amount"]), cat[-1]["bills"]), ("Total", 22700.0, 1), "by category")
	reg = rows_of(run_report(USERS["bm"], "Bill Register", bill_run=S["run"]["name"]))
	eq((reg[0]["bill"], float(reg[-1]["current_amount"]), float(reg[-1]["payable_within_due"]), float(reg[-1]["payable_after_due"])),
	   (S["bill"], 22700.0, 22700.0, 24970.0), "register totals")
	lines = rows_of(run_report(USERS["bm"], "Bill Register", bill_run=S["run"]["name"], show_lines=1))
	eq(sum(float(l["amount"]) for l in lines), 22700.0, "lines add up to the bill")


@case("QA-60", "FS-RPT-02/17/18/21: collections, collection vs billing, cashier closing and reversals match the counter")
def t_collection_reports(d: Desk):
	by_mode = {r["item"]: r for r in rows_of(run_report(USERS["ao"], "Collections", group_by="Payment Mode", **WIDE))}
	eq((float(by_mode["Cash"]["amount"]), float(by_mode["1Link"]["amount"]), float(by_mode["Cheque"]["amount"]),
	    float(by_mode["Cheque"]["reversed_amount"])), (20000.0, 700.0, 0.0, 3000.0), "by payment mode")
	eq(float(by_mode["Total"]["amount"]), 20700.0, "total collected")
	by_counter = {r["item"]: r for r in rows_of(run_report(USERS["ao"], "Collections", group_by="Counter", **WIDE))}
	eq((float(by_counter[S["counter"]]["amount"]), float(by_counter["(bank / channel)"]["amount"])), (20000.0, 700.0), "by counter")
	cvb = rows_of(run_report(USERS["gm"], "Collection vs Billing by Category and Block", **WIDE))
	eq((float(cvb[-1]["billed"]), float(cvb[-1]["collected"]), float(cvb[-1]["rate"])), (22700.0, 20700.0, 91.2), "collection rate")
	closing = [r for r in rows_of(run_report(USERS["ao"], "Cashier Closing", **WIDE)) if r["name"] == S["day"]][0]
	eq((closing["status"], float(closing["expected_cash"]), float(closing["cash_counted"]), float(closing["difference"]), closing["approved_by"]),
	   ("Approved", 20500.0, 20500.0, 0.0, EMAIL["ao"]), "cashier day")
	rev = [r for r in rows_of(run_report(USERS["bm"], "Payment and Fee Reversals", **WIDE)) if r.get("document") == S["cheque"]][0]
	eq((rev["kind"], float(rev["amount"]), rev["reason"], rev["reversed_by"]), ("Payment", 3000.0, "CHQ-BOUNCE", EMAIL["bm"]), "reversal")


@case("QA-61", "FS-RPT-23/24/26: arrears ageing and defaulters for the Rs 2,000 still owed")
def t_receivable_reports(d: Desk):
	age = rows_of(run_report(USERS["bm"], "Arrears Ageing", as_of="2027-03-01"))
	eq(float(age[-1]["total"]), 2000.0, "ageing total = the balance")
	dflt = rows_of(run_report(USERS["gm"], "Defaulters", as_of="2027-03-01", min_days=60))
	eq((dflt[0]["connection"], float(dflt[0]["owed"])), (S["conn"]["name"], 2000.0), "defaulter")
	if int(dflt[0]["oldest"]) < 60 or not dflt[0]["last_paid"]:
		raise AssertionError(f"oldest overdue / last payment wrong: {dflt[0]}")
	eq(rows_of(run_report(USERS["gm"], "Defaulters", as_of="2027-03-01", min_amount=5000)), [], "nobody owes 5,000")
	run_report(USERS["bm"], "Credit Balances and Advance Payments")


@case("QA-62", "FS-RPT-41/42, ACC: report rights by role; the audit log shows who changed what")
def t_report_rights(d: Desk):
	for desk, name in ((USERS["cashier"], "Management Dashboard"), (USERS["reader"], "Collections"), (USERS["bo"], "Collections"),
	                   (USERS["mgmt"], "Audit Log and User Activity"), (USERS["bm"], "Audit Log and User Activity")):
		expect_error(lambda desk=desk, name=name: run_report(desk, name, **WIDE))
	run_report(USERS["bo"], "Billing Summary by Cycle")
	run_report(USERS["mgmt"], "Pending Approvals")
	audit = rows_of(run_report(USERS["auditor"], "Audit Log and User Activity", doctype="Tariff", **WIDE))
	if not audit or not all(r["summary"] is not None for r in audit):
		raise AssertionError("audit log lists no tariff changes")
	if not any("Approval Status" in r["summary"] or "Workflow State" in r["summary"] for r in audit):
		raise AssertionError("audit log doesn't show the approval status changes")
	logins = rows_of(run_report(USERS["gm"], "Audit Log and User Activity", show="Logins and Logouts", **WIDE))
	if not any(r["operation"] == "Login" for r in logins):
		raise AssertionError("audit log lists no logins")


@case("QA-63", "FS-RPT-06/07/10/11/13-16/35, RPT-45: remaining reports and the KPI dictionary")
def t_other_reports(d: Desk):
	bm = USERS["bm"]
	sales = rows_of(run_report(bm, "Sales by Category and Tariff", from_date="2026-11-01", to_date="2026-11-30"))
	eq(float(sales[-1]["units"]), 300.0, "sales units")
	slab = rows_of(run_report(bm, "Sales by Category and Tariff", from_date="2026-11-01", to_date="2026-11-30", show="Units by Slab"))
	eq(slab[0]["slab"], "Flat rate", "flat tariff")
	fpa = [r for r in rows_of(run_report(bm, "FPA and QTA Register", adjustment_type="FPA", **WIDE)) if str(r["billing_month"]) == "2026-11-01"][0]
	eq((float(fpa["rate"]), str(fpa["reference_month"]), fpa["bills"]), (1.3, "2026-09-01", 1), "FPA register")
	adj = rows_of(run_report(bm, "Adjustments by Reason and Approver", **WIDE))
	eq((adj[0]["reason_code"], float(adj[0]["amount"]), adj[0]["approved_by"]), ("BILL-ERROR", 100.0, EMAIL["gm"]), "credit note")
	run_report(bm, "Surcharge Register", **WIDE)
	comp = rows_of(run_report(USERS["bo"], "Reading Completion by Route and Reader", reading_cycle=S["cycle"]))
	if comp[-1]["meters"] < 1 or comp[-1]["done"] < 1:
		raise AssertionError(f"reading completion: {comp}")
	run_report(USERS["reader"], "Reading Exceptions", reading_cycle=S["cycle"])
	run_report(bm, "Estimated Bills", month="2026-11-01")
	meters = run_report(bm, "Meter Register")
	if not rows_of(meters) or len(meters["report_summary"]) != 8:  # six statuses (Issued, Scrapped: 23), changed and under test
		raise AssertionError("meter register is empty or has no status counts")
	census = rows_of(run_report(bm, "Consumer Census"))
	if census[-1]["total"] < 1:
		raise AssertionError(f"census: {census}")
	kpis = d.s.get(f"{URL}/api/resource/KPI Definition", params={"limit_page_length": 100, "fields": json.dumps(["name", "formula"])}).json()["data"]
	if len(kpis) < 15 or not all(k["formula"] for k in kpis):
		raise AssertionError(f"KPI dictionary has {len(kpis)} entries")


# ---------------------------------------------------------------- notifications cleared when acted on
def notes_of(desk, doc_name):
	r = desk.s.get(f"{URL}/api/resource/Notification Log", params={
		"filters": json.dumps([["document_name", "=", doc_name]]), "fields": json.dumps(["name", "read", "for_user", "subject"]),
		"limit_page_length": 50})
	return r.json()["data"]


def wait_for_notes(desk, doc_name, count, seconds=20):
	"""Notifications are created by the background worker; wait for them."""
	import time

	for _ in range(seconds * 2):
		rows = notes_of(desk, doc_name)
		if len(rows) >= count:
			return rows
		time.sleep(0.5)
	raise AssertionError(f"expected {count} notifications about {doc_name}, found {len(notes_of(desk, doc_name))}")


@case("QA-64", "Notifications: acting on a document clears the approvers' notifications; the maker's is read when opened")
def t_notifications(d: Desk):
	adj = USERS["bm"].save_doc({"doctype": "Tariff Adjustment", "naming_series": "ADJ-.YYYY.-.####", "adjustment_type": "FPA",
	                            "society": S["society"], "utility": "Electricity", "billing_month": "2027-01-15",
	                            "rate_per_unit": "1.00", "charge_type": f"FPA {RUN}"})
	adj = USERS["bm"].workflow(adj, "Submit for Approval")
	gm_notes = [n for n in wait_for_notes(USERS["gm"], adj["name"], 1) if n["for_user"] == EMAIL["gm"]]
	eq([n["read"] for n in gm_notes], [0], "the approver has one unread notification")
	adj = USERS["gm"].workflow(adj, "Approve")
	eq([n["read"] for n in notes_of(USERS["gm"], adj["name"])], [1], "approving clears the approver's notification")
	bm_notes = [n for n in wait_for_notes(USERS["bm"], adj["name"], 1) if n["for_user"] == EMAIL["bm"]]
	eq([n["read"] for n in bm_notes], [0], "the maker's 'was approved' waits to be opened")
	USERS["bm"].call("frappe.desk.doctype.notification_log.notification_log.mark_as_read", docname=bm_notes[0]["name"])
	eq([n["read"] for n in notes_of(USERS["bm"], adj["name"])], [1], "opening it marks it read")


# ---------------------------------------------------------------- modern UI: home dashboard and theme
@case("QA-65", "Modern UI: dashboard data for Management over HTTP; cashier refused; theme, font and page are served")
def t_modern_dashboard(d: Desk):
	call = "utility_billing.utility_billing.dashboard.get_dashboard"
	res = USERS["mgmt"].call(call, society=S["society"], month="2026-11-01")
	eq(len(res["kpis"]), 9, "nine cards")
	cards = {k["label"]: k for k in res["kpis"]}
	# increment 46: the first card counts the society's active connections
	active = d.call("frappe.client.get_count", doctype="Connection", filters=json.dumps({"society": S["society"], "status": "Active"}))
	eq((res["kpis"][0]["label"], cards["Active connections"]["value"], active > 0), ("Active connections", f"{active:,}", True), "active connections")
	eq((cards["Billed this cycle"]["value"], cards["Units billed"]["value"], cards["Units billed"]["note"]),
	   ("Rs 22,700", "300 kWh", "1 connection billed"), "November figures")
	loss = {k["icon"]: k for k in res["kpis"]}["losses"]
	eq((loss["label"], loss["value"], cards["Open service requests"]["value"]), ("Line losses (AT&C)", "—", "0"),
	   "line losses unavailable without a DISCO bill; no open service requests")
	eq([s["state"] for s in res["steps"]][:3], ["done", "done", "done"], "readings, review and bill run are done")
	eq(len(res["series"]), 12, "12 months")
	if not res["channels"] or res["channels"][0]["name"] != "Cash":
		raise AssertionError(f"collections by channel: {res['channels']}")
	eq(res["society"], S["society"], "society")
	expect_error(lambda: USERS["cashier"].call(call, society=S["society"]))
	expect_error(lambda: USERS["reader"].call(call, society=S["society"]))
	css = d.s.get(f"{URL}/assets/utility_billing/css/ub_theme.css")
	if css.status_code != 200 or "--ub-navy" not in css.text or ".ub-kpi" not in css.text:
		raise AssertionError("theme stylesheet isn't served")
	font = d.s.get(f"{URL}/assets/utility_billing/fonts/Inter-latin.woff2")
	if font.status_code != 200 or font.content[:4] != b"wOF2":
		raise AssertionError("the Inter font isn't served")
	desk = d.s.get(f"{URL}/desk", headers={"Accept": "text/html"})
	if "ub_theme.css" not in desk.text:
		raise AssertionError("the desk doesn't load the theme")
	# Opening the desk gives this session a CSRF token, and from then on Frappe wants it on every POST, as it does from a browser
	# (found in increment 11: later POSTs by this session were refused with "Invalid Request"). Send it the way the browser does.
	import re

	token = re.search(r'csrf_token\W+([0-9a-f]{20,})', desk.text)
	if token:
		d.s.headers["X-Frappe-CSRF-Token"] = token.group(1)
	eq(d.get("Page", "ub-dashboard")["title"], "Dashboard", "dashboard page")


# ---------------------------------------------------------------- increment 10: DISCO bill, line losses, AT&C, reconciliation
@case("QA-66", "FS-RPT-08, 30-34, 38: DISCO bill entered by the Accounts Officer; loss, AT&C, transformer and reconciliation reports; dashboard")
def t_losses(d: Desk):
	tx = d.get("Transformer", S["transformer"])
	base = {"doctype": "DISCO Bill", "naming_series": "DISCO-.YYYY.-.####", "society": S["society"], "utility": "Electricity",
	        "billing_month": "2026-11-20", "units_kwh": "340", "energy_amount": "24000", "fpa_amount": "1200", "qta_amount": "0",
	        "other_amount": "2600", "inputs": [{"feeder": tx["feeder"], "input_kwh": "340"}, {"transformer": tx["name"], "input_kwh": "330"}]}
	expect_error(lambda: USERS["cashier"].save_doc(dict(base)))
	bill = USERS["ao"].save_doc(dict(base))
	eq((str(bill["billing_month"]), float(bill["total_amount"])), ("2026-11-01", 27800.0), "saved for the month, total worked out")
	expect_error(lambda: USERS["ao"].save_doc(dict(base)), "already covers")
	expect_error(lambda: USERS["ao"].save_doc(dict(base, billing_month="2026-12-01", inputs=[{"feeder": tx["feeder"], "transformer": tx["name"], "input_kwh": "1"}])),
	             "not both")

	# 340 kWh came in, 300 were billed: 40 kWh lost = 11.8%, between the Watch (8%) and High (12%) levels.
	rows = rows_of(run_report(USERS["bm"], "Line Losses by Feeder and Transformer", month="2026-11-01"))
	eq((float(rows[0]["input_kwh"]), float(rows[0]["billed_kwh"]), float(rows[0]["loss_kwh"]), float(rows[0]["loss_pct"]), rows[0]["status"]),
	   (340.0, 300.0, 40.0, 11.8, "Watch"), "whole society")
	eq((float(rows[1]["input_kwh"]), float(rows[1]["loss_pct"]), rows[1]["status"]), (340.0, 11.8, "Watch"), "feeder")
	tr = rows_of(run_report(USERS["bm"], "Line Losses by Feeder and Transformer", month="2026-11-01", group_by="Transformer"))
	eq((float(tr[1]["input_kwh"]), float(tr[1]["loss_pct"])), (330.0, 9.1), "transformer")
	rank = rows_of(run_report(USERS["gm"], "Highest-Loss Feeders and Loss Trend", month="2026-11-01"))
	eq((rank[0]["rank"], float(rank[0]["loss_pct"])), (1, 11.8), "ranking")
	trend = rows_of(run_report(USERS["gm"], "Highest-Loss Feeders and Loss Trend", month="2026-11-01", show="12-Month Trend"))
	eq((len(trend), trend[-1]["month"], float(trend[-1]["loss_pct"])), (12, "Nov 2026", 11.8), "12-month trend")

	atc = rows_of(run_report(USERS["gm"], "AT&C Losses", month="2026-11-01"))[-1]
	eq(float(atc["billing_efficiency"]), 88.2, "billing efficiency = 300 / 340")
	if atc["atc"] is None:
		raise AssertionError("AT&C wasn't worked out")
	load = [r for r in rows_of(run_report(USERS["bm"], "Transformer Loading")) if r["transformer"] == tx["name"]][0]
	eq((load["connections"] >= 1, float(load["kw"]) >= 5), (True, True), "transformer carries the connection")

	rec = [r for r in rows_of(run_report(USERS["ao"], "DISCO Bill vs Resident Billing", from_date="2026-11-01", to_date="2026-11-30")) if r["month"] == "Nov 2026"][0]
	eq((float(rec["disco_units"]), float(rec["billed_units"]), float(rec["units_difference"])), (340.0, 300.0, 40.0), "units")
	# Residents were billed energy 21,000 (+ FPA 0) for the electricity group; the DISCO billed the society 27,800.
	eq((float(rec["disco_total"]), float(rec["billed_amount"]), float(rec["margin"]), float(rec["margin_pct"])), (27800.0, 21000.0, -6800.0, -32.4), "margin")
	eq((float(rec["disco_fpa"]), float(rec["billed_fpa"]), float(rec["fpa_recovery"])), (1200.0, 0.0, 0.0), "FPA recovery")
	expect_error(lambda: run_report(USERS["bo"], "DISCO Bill vs Resident Billing", from_date="2026-11-01", to_date="2026-11-30"))

	dash = USERS["mgmt"].call("utility_billing.utility_billing.dashboard.get_dashboard", society=S["society"], month="2026-11-01")
	card = {k["icon"]: k for k in dash["kpis"]}["losses"]  # "Line losses" until the bills fall due, then "Line losses (AT&C)"
	if card["value"] == "—" or not card["value"].endswith("%"):
		raise AssertionError(f"dashboard line-loss card still empty: {card}")
	eq((dash["feeders"][0]["input_kwh"], dash["feeders"][0]["loss"], dash["feeders"][0]["status"], dash["feeders"][0]["tone"]),
	   ("340", "11.8%", "Watch", "amber"), "dashboard feeder row")
	eq(dash["feeders_have_input"], True, "feeder note hidden")
	for desk, name in ((USERS["cashier"], "Line Losses by Feeder and Transformer"), (USERS["reader"], "AT&C Losses")):
		expect_error(lambda desk=desk, name=name: run_report(desk, name, month="2026-11-01"))


@case("QA-67", "FS-REC-04/05/06/07/08/15: stage rules saved from the form; recovery run, notices, print, case with a promise, order approval, target")
def t_recovery(d: Desk):
	R = "utility_billing.utility_billing.recovery."
	rec, bm, cashier = USERS["rec"], USERS["bm"], USERS["cashier"]
	soc = d.get("Society", S["society"])
	soc.update({"defaulter_min_amount": "1000", "defaulter_min_days": "30", "recovery_stages": [
		{"stage": "Reminder", "days_overdue": "30", "channel": "Print", "message": "Dear {consumer}, {owed} is overdue ({days} days)."},
		{"stage": "Disconnection Order", "days_overdue": "60", "channel": "Print", "message": "Disconnect {reference}."}]})
	saved = d.save_doc(soc)
	eq([(r["stage"], int(r["days_overdue"])) for r in saved["recovery_stages"]], [("Reminder", 30), ("Disconnection Order", 60)], "stages saved")
	bad = d.get("Society", S["society"])
	bad["recovery_stages"] = [{"stage": "Reminder", "days_overdue": "40", "channel": "Print"}, {"stage": "Notice", "days_overdue": "40", "channel": "Print"}]
	expect_error(lambda: d.save_doc(bad), "after the stage before")

	as_of = "2027-03-01"
	page = rec.call(R + "get_recovery", society=S["society"], as_of=as_of)
	eq((page["cards"]["defaulters"], page["cards"]["defaulter_amount"], page["rows"][0]["connection"], page["rows"][0]["next_stage"]),
	   (1, 2000.0, S["conn"]["name"], "Reminder"), "page data")
	expect_error(lambda: cashier.call(R + "get_recovery", society=S["society"], as_of=as_of))
	expect_error(lambda: cashier.call(R + "new_run", society=S["society"], as_of=as_of))

	run_name = rec.call(R + "new_run", society=S["society"], as_of=as_of)
	run = rec.get("Recovery Run", run_name)
	eq((run["status"], run["accounts_due"], run["items"][0]["stage"], run["items"][0]["channel"]), ("Draft", 1, "Reminder", "Print"), "draft run")
	n = doc_method(rec, run, "issue")
	run = rec.get("Recovery Run", run_name)
	eq((run["status"], run["notices_issued"]), ("Issued", 1), "issued")
	notice = rec.get("Recovery Notice", run["items"][0]["notice"])
	eq((notice["stage"], notice["status"], float(notice["owed"]), int(notice["oldest_days"]) >= 60), ("Reminder", "Issued", 2000.0, True), "notice")
	if "Rs 2,000" not in notice["message"]:
		raise AssertionError(f"notice text: {notice['message']}")
	_reader, text = _notice_pdf(rec, run_name)
	for word in ("Payment Reminder", "Rs 2,000"):
		if word not in text:
			raise AssertionError(f"{word!r} missing from the notice PDF")
	expect_error(lambda: doc_method(rec, run, "issue"))  # already issued

	run2 = rec.call(R + "new_run", society=S["society"], as_of=as_of)
	doc_method(rec, rec.get("Recovery Run", run2), "issue")
	order = rec.get("Recovery Run", run2)["items"][0]["notice"]
	eq(rec.get("Recovery Notice", order)["status"], "Awaiting Approval", "disconnection order waits")
	expect_error(lambda: rec.call(R + "approve_order", name=order))  # the officer isn't an approver
	eq(bm.call(R + "approve_order", name=order), order, "approved by a Billing Manager")
	eq(rec.get("Recovery Notice", order)["approved_by"], EMAIL["bm"], "approver recorded")
	dlist = rows_of(run_report(USERS["gm"], "Disconnection List"))
	eq((dlist[0]["connection"], dlist[0]["status"], float(dlist[0]["owed"])), (S["conn"]["name"], "Issued", 2000.0), "disconnection list")

	assigned = rec.call(R + "assign", society=S["society"], connections=[S["conn"]["name"]], user=EMAIL["rec"])
	eq(assigned, {"opened": 1, "reassigned": 0}, "case opened")
	case_name = rec.call(R + "case_of", connection=S["conn"]["name"])
	c = rec.get("Recovery Case", case_name)
	c["followups"] = [{"followup_date": as_of, "kind": "Promise to Pay", "promise_date": "2027-03-10", "promise_amount": "2000", "outcome": "Will pay on the 10th"}]
	saved = rec.save_doc(c)
	eq((saved["status"], saved["promise_date"], saved["followups"][0]["done_by"]), ("Promise to Pay", "2027-03-10", EMAIL["rec"]), "promise recorded")
	held = rec.call(R + "get_recovery", society=S["society"], as_of=as_of)["rows"][0]
	eq((held["hold"], held["next_stage"], held["promise"]), ("Promise to pay not yet due", None, "Promised"), "promise holds recovery")
	broken = rec.call(R + "get_recovery", society=S["society"], as_of="2027-03-12")["rows"][0]
	eq(broken["promise"], "Broken", "promise broken after the date")

	bm.save_doc({"doctype": "Recovery Target", "society": S["society"], "month": "2027-03-20", "target_amount": "5000"})
	tgt = rows_of(run_report(USERS["gm"], "Recovery against Target", year=2027))
	eq([float(r["target"]) for r in tgt if r["month"] == "Mar 2027"], [5000.0], "target in the report")
	notices = rows_of(run_report(USERS["bm"], "Recovery Notices Register", from_date="2027-03-01", to_date="2027-03-31"))
	eq(sorted(r["stage"] for r in notices), ["Disconnection Order", "Reminder"], "notices register")
	expect_error(lambda: run_report(cashier, "Recovery Notices Register", from_date="2027-03-01", to_date="2027-03-31"))


def doc_method(desk, doc: dict, method: str, args: dict | None = None):
	"""Call a whitelisted document method the way the form's custom buttons do (frm.call)."""
	data = {"docs": json.dumps(doc), "method": method}
	if args:
		data["args"] = json.dumps(args)
	r = desk.s.post(f"{URL}/api/method/run_doc_method", data=data)
	data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
	if r.status_code != 200:
		raise ServerError(r.status_code, f"{_message(data) or r.text[:300]} [{method}]")
	return data.get("message")


@case("QA-68", "FS-NTF-01..09, FS-PAY-09/10/11/16: messages, providers, templates, settlement file, suspense, refund, broadcast, links")
def t_notifications_and_settlement(d: Desk):
	from datetime import date

	import requests

	N = "utility_billing.utility_billing.notify."
	ao, bm, gm, cashier = USERS["ao"], USERS["bm"], USERS["gm"], USERS["cashier"]
	today = date.today().isoformat()
	conn = S["conn"]["name"]

	def logs(**filters):
		r = d.s.get(f"{URL}/api/resource/Message Log", params={"filters": json.dumps({"society": S["society"], **filters}), "limit_page_length": 200,
		                                                         "fields": json.dumps(["name", "event", "channel", "status", "body", "recipient", "provider_ref", "cost", "bill_link", "reference_name"]),
		                                                         "order_by": "creation asc"})
		return r.json()["data"]

	owner = d.get("Consumer", S["owner"])
	owner.update({"mobile_no": "0300-1234567", "email": "owner@example.com", "notify_sms": "1", "notify_email": "1", "notify_whatsapp": "0"})
	d.save_doc(owner)
	soc = d.get("Society", S["society"])
	soc.update({"quiet_hours_start": "00:00:00", "quiet_hours_end": "00:00:00", "whatsapp_enabled": "0",
	            "notification_rules": [{"event": "payment.received", "channel_1": "SMS", "channel_2": "Email", "enabled": "1"}]})
	d.save_doc(soc)
	bad_rule = d.get("Society", S["society"])
	bad_rule["notification_rules"] = [{"event": "bill.issued", "channel_1": "SMS", "enabled": "1"}, {"event": "bill.issued", "channel_1": "Email", "enabled": "1"}]
	expect_error(lambda: d.save_doc(bad_rule), "twice")

	# providers and templates
	expect_error(lambda: d.save_doc({"doctype": "Messaging Provider", "channel": "SMS", "provider_type": "HTTP gateway", "endpoint_url": "http://insecure.example/x",
	                                 "society": S["society"], "enabled": "1"}), "https")
	sms = d.save_doc({"doctype": "Messaging Provider", "channel": "SMS", "provider_type": "Dry run", "society": S["society"], "cost_per_message": "2", "enabled": "1"})
	d.save_doc({"doctype": "Messaging Provider", "channel": "Email", "provider_type": "Dry run", "society": S["society"], "enabled": "1"})
	expect_error(lambda: cashier.get("Messaging Provider", sms["name"]))
	tpl = {"doctype": "Notification Template", "society": S["society"], "event": "payment.received", "channel": "SMS", "language": "English",
	       "body": "Thanks {consumer}: Rs {amount} received, receipt {receipt}."}
	bm.save_doc(dict(tpl))
	expect_error(lambda: bm.save_doc(dict(tpl)), "already")

	# FS-PAY-09: a payment sends its receipt message, which goes out and is logged with its cost
	pay = ao.save_doc({"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": conn, "amount": "100", "mode": "Bank Transfer",
	                   "payment_date": today, "channel_txn_id": f"N-{RUN}-1"})
	pay = ao.save_doc(pay, action="Submit")
	d.call(N + "send_now")
	expect_error(lambda: cashier.call(N + "send_now"))
	sent = [m for m in logs(event="payment.received") if m["reference_name"] == pay["name"]]
	eq(len(sent), 1, "one receipt message")
	m = sent[0]
	eq((m["channel"], m["status"], m["recipient"], float(m["cost"])), ("SMS", "Sent", "0300-1234567", 2.0), "receipt message")
	eq(m["body"], f"Thanks {owner['consumer_name']}: Rs 100 received, receipt {pay['name']}.", "the society's own template was used")
	if not m["provider_ref"].startswith("DRY-"):
		raise AssertionError(f"provider reference: {m['provider_ref']}")

	# the delivery report and the bill link are open to a gateway or a consumer, but only with the right token
	anon = requests.Session()
	anon.headers.update({"Host": SITE, "Accept": "application/json"})
	r = anon.post(f"{URL}/api/method/{N}delivery_report", data={"provider_ref": m["provider_ref"], "status": "Delivered", "token": "guess"})
	eq(r.status_code, 403, "delivery report without the provider's token")
	link = [x for x in logs(event="bill.issued") if x["reference_name"] == S["bill"] and x["bill_link"]]
	if not link:
		raise AssertionError("no bill message carries a PDF link")
	url = link[0]["bill_link"].split("/api/method/")[1]
	good = anon.get(f"{URL}/api/method/{url}")
	if good.status_code != 200 or not good.content.startswith(b"%PDF"):
		raise AssertionError(f"bill link did not give a PDF: {good.status_code}")
	bad = anon.get(f"{URL}/api/method/{url.split('token=')[0]}token=0000")
	eq(bad.status_code, 403, "bill link with a wrong token")

	# FS-PAY-10/11: a settlement file is matched, unrecorded money is posted or held, and the file closes
	csv_text = (f"transaction_id,reference_no,amount,date\nN-{RUN}-1,{conn},100,{today}\nN-{RUN}-2,{conn},250,{today}\nN-{RUN}-3,NOPE-REF,75,{today}\n")
	sf = ao.save_doc({"doctype": "Settlement File", "society": S["society"], "mode": "Bank Transfer", "file_date": today, "source_name": "MBL"})
	up = ao.s.post(f"{URL}/api/method/upload_file", files={"file": (f"settlement-{RUN}.csv", csv_text)},
	               data={"is_private": "1", "doctype": "Settlement File", "docname": sf["name"], "fieldname": "settlement_file"})
	eq(up.status_code, 200, "file upload")
	sf = ao.get("Settlement File", sf["name"])
	sf["settlement_file"] = up.json()["message"]["file_url"]  # the form puts the uploaded file's URL in the Attach field and saves
	sf = ao.save_doc(sf)
	out = doc_method(ao, sf, "import_file")
	eq(out, {"matched": 1, "flagged": 2}, "import result")
	sf = ao.get("Settlement File", sf["name"])
	eq([r["result"] for r in sf["rows"]], ["Matched", "Not recorded", "Not recorded"], "row results")
	expect_error(lambda: doc_method(cashier, sf, "post_unrecorded"))
	expect_error(lambda: doc_method(ao, sf, "close_file"))  # flagged rows have no action yet
	out = doc_method(ao, sf, "post_unrecorded")
	eq(out, {"posted": 1, "suspense": 1}, "posted and held")
	sf = ao.get("Settlement File", sf["name"])
	susp = sf["rows"][2]["suspense"]
	receipt2 = sf["rows"][1]["receipt"]
	eq((ao.get("Payment Receipt", receipt2)["amount"], ao.get("Suspense Payment", susp)["status"]), (250.0, "Held"), "posted receipt and held payment")
	exc = rows_of(run_report(ao, "Settlement Exceptions", from_date=today, to_date=today))
	eq([r["result"] for r in exc], [], "nothing open after the actions")
	eq(doc_method(ao, sf, "close_file"), "Closed", "file closed")
	held = rows_of(run_report(ao, "Suspense Payments", as_of=today))
	eq((held[0]["name"], float(held[0]["amount"])), (susp, 75.0), "suspense report")
	doc_method(ao, ao.get("Suspense Payment", susp), "match", {"connection": conn})
	eq(ao.get("Suspense Payment", susp)["status"], "Matched", "suspense matched")

	# FS-PAY-16, WF-13: a refund of credit needs a second person and can't exceed the credit
	big = ao.save_doc({"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": conn, "amount": "5000", "mode": "Bank Transfer",
	                   "payment_date": today, "channel_txn_id": f"N-{RUN}-4"})
	ao.save_doc(big, action="Submit")
	credit = float(d.call("utility_billing.utility_billing.account.account_summary", connection=conn)["balances"]["credit"])
	if credit < 1000:
		raise AssertionError(f"expected credit after the overpayment, got {credit}")
	refund = {"doctype": "Refund", "connection": conn, "amount": str(credit + 1), "reason_code": "REFUND-EXCESS", "refund_mode": "Bank Transfer",
	          "payee_name": "QA Owner", "instrument_no": "PK00 QA 1"}
	too_much = ao.save_doc(dict(refund))
	expect_error(lambda: ao.workflow(too_much, "Submit for Approval"), "credit")
	ok = ao.save_doc(dict(refund, amount="1000"))
	ao.workflow(ok, "Submit for Approval")
	expect_error(lambda: ao.workflow(ok, "Approve"))
	gm.workflow(ok, "Approve")
	ok = d.get("Refund", ok["name"])
	eq((ok["docstatus"], ok["workflow_state"], ok["approved_by"]), (1, "Approved", EMAIL["gm"]), "refund approved")
	entry = d.get("Customer Ledger Entry", ok["ledger_entry"])
	eq((entry["entry_type"], float(entry["amount"])), ("Refund", 1000.0), "refund entry")
	eq(float(d.call("utility_billing.utility_billing.account.account_summary", connection=conn)["balances"]["credit"]), credit - 1000, "credit reduced")
	reg = rows_of(run_report(gm, "Refunds Register", from_date=today, to_date=today))
	row = [r for r in reg if r["name"] == ok["name"]][0]
	eq((row["amount"], row["workflow_state"], row["approved_by"]), (1000.0, "Approved", EMAIL["gm"]), "refunds register")
	eq([r["workflow_state"] for r in reg if r["name"] == too_much["name"]], ["Draft"], "the refused refund stays a draft")

	# FS-NTF-09: a broadcast to the whole society
	b = bm.save_doc({"doctype": "Broadcast", "society": S["society"], "audience": "Whole society", "message": "Power off 9 am to 1 pm for maintenance."})
	expect_error(lambda: doc_method(cashier, b, "send"))
	n = doc_method(bm, b, "send")
	if int(n) < 1:
		raise AssertionError(f"broadcast reached nobody: {n}")
	d.call(N + "send_now")
	outage = [x for x in logs(event="outage.notice")]
	eq((outage[-1]["status"], outage[-1]["body"]), ("Sent", "Power off 9 am to 1 pm for maintenance."), "broadcast message")

	# FS-NTF-06: the cost report
	cost = rows_of(run_report(ao, "Message Cost by Channel", from_date=today, to_date=today))
	by = {r["channel"]: r for r in cost}
	if int(by["SMS"]["sent"]) < 2 or float(by["SMS"]["cost"]) < 4:
		raise AssertionError(f"cost report: {by['SMS']}")
	expect_error(lambda: run_report(cashier, "Message Cost by Channel", from_date=today, to_date=today))


@case("QA-69", "FS-BIL-11, FS-WF-01/03/10/12: bulk debit, due date extension, dispute, revised bill refused after payments, corrections register")
def t_corrections(d: Desk):
	bo, bm, gm, cashier = USERS["bo"], USERS["bm"], USERS["gm"], USERS["cashier"]
	conn = S["conn"]["name"]
	def owed():
		b = d.call("utility_billing.utility_billing.account.account_summary", connection=conn)["balances"]
		return round(float(b["current"]) + float(b["arrears"]) + float(b["surcharge"]) + float(b["fees"]) - float(b["credit"]), 2)

	# bulk adjustment: made by a Billing Manager, approved by a General Manager, one ledger entry per account
	ba = {"doctype": "Bulk Adjustment", "society": S["society"], "audience": "Whole society", "note_type": "Debit Note", "bucket": "Current bill",
	      "amount": "3000", "reason_code": "BULK-CORRECTION", "remarks": "Group correction approved by management"}
	expect_error(lambda: cashier.save_doc(dict(ba)))
	made = bm.save_doc(dict(ba))
	if int(made["accounts"]) < 1 or made["rows"][0]["result"] != "Will post":
		raise AssertionError(f"bulk adjustment list: {made['accounts']} {made['rows']}")
	before = owed()
	bm.workflow(made, "Submit for Approval")
	expect_error(lambda: bm.workflow(made, "Approve"))
	gm.workflow(made, "Approve")
	done = d.get("Bulk Adjustment", made["name"])
	eq((done["docstatus"], done["rows"][0]["result"]), (1, "Posted"), "bulk debit posted")
	eq(owed(), before + 3000, "what the account owes after the bulk debit")

	# due date extension, within the society's limits (7 days, 2 a year) unless a General Manager
	from datetime import date, timedelta

	bill = d.get("Utility Bill", S["bill"])
	due = date.fromisoformat(bill["due_date"])
	ext = {"doctype": "Due Date Extension", "connection": conn, "new_due_date": (due + timedelta(days=5)).isoformat(), "reason_code": "EXT-HARDSHIP"}
	expect_error(lambda: cashier.save_doc(dict(ext)))
	saved = bm.save_doc(dict(ext))
	eq((int(saved["days_extended"]), saved["bill"]), (5, S["bill"]), "extension")
	old = d.get("Utility Bill", S["bill"])
	eq((old["extended_due_date"], old["due_date"], old["status"]), ((due + timedelta(days=5)).isoformat(), bill["due_date"], "Superseded"),
	   "the issued bill is not edited: kept, superseded, with the extended date beside it")
	new = d.get("Utility Bill", saved["revised_bill"])  # increment 37: a revised bill with the new due date
	eq((new["revises"], new["due_date"], new["status"]), (S["bill"], (due + timedelta(days=5)).isoformat(), "Posted"), "the revised bill")
	eq(USERS["cashier"].call("utility_billing.utility_billing.account.enquiry", reference_no=S["conn"]["reference_no"])["bill"], new["name"],
	   "payment channels show the revised bill")
	too_long = dict(ext, new_due_date=(due + timedelta(days=14)).isoformat())
	expect_error(lambda: bm.save_doc(dict(too_long)), "General Manager")
	eq(int(gm.save_doc(dict(too_long))["days_extended"]), 9, "a General Manager may exceed the limit")
	latest = latest_bill(conn)

	# dispute: opened by a Billing Officer, decided by a manager
	# the revised bill asks for what is still owed, so the disputed amount is kept within it (increment 37)
	dp = {"doctype": "Bill Dispute", "connection": conn, "bill": latest,
	      "disputed_amount": str(min(1000, int(float(d.get("Utility Bill", latest)["payable_within_due"])))), "reason": "The consumer says the meter ran slow"}
	opened = bo.save_doc(dict(dp))
	eq(opened["status"], "Open", "dispute opened")
	expect_error(lambda: bo.save_doc(dict(dp)), "already disputes")
	expect_error(lambda: doc_method(bo, opened, "resolve", {"outcome": "Released", "remarks": "an officer may not decide"}))
	doc_method(bm, opened, "resolve", {"outcome": "Withdrawn", "remarks": "The consumer withdrew the dispute"})
	eq(d.get("Bill Dispute", opened["name"])["status"], "Withdrawn", "dispute decided")

	# a revised bill is refused once payments came after the bill: use a credit or debit note instead
	rev = bo.save_doc({"doctype": "Bill Revision", "original_bill": latest, "kind": "Corrected reading", "reason_code": "READ-WRONG",
	                   "remarks": "The reader mistyped the present reading", "new_present_reading": "1"})
	expect_error(lambda: bo.workflow(rev, "Submit for Approval"))

	reg = rows_of(run_report(gm, "Corrections Register", from_date="2026-01-01", to_date="2030-12-31"))
	eq(sorted({r["kind"] for r in reg}), ["Bill dispute", "Bill revision", "Bulk adjustment", "Due date extension"], "corrections register")
	expect_error(lambda: run_report(cashier, "Corrections Register", from_date="2026-01-01", to_date="2030-12-31"))


@case("QA-70", "FS-CFG-07, 14, FS-REC-17, FS-BIL-07: tax saved from the form, impact previews, tax register; part-month setting")
def t_taxes_and_previews(d: Desk):
	P = "utility_billing.utility_billing.preview."
	bm, ao, cashier = USERS["bm"], USERS["ao"], USERS["cashier"]
	tax = {"doctype": "Tax", "society": S["society"], "tax_name": "Society Duty", "tax_code": f"SD{RUN}", "rate": "10", "applies_to": "Charge groups",
	       "charge_rows": [{"charge_group": "Society"}], "enabled": "1", "calculation_order": "10", "utilities": [{"utility": "Electricity"}]}
	expect_error(lambda: bm.save_doc(dict(tax, rate="101", tax_code=f"BAD{RUN}")), "between 0 and 100")
	expect_error(lambda: bm.save_doc(dict(tax, utilities=[], tax_code=f"NU{RUN}")), "utilities")  # each tax names the utilities it is charged on
	expect_error(lambda: cashier.save_doc(dict(tax)))
	saved = bm.save_doc(dict(tax, enabled="0"))  # saved switched off: the preview shows what it would add
	eq((saved["tax_title"], float(saved["rate"])), ("Society Duty 10%", 10.0), "tax saved from the form")
	expect_error(lambda: bm.save_doc(dict(tax, tax_name="Other")), "already uses")

	r = bm.call(P + "tax_impact", name=saved["name"])
	if r["bills"] < 1 or float(r["difference"]) <= 0 or not r["by_category"]:
		raise AssertionError(f"tax preview: {r}")
	eq((r["old_total"] + r["difference"] - r["new_total"]) < 0.01, True, "preview totals add up")
	expect_error(lambda: cashier.call(P + "tax_impact", name=saved["name"]))
	eq(d.get("Tax", saved["name"])["enabled"], 0, "the preview didn't switch the tax on")

	tariffs = d.s.get(f"{URL}/api/resource/Tariff", params={"filters": json.dumps({"society": S["society"], "docstatus": 1}), "limit_page_length": 5}).json()["data"]
	same = bm.call(P + "tariff_impact", name=tariffs[0]["name"])
	eq(float(same["difference"]), 0.0, "an unchanged tariff changes nothing")
	adjs = d.s.get(f"{URL}/api/resource/Tariff Adjustment", params={"filters": json.dumps({"society": S["society"]}), "limit_page_length": 5}).json()["data"]
	if adjs:
		adj = bm.call(P + "adjustment_impact", name=adjs[0]["name"])
		eq(adj["bills"] >= 0, True, "adjustment preview")

	reg = rows_of(run_report(ao, "Tax Register and Taxes Payable", from_date="2026-01-01", to_date="2030-12-31"))
	eq(reg, [], "no bill has been issued with a tax yet")
	expect_error(lambda: run_report(cashier, "Tax Register and Taxes Payable", from_date="2026-01-01", to_date="2030-12-31"))

	soc = d.get("Society", S["society"])
	eq(int(soc["prorate_part_months"]), 1, "part-month billing is on by default")
	owner = d.get("Consumer", S["owner"])
	owner.update({"tax_status": "Non-filer"})
	eq(d.save_doc(owner)["tax_status"], "Non-filer", "consumer tax status saved")


@case("QA-71", "FS-WF-04/05/14, FS-ACC-04, FS-BIL-14: approval levels by amount, installment plan with down payment, write-off with board resolution")
def t_arrangements(d: Desk):
	from datetime import date, timedelta

	bo, bm, gm, ao, cashier = USERS["bo"], USERS["bm"], USERS["gm"], USERS["ao"], USERS["cashier"]
	conn = S["conn"]["name"]
	today = date.today()
	soc = d.get("Society", S["society"])
	soc["approval_levels"] = [{"transaction": "Write Off", "up_to_amount": "1000", "approver_role": "Billing Manager"},
	                          {"transaction": "Write Off", "up_to_amount": "0", "approver_role": "General Manager"}]
	d.save_doc(soc)

	# arrears to work on: a bulk debit on the Arrears balance, approved by a General Manager
	ba = bm.save_doc({"doctype": "Bulk Adjustment", "society": S["society"], "audience": "Whole society", "note_type": "Debit Note", "bucket": "Arrears",
	                  "amount": "30000", "reason_code": "BULK-CORRECTION", "remarks": "Arrears for the plan and write-off cases"})
	bm.workflow(ba, "Submit for Approval")
	gm.workflow(ba, "Approve")
	owed = lambda: (lambda b: float(b["arrears"]) + float(b["surcharge"]) + float(b["fees"]))(  # noqa: E731
		d.call("utility_billing.utility_billing.account.account_summary", connection=conn)["balances"])
	if owed() < 20000:
		raise AssertionError(f"expected arrears after the bulk debit, got {owed()}")

	# installment plan: terms checked, down payment before approval, approved by a Billing Manager
	first = (today.replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()
	plan = {"doctype": "Installment Plan", "connection": conn, "amount_to_split": "5000", "down_payment": "4900", "installments": "2", "first_month": first,
	        "remarks": "Agreed with the consumer"}
	expect_error(lambda: bo.save_doc(dict(plan, down_payment="100", installments="5")), "at least 10%")
	expect_error(lambda: bo.save_doc(dict(plan, installments="9", down_payment="500")), "2 to 6")
	saved = bo.save_doc(dict(plan))
	eq((float(saved["installment_amount"]), [float(r["amount"]) for r in saved["schedule"]]), (50.0, [50.0] * 2), "schedule")
	bo.workflow(saved, "Submit for Approval")
	# (the refusal without a down payment is covered by the integration tests: payments from earlier cases today would count here)
	ao.save_doc({"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": conn, "amount": "4900", "mode": "Bank Transfer",
	             "payment_date": today.isoformat(), "channel_txn_id": f"DP-{RUN}"}, action="Submit")
	expect_error(lambda: bo.workflow(saved, "Approve"))  # the maker can't approve their own plan
	bm.workflow(saved, "Approve")
	active = d.get("Installment Plan", saved["name"])
	eq((active["docstatus"], active["plan_status"]), (1, "Active"), "plan active")
	rb = d.get("Utility Bill", active["revised_bill"])  # increment 37: the latest bill reissued with the plan's arrears not yet due
	eq((rb["installment_plan"], float(rb["plan_deferred"]), rb["status"]), (saved["name"], 100.0, "Posted"), "the bill reissued for the plan")

	# write-off: the amount decides who approves (up to 1,000 a Billing Manager, above that a General Manager)
	wo = {"doctype": "Write Off", "connection": conn, "amount": "500", "reason_code": "IRRECOV", "resolution_no": f"BR-{RUN}",
	      "resolution_date": today.isoformat(), "remarks": "Three notices and two visits; the consumer has left"}
	expect_error(lambda: cashier.save_doc(dict(wo)))
	small = ao.save_doc(dict(wo))
	ao.workflow(small, "Submit for Approval")
	bm.workflow(small, "Approve")
	eq(d.get("Write Off", small["name"])["docstatus"], 1, "a small write-off approved by a Billing Manager")
	before = owed()
	big = ao.save_doc(dict(wo, amount="5000"))
	ao.workflow(big, "Submit for Approval")
	expect_error(lambda: bm.workflow(big, "Approve"), "General Manager")
	gm.workflow(big, "Approve")
	entry = d.get("Customer Ledger Entry", d.get("Write Off", big["name"])["ledger_entry"])
	eq((entry["entry_type"], float(entry["amount"])), ("Write-off", -5000.0), "write-off entry")
	eq(owed(), before - 5000, "arrears after the write-off")
	expect_error(lambda: ao.save_doc(dict(wo, amount="99999999")), "most that can be written off")
	expect_error(lambda: ao.save_doc(dict(wo, resolution_no="")))

	reg = rows_of(run_report(gm, "Corrections Register", from_date="2026-01-01", to_date="2030-12-31"))
	if not {"Installment plan", "Write-off"} <= {r["kind"] for r in reg}:
		raise AssertionError("corrections register lacks plans and write-offs")


@case("QA-72", "FS-RUN-10/11/12/13, FS-ACC-10: month close and lock, year end, delivery tracking, run snapshot")
def t_period_controls(d: Desk):
	P = "utility_billing.utility_billing"
	bo, bm, gm, ao = USERS["bo"], USERS["bm"], USERS["gm"], USERS["ao"]
	conn = S["conn"]["name"]

	# the run posted in QA-35 left a snapshot taken before it was posted
	run = S["run"]["name"]
	snaps = bm.s.get(f"{URL}/api/resource/Run Snapshot", params={"filters": json.dumps([["bill_run", "=", run]]),
	                                                              "fields": json.dumps(["name", "status", "bill_count"])}).json()["data"]
	eq([(s["status"], s["bill_count"]) for s in snaps], [("Kept", 1)], "run snapshot")

	# bill delivery: a distributor must be named; the history keeps every step
	expect_error(lambda: bo.call(f"{P}.delivery.mark", run=run, status="Handed to Distributor"), "distributor")
	eq(bo.call(f"{P}.delivery.mark", run=run, status="Handed to Distributor", distributor="Ali Khan")["changed"], 1, "handed over")
	bo.call(f"{P}.delivery.mark", run=run, status="Delivered", remarks="Left with the guard")
	bill = bo.get("Utility Bill", latest_bill(S["conn"]["name"]))  # the bill reissued for QA-69's extension and QA-71's plan
	eq((bill["delivery_status"], bill["distributor"], [e["status"] for e in bill["delivery_log"]][-2:]), ("Delivered", "Ali Khan", ["Handed to Distributor", "Delivered"]), "delivery")  # the message sent at posting comes first
	expect_error(lambda: USERS["cashier"].call(f"{P}.delivery.mark", run=run, status="Delivered"))
	rep = rows_of(run_report(bo, "Bill Delivery Status", bill_run=run))
	eq((rep[-1]["bills"], rep[-1]["delivered"]), (1, 1), "delivery report")

	# month close: the checklist runs, only a manager closes, a closed month is locked
	period = bm.save_doc({"doctype": "Billing Period", "society": S["society"], "period_start": "2026-03-15"})
	eq((period["period_start"], period["period_end"], period["status"]), ("2026-03-01", "2026-03-31", "Open"), "period")
	expect_error(lambda: bm.save_doc({"doctype": "Billing Period", "society": S["society"], "period_start": "2026-03-02"}), "already exists")
	checks = bm.call(f"{P}.periods.preview", period=period["name"])
	if not {"Bill runs approved", "Readings resolved", "Counters closed", "Bank statements matched", "Ledger reconciled"} <= {c["check"] for c in checks}:
		raise AssertionError(f"checklist is missing checks: {[c['check'] for c in checks]}")
	expect_error(lambda: USERS["rec"].call(f"{P}.periods.close", period=period["name"]))
	closed = bm.call(f"{P}.periods.close", period=period["name"])
	eq(closed["status"], "Closed", "closed")
	expect_error(lambda: ao.save_doc({"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": conn, "amount": "100", "mode": "Bank Transfer",
	                                  "payment_date": "2026-03-20", "channel_txn_id": f"LOCK-{RUN}"}, action="Submit"), "closed")
	expect_error(lambda: bm.call(f"{P}.periods.reopen", period=period["name"], reason="I would like it open again"))
	expect_error(lambda: gm.call(f"{P}.periods.reopen", period=period["name"], reason="short"))
	gm.call(f"{P}.periods.reopen", period=period["name"], reason="A payment was dated in the wrong month")
	eq(bm.get("Billing Period", period["name"])["status"], "Open", "reopened")

	# year end needs every month closed, then fixes the balances
	year = bm.save_doc({"doctype": "Year End Close", "society": S["society"], "year_start": "2026-03-01", "year_end": "2026-03-31"})
	expect_error(lambda: bm.call(f"{P}.yearend.close", name=year["name"]), "Close every month")
	bm.call(f"{P}.periods.close", period=period["name"])
	bm.call(f"{P}.yearend.prepare", name=year["name"])
	eq(bm.call(f"{P}.yearend.close", name=year["name"])["status"], "Closed", "year closed")
	expect_error(lambda: gm.call(f"{P}.periods.reopen", period=period["name"], reason="Reopen after the year end"), "year was closed")
	rep = rows_of(run_report(gm, "Period Close Status", society=S["society"]))
	eq([(r["status"], r["reopened"]) for r in rep], [("Closed", 1)], "period report")


@case("QA-73", "FS-MST-09/10/13/14/15/18: reading route, connection flag, security deposit, representatives, Consumer 360")
def t_master_data(d: Desk):
	P = "utility_billing.utility_billing"
	bo, bm, gm, ao = USERS["bo"], USERS["bm"], USERS["gm"], USERS["ao"]
	conn = S["conn"]["name"]
	consumer = bo.get("Connection", conn)["consumer"]

	# a route for the account, with its code kept on the account
	route = bo.save_doc({"doctype": "Reading Route", "society": S["society"], "utility": "Electricity", "route_code": f"R-{RUN}",
	                     "stops": [{"sequence": "1", "stop_type": "Connection", "connection": conn}]})
	eq((route["stops_count"], bo.get("Connection", conn)["route"]), (1, f"R-{RUN}"), "route")
	expect_error(lambda: bo.save_doc({"doctype": "Reading Route", "society": S["society"], "utility": "Electricity", "route_code": f"R2-{RUN}",
	                                  "stops": [{"sequence": "1", "stop_type": "Connection", "connection": conn}]}), "already on route")

	# a flag that holds recovery shows in the account view
	c = bm.get("Connection", conn)
	c["connection_flags"] = [{"flag": "DISPUTE", "from_date": "2026-01-01", "remarks": "Disputes the reading"}]
	bm.save_doc(c)

	# a deposit received and approved by a General Manager
	dep = ao.save_doc({"doctype": "Security Deposit", "connection": conn, "deposit_type": "Received", "amount": "3000", "mode": "Cash",
	                   "posting_date": "2026-11-02", "reference_no": f"DEP-{RUN}"})
	ao.workflow(dep, "Submit for Approval")
	expect_error(lambda: bm.workflow(dep, "Approve"), "General Manager")
	gm.workflow(dep, "Approve")
	eq(float(bo.get("Connection", conn)["security_deposit"]), 3000.0, "deposit held")
	expect_error(lambda: USERS["cashier"].save_doc({"doctype": "Security Deposit", "connection": conn, "deposit_type": "Received", "amount": "10",
	                                                "mode": "Cash", "posting_date": "2026-11-02"}))

	# representatives on the consumer
	cons = bo.get("Consumer", consumer)
	cons["representatives"] = [{"rep_name": "Agent", "relation": "Attorney", "can_pay": "0"}]
	expect_error(lambda: bo.save_doc(cons), "tick at least one")
	cons["representatives"][0]["can_pay"] = "1"
	bo.save_doc(cons)

	# Consumer 360 and the combined statement
	found = bo.call(f"{P}.consumer360.search", text=bo.get("Connection", conn)["reference_no"])
	eq([r["name"] for r in found][:1], [consumer], "search by reference number")
	v = bo.call(f"{P}.consumer360.view", consumer=consumer)
	acc = next(a for a in v["accounts"] if a["name"] == conn)
	eq((acc["flags"], float(acc["security_deposit"]), v["consumer"]["representatives"][0]["name"]), (["Under dispute"], 3000.0, "Agent"), "consumer view")
	expect_error(lambda: USERS["reader"].call(f"{P}.consumer360.view", consumer=consumer))
	rep = run_report(bo, "Consumer Statement (All Accounts)", consumer=consumer)
	if not rows_of(rep):
		raise AssertionError("combined statement is empty")
	rep = rows_of(run_report(ao, "Security Deposits Held", society=S["society"]))
	eq((rep[0]["connection"], rep[0]["held"]), (conn, 3000), "deposits held")


@case("QA-74", "FS-MST-23 to 27, FS-WF-00/22/29/31: grid station, feeder, line section, asset, map; service requests step by step")
def t_network_and_requests(d: Desk):
	P = "utility_billing.utility_billing"
	bo, bm, rec = USERS["bo"], USERS["bm"], USERS["rec"]
	conn = S["conn"]["name"]
	soc = S["society"]

	gs = bm.save_doc({"doctype": "Grid Station", "society": soc, "station_code": f"G{RUN}", "station_name": "Main grid", "capacity_mva": "20",
	                  "latitude": "31.4504", "longitude": "74.2786"})
	expect_error(lambda: bm.save_doc({"doctype": "Grid Station", "society": soc, "station_code": f"H{RUN}", "station_name": "Bad", "latitude": "31.4"}),
	             "both latitude and longitude")
	fd = bm.save_doc({"doctype": "Feeder", "society": soc, "feeder_code": f"F{RUN}", "feeder_name": "Feeder A", "grid_station": gs["name"]})
	bm.save_doc({"doctype": "Line Section", "society": soc, "section_code": f"L{RUN}", "feeder": fd["name"], "length_km": "2.5",
	             "estimated_loss_percent": "4"})
	asset = bo.save_doc({"doctype": "Network Asset", "society": soc, "asset_name": "Pump motor", "asset_type": "Pump", "status": "In service",
	                     "maintenance": [{"maintenance_date": "2026-06-01", "kind": "Preventive", "next_due": "2026-12-01"}]})
	eq(asset["next_maintenance"], "2026-12-01", "next maintenance")
	geo = bm.call(f"{P}.network.geojson", society=soc)
	if not any(f["properties"]["kind"] == "Grid station" for f in geo["features"]):
		raise AssertionError("the grid station is not on the map export")

	# service requests: the standard types, a contact update, a disconnection with maker-checker
	bm.call(f"{P}.servicereq.set_up_types", society=soc)
	sr = bo.save_doc({"doctype": "Service Request", "request_type": f"{soc}-CONTACT", "connection": conn, "new_mobile": "0300-5550000"})
	bo.call(f"{P}.servicereq.advance", request=sr["name"], step="1", remarks="Asked at the counter")
	eq((bo.get("Service Request", sr["name"])["status"], bo.get("Consumer", bo.get("Connection", conn)["consumer"])["mobile_no"]),
	   ("Completed", "0300-5550000"), "contact updated")
	nd = bo.save_doc({"doctype": "Service Request", "request_type": f"{soc}-NODUES", "connection": conn})
	expect_error(lambda: bo.call(f"{P}.servicereq.advance", request=nd["name"], step="1"), "owes")
	eq(bo.get("Service Request", nd["name"])["status"], "Open", "a refused step changes nothing")
	expect_error(lambda: bo.save_doc({"doctype": "Service Request", "request_type": f"{soc}-DISC", "connection": conn, "disconnection_reason": "Defaulter",
	                                  "requested_on": "2026-11-01", "effective_date": "2026-11-02"}), "notice")
	disc = bo.save_doc({"doctype": "Service Request", "request_type": f"{soc}-DISC", "connection": conn, "disconnection_reason": "Defaulter",
	                    "requested_on": "2026-10-20", "effective_date": "2026-11-01"})
	expect_error(lambda: bo.call(f"{P}.servicereq.advance", request=disc["name"], step="1"))  # approved by a Billing Manager
	bm.call(f"{P}.servicereq.advance", request=disc["name"], step="1", remarks="Three notices unanswered")
	expect_error(lambda: bm.call(f"{P}.servicereq.advance", request=disc["name"], step="2"))  # the field step is a Recovery Officer's
	rec.call(f"{P}.servicereq.advance", request=disc["name"], step="2", reading="2500", latitude="31.45", longitude="74.27")
	eq((bo.get("Service Request", disc["name"])["status"], bo.get("Connection", conn)["status"]), ("Completed", "Disconnected"), "disconnected")
	anon = Desk()
	eq(anon.call(f"{P}.servicereq.verify_certificate", code="NOPE1234"), {"valid": False}, "certificate check is public")
	rep = rows_of(run_report(bm, "Service Requests and Service Standards", society=soc, from_date="2026-01-01", to_date="2030-12-31"))
	eq(rep[-1]["completed"], 2, "service standards report")


@case("QA-75", "FS-ACC-01/06/07/09/11/12, FS-CFG-27: user access, approval inbox, delegation, masking, rights, security status, society template")
def t_access(d: Desk):
	P = "utility_billing.utility_billing"
	bo, bm, gm, aud = USERS["bo"], USERS["bm"], USERS["gm"], USERS["auditor"]
	conn = S["conn"]["name"]
	consumer = bo.get("Connection", conn)["consumer"]

	# an auditor limited to this society sees only it; CNIC and phone are masked for them
	ua = d.save_doc({"doctype": "User Access", "user": EMAIL["auditor"], "societies": [{"society": S["society"]}]})
	try:
		socs = aud.s.get(f"{URL}/api/resource/Society", params={"limit_page_length": 0}).json()["data"]
		eq([x["name"] for x in socs], [S["society"]], "auditor sees one society")
		v = aud.call(f"{P}.consumer360.view", consumer=consumer)
		if v["consumer"]["mobile_no"] and "*" not in v["consumer"]["mobile_no"]:
			raise AssertionError(f"mobile number not masked for the auditor: {v['consumer']['mobile_no']}")
	finally:
		d.s.delete(f"{URL}/api/resource/User Access/{ua['name']}")

	# the approval inbox approves in bulk; the maker doesn't see their own item
	adj = bo.save_doc({"doctype": "Account Adjustment", "note_type": "Debit Note", "connection": conn, "bucket": "Arrears", "amount": "250",
	                   "reason_code": "BILL-ERROR", "remarks": "Inbox test adjustment"})
	bo.workflow(adj, "Submit for Approval")
	eq(adj["name"] in {r["name"] for r in gm.call(f"{P}.access.inbox")}, True, "in the inbox")
	out = gm.call(f"{P}.access.bulk", items=json.dumps([{"doctype": "Account Adjustment", "name": adj["name"]}]), action="Approve")
	eq((out["done"], out["failed"]), ([adj["name"]], []), "bulk approve")

	# a delegation lends the General Manager role to the Billing Manager, and cancelling takes it back
	dlg = gm.save_doc({"doctype": "Approval Delegation", "delegator": EMAIL["gm"], "delegate": EMAIL["bm"], "from_date": __import__("datetime").date.today().isoformat(),
	                   "to_date": "2030-01-01", "reason": "Leave"})
	eq(gm.get("Approval Delegation", dlg["name"])["status"], "Active", "delegation active")
	gm.call(f"{P}.access.cancel_delegation", name=dlg["name"])
	eq(gm.get("Approval Delegation", dlg["name"])["status"], "Cancelled", "delegation cancelled")

	# security status is visible; only a System Manager applies the policy (not applied here: it would switch on two-factor sign-in)
	if not gm.call(f"{P}.security.get_status"):
		raise AssertionError("no security status")
	expect_error(lambda: gm.call(f"{P}.security.apply_policy"))
	rights = rows_of(run_report(aud, "Users, Roles and Rights", user=EMAIL["bo"], doctype="Consumer"))
	eq("Create" in rights[0]["rights"], True, "rights report")

	# a new society from the template
	made = d.call(f"{P}.templates.new_society", society_name=f"Template Society {RUN}", short_name=f"TP{RUN}")
	eq((made["society"], len(made["to_do"])), (f"TP{RUN}", 5), "society from template")
	expect_error(lambda: bo.call(f"{P}.templates.new_society", society_name="No", short_name=f"NO{RUN}"))


@case("QA-76", "FS-RPT-03/12/22/25/28/36/38/43: the remaining reports run for their roles; revenue target and channel fees")
def t_reports_more(d: Desk):
	gm, ao, bo, aud = USERS["gm"], USERS["ao"], USERS["bo"], USERS["auditor"]
	soc = S["society"]
	wide = {"society": soc, "from_date": "2026-01-01", "to_date": "2030-12-31"}
	ao.save_doc({"doctype": "Revenue Target", "society": soc, "target_year": "2026",
	             "months": [{"month": "2026-11-01", "billed": "25000", "collected": "20000"}]})
	s = d.get("Society", soc)
	s["channel_fees"] = [{"mode": "Bank Transfer", "fee_percent": "1.5", "fixed_fee": "0", "cap": "100"}]
	d.save_doc(s)
	for user, name, filters in ((gm, "Installment Plans", {"society": soc}), (gm, "Consumer Movement", wide), (gm, "Disconnections and Reconnections", wide),
	                            (bo, "No-Dues Certificates Issued", wide), (bo, "Detection Bills", wide), (bo, "Abnormal Consumption", {"society": soc, "month": "2026-11-01"}),
	                            (ao, "Channel Fees and Commissions", wide), (ao, "Revenue against Target", {"society": soc, "year": "2026"}),
	                            (aud, "Consumption Analytics", {"society": soc, "month": "2026-11-01", "show": "Seasonality"})):
		run_report(user, name, **filters)
	rows = rows_of(run_report(gm, "Disconnections and Reconnections", **wide))
	eq(any(r.get("kind") == "Disconnection" for r in rows), True, "the QA-74 disconnection is listed")
	fees = rows_of(run_report(ao, "Channel Fees and Commissions", **wide))
	bank = next((r for r in fees if r.get("mode") == "Bank Transfer"), None)
	if not bank or not float(bank["fees"]):
		raise AssertionError(f"no channel fee worked out: {fees}")
	budget = rows_of(run_report(ao, "Revenue against Target", society=soc, year="2026"))
	eq(any(float(r.get("budget_billed") or 0) == 25000 for r in budget), True, "target month shown")
	expect_error(lambda: run_report(USERS["reader"], "Channel Fees and Commissions", **wide))


# ---------------------------------------------------------------- increment 21
@case("QA-77", "FS-RA-08/09, FS-MST: duplicate numbers refused at entry; revenue assurance checks run by a manager; a finding closed from the form")
def t_revenue_assurance(d: Desk):
	RA = "utility_billing.utility_billing.revenue_assurance.run_now"
	bm, rec, soc = USERS["bm"], USERS["rec"], S["society"]
	expect_error(lambda: USERS["cashier"].call(RA, society=soc, month="2026-11-01"), "Only a Billing Manager")
	# user, 2026-11-15: a duplicate meter number is refused when it is typed, however it is spelt; the same for other numbers and codes
	d.save_doc({"doctype": "Meter", "serial_no": f"QA{RUN}-DUP", "society": soc, "utility": "Electricity"})
	expect_error(lambda: d.save_doc({"doctype": "Meter", "serial_no": f"qa{RUN} dup", "society": soc, "utility": "Electricity"}), "already used")
	expect_error(lambda: d.save_doc({"doctype": "Meter", "serial_no": f"00QA{RUN}DUP", "society": soc, "utility": "Electricity"}), "already used")
	expect_error(lambda: d.save_doc({"doctype": "Feeder", "society": soc, "feeder_code": "f-1", "feeder_name": "Feeder one again"}), "already used")
	s = d.get("Society", soc)
	d.save_doc(dict(s, ra_adjustment_limit="1"))  # any reversal or adjustment in the month stands out
	out = bm.call(RA, society=soc, month="2026-11-01")
	eq(out["month"], "2026-11-01", "checks ran for November")
	found = d.s.get(f"{URL}/api/resource/RA Finding", params={"filters": json.dumps({"society": soc, "check_type": "RA-08 Unusual reversals or adjustments"}),
	                                                          "fields": json.dumps(["name", "check_type", "status", "subject"])}).json()["data"]
	if not found:
		raise AssertionError(f"no RA-08 finding: {out}")
	eq(found[0]["status"], "Open", "unusual reversals and adjustments are a finding")
	f = rec.get("RA Finding", found[0]["name"])
	expect_error(lambda: rec.save_doc(dict(f, status="Closed")), "result")
	closed = rec.save_doc(dict(f, status="Closed", result="No issue", notes="Reversals were for dishonoured cheques, each with a reason"))
	eq((closed["status"], closed["closed_by"]), ("Closed", EMAIL["rec"]), "closed by the recovery officer")
	again = bm.call(RA, society=soc, month="2026-11-01")
	eq(rec.get("RA Finding", f["name"])["status"], "Closed", "running again doesn't reopen it")
	rows = rows_of(run_report(bm, "Revenue Assurance Findings", society=soc))
	meter_row = next((r for r in rows if r.get("item") == "RA-08 Unusual reversals or adjustments"), None)
	if not meter_row or int(meter_row["closed"]) < 1:
		raise AssertionError(f"findings report: {rows} (second run {again})")
	expect_error(lambda: run_report(USERS["cashier"], "Revenue Assurance Findings", society=soc))


@case("QA-78", "FS-API-01/03/06/07/08/10: a 1Link client signs in with its API key; inquiry, payment once, idempotent v1 payment; docs served")
def t_billing_api(d: Desk):
	soc, conn = S["society"], S["conn"]["name"]
	email = f"qa{RUN}.1link@example.com"
	d.save_doc({"doctype": "User", "email": email, "first_name": f"QA{RUN} 1Link", "send_welcome_email": 0, "user_type": "System User",
	            "roles": [{"role": "API Integration"}]})
	api_secret = d.call("frappe.core.doctype.user.user.generate_keys", user=email)["api_secret"]
	api_key = d.get("User", email)["api_key"]
	d.save_doc({"doctype": "API Client", "client_name": f"QA{RUN} 1Link", "channel": "1Link", "user": email, "enabled": 1, "rate_limit": "100",
	            "scope_account_read": 1, "scope_bill_read": 1, "scope_payment_write": 1, "scope_payment_reverse": 1,
	            "societies": [{"society": soc}], "payment_mode": "1Link"})
	api = Desk()
	api.s.headers["Authorization"] = f"token {api_key}:{api_secret}"
	CH, V1 = f"{URL}/api/method/utility_billing.api.channels", f"{URL}/api/method/utility_billing.api.v1"

	inq = api.s.get(f"{CH}.bill_inquiry", params={"consumer_number": conn, "bank_mnemonic": "MCB"}).json()["message"]
	eq(inq["response_Code"], "00", "inquiry answered")
	eq(api.s.get(f"{CH}.bill_inquiry", params={"consumer_number": "9999999999"}).json()["message"]["response_Code"], "01", "unknown consumer")
	due = int(inq["amount_within_dueDate"][1:]) / 100
	today = time.strftime("%Y%m%d")
	if due > 0:
		pay = {"consumer_number": conn, "tran_auth_id": f"QA{RUN}A1", "transaction_amount": inq["amount_within_dueDate"], "tran_date": today, "tran_time": "101500"}
		eq(api.s.post(f"{CH}.bill_payment", data=pay).json()["message"]["response_Code"], "00", "1Link payment posted")
		eq(api.s.post(f"{CH}.bill_payment", data=pay).json()["message"]["response_Code"], "03", "the same authorisation ID posts once")
		eq(api.s.post(f"{CH}.bill_reversal", data={"consumer_number": conn, "tran_auth_id": f"QA{RUN}A1"}).json()["message"]["response_Code"], "00",
		   "reversed the same day")
	short = {"consumer_number": conn, "tran_auth_id": f"QA{RUN}A2", "transaction_amount": "+0000000000100", "tran_date": today}
	eq(api.s.post(f"{CH}.bill_payment", data=short).json()["message"]["response_Code"], "04", "a part payment is refused for this channel")

	body = {"reference_no": conn, "amount": "1", "paid_on": time.strftime("%Y-%m-%d"), "transaction_id": f"QA{RUN}V1"}
	r = api.s.post(f"{V1}.post_payment", data=body)
	eq(r.status_code, 417, "a write without an Idempotency-Key is refused")
	d.save_doc(dict(d.get("API Client", f"QA{RUN} 1Link"), partial_payments=1))
	first = api.s.post(f"{V1}.post_payment", data=body, headers={"Idempotency-Key": f"QA{RUN}-K1"}).json()["message"]
	again = api.s.post(f"{V1}.post_payment", data=body, headers={"Idempotency-Key": f"QA{RUN}-K1"}).json()["message"]
	eq(first["receipt"], again["receipt"], "a retry with the same key returns the same receipt")
	eq(api.s.post(f"{V1}.post_payment", data=dict(body, amount="2"), headers={"Idempotency-Key": f"QA{RUN}-K1"}).status_code, 409, "key reused for another request")
	eq(api.s.get(f"{V1}.bills", params={"reference_no": conn}).status_code, 200, "bills readable with the bill scope")
	eq(api.s.get(f"{URL}/api/resource/Connection/{conn}").status_code in (403, 404), True, "the integration user can't browse the desk")
	logs = d.s.get(f"{URL}/api/resource/API Call Log", params={"filters": json.dumps({"client": f"QA{RUN} 1Link"}), "limit_page_length": 50}).json()["data"]
	eq(len(logs) >= 8, True, "every call is logged")

	guest = requests.get(f"{V1}.openapi", headers={"Host": SITE})
	eq((guest.status_code, guest.json()["message"]["openapi"]), (200, "3.0.3"), "OpenAPI document is public")
	page = requests.get(f"{URL}/ums-api", headers={"Host": SITE})
	eq((page.status_code, "bill_inquiry" in page.text and "Idempotency-Key" in page.text), (200, True), "documentation page")


@case("QA-79", "Gap 9: opening balances file uploaded, loaded and checked from the form; problems downloadable; can't be submitted with problems")
def t_opening_balances(d: Desk):
	OB = f"{URL}/api/method/utility_billing.utility_billing.opening"
	ao, soc, conn = USERS["ao"], S["society"], S["conn"]["name"]
	t = ao.s.get(f"{OB}.template")
	header = t.text.lstrip("﻿").splitlines()[0].replace('"', "")
	eq((t.status_code, header.startswith("reference_no,current_bill")), (200, True), f"template download ({header[:60]})")
	csv = f"reference_no,current_bill,arrears\n{conn},1000,500\n9999999999,10,0\n"
	up = ao.s.post(f"{URL}/api/method/upload_file", files={"file": ("opening.csv", csv.encode(), "text/csv")}, data={"is_private": "1"})
	if up.status_code != 200:
		raise ServerError(up.status_code, up.text[:300])
	yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
	doc = ao.save_doc({"doctype": "Opening Balance Import", "society": soc, "as_of_date": yesterday, "import_file": up.json()["message"]["file_url"]})
	out = ao.call("utility_billing.utility_billing.opening.load_file", name=doc["name"])
	eq(out, {"rows": 2, "errors": 2}, "both rows have problems")
	doc = ao.get("Opening Balance Import", doc["name"])
	eq(["already has entries" in (doc["rows"][0]["error"] or ""), "no account" in (doc["rows"][1]["error"] or "")], [True, True], "problems per row")
	rep = ao.s.get(f"{OB}.error_report", params={"name": doc["name"]})
	eq((rep.status_code, len(rep.text.strip().splitlines())), (200, 3), "problems report: header and two rows")
	expect_error(lambda: ao.workflow(doc, "Submit for Approval"), "problems")
	expect_error(lambda: USERS["cashier"].save_doc({"doctype": "Opening Balance Import", "society": soc, "as_of_date": yesterday}))
	# increment 48: the step asked for by name, as the form of a large import does; the file handed over with the call; the progress
	act = "utility_billing.utility_billing.opening"
	expect_error(lambda: ao.call(f"{act}.act", name=doc["name"], action="Submit for Approval"), "problems")
	expect_error(lambda: ao.call(f"{act}.act", name=doc["name"], action="Approve"), "isn't available")
	eq(ao.call(f"{act}.load_file", name=doc["name"], file_url=doc["import_file"]), {"rows": 2, "errors": 2}, "Load File with the file's address")
	eq(ao.call(f"{act}.progress", name=doc["name"]), None, "no step is running on it")
	expect_error(lambda: USERS["cashier"].call(f"{act}.progress", name=doc["name"]))


@case("QA-80", "FS-ACC-04, gap 10: approvals in sequence set on the Society; level 1 approves from the form, then the covering level")
def t_approvals_in_turn(d: Desk):
	AP = "utility_billing.utility_billing.approval"
	soc = S["society"]
	s = d.get("Society", soc)
	keep = {"approval_order": s.get("approval_order"), "approval_levels": s.get("approval_levels")}
	d.save_doc(dict(s, approval_order="Each level in turn", approval_levels=[
		{"transaction": "Account Adjustment", "up_to_amount": "50", "approver_role": "Billing Manager"},
		{"transaction": "Account Adjustment", "up_to_amount": "0", "approver_role": "General Manager"}]))
	try:
		note = USERS["bo"].save_doc({"doctype": "Account Adjustment", "note_type": "Debit Note", "connection": S["conn"]["name"], "bucket": "Current bill",
		                             "amount": "60", "reason_code": "BILL-ERROR", "remarks": "Meter charge missed on the bill"})
		note = USERS["bo"].workflow(note, "Submit for Approval")
		st = USERS["gm"].call(f"{AP}.level_status", doctype="Account Adjustment", name=note["name"])
		eq((st["in_turn"], st["chain"], st["next_role"]), (True, ["Billing Manager", "General Manager"], "Billing Manager"), "two levels in turn")
		expect_error(lambda: USERS["gm"].workflow(note, "Approve"), "level 1")
		inbox = [x["name"] for x in USERS["gm"].call("utility_billing.utility_billing.access.inbox")]
		eq(note["name"] in inbox, False, "not in the General Manager's inbox before level 1")
		eq(USERS["bm"].call(f"{AP}.approve_level", doctype="Account Adjustment", name=note["name"])["next_role"], "General Manager", "level 1 approved")
		eq(USERS["bm"].get("Account Adjustment", note["name"])["workflow_state"], "Pending Approval", "still pending after level 1")
		note = USERS["gm"].workflow(note, "Approve")
		eq((note["workflow_state"], note["docstatus"]), ("Approved", 1), "approved by the covering level")
	finally:
		d.save_doc(dict(d.get("Society", soc), **keep))


@case("QA-81", "FS-CLT-07/09/10: signatories and branding history saved from the Society form; onboarding checklist and tasks")
def t_client_setup(d: Desk):
	OB = "utility_billing.utility_billing.onboarding"
	soc = S["society"]
	s = d.get("Society", soc)
	overlap = [{"signatory_name": "A", "designation": "GM", "for_bill": 1, "valid_from": "2026-01-01"},
	           {"signatory_name": "B", "designation": "BM", "for_bill": 1, "valid_from": "2026-06-01"}]
	expect_error(lambda: d.save_doc(dict(s, signatories=overlap)), "overlapping")
	saved = d.save_doc(dict(s, signatories=[{"signatory_name": f"QA Signatory {RUN}", "designation": "General Manager", "for_bill": 1, "for_receipt": 1,
	                                         "for_notice": 1, "valid_from": "2026-01-01"}],
	                        branding_versions=[{"effective_from": "2026-01-01", "brand_colour": "#7A1F5F", "footer_text": f"QA footer {RUN}"}]))
	eq((len(saved["signatories"]), len(saved["branding_versions"])), (1, 1), "signatory and branding version saved")
	name = USERS["gm"].call(f"{OB}.open_for", society=soc)
	check = USERS["gm"].call(f"{OB}.checklist", society=soc)
	names = {c["check"]: c["done"] for c in check["checks"]}
	eq((names.get("Signatory for bills"), names.get("Tariffs approved"), check["total"] >= 14), (True, True, True), "checklist from the setup")
	ob = USERS["bm"].get("Society Onboarding", name)
	ob["tasks"][0]["done"] = 1
	ob = USERS["bm"].save_doc(ob)
	eq((ob["tasks"][0]["done_by"], float(ob["percent_complete"]) > 0), (EMAIL["bm"], True), "task ticked with who and when")
	expect_error(lambda: USERS["cashier"].call(f"{OB}.open_for", society=soc))


# ---------------------------------------------------------------- increment 22
@case("QA-82", "FS-WF-08, FS-BIL-02, SC-13: defective-meter, disconnection and split settings saved from the Society form; their reports")
def t_completeness_settings(d: Desk):
	soc = S["society"]
	s = d.get("Society", soc)
	saved = d.save_doc(dict(s, defective_billing_method="Load × load factor × hours", defective_average_months="3", defective_max_months="2",
	                        defective_load_factor="35", defective_hours="720", disconnected_fixed_charges="Continue",
	                        tariff_split_method="By the change-day reading, else by days"))
	eq((saved["defective_billing_method"], int(saved["defective_max_months"]), saved["disconnected_fixed_charges"], saved["tariff_split_method"]),
	   ("Load × load factor × hours", 2, "Continue", "By the change-day reading, else by days"), "settings saved")
	eq(USERS["bm"].get("Reading Exception Code", "Defective")["billing_treatment"], "Defective meter", "Defective is billed by the defective-meter rules")
	run_report(USERS["bm"], "Defective Meters", society=soc)
	run_report(USERS["rec"], "Former Occupant Balances", society=soc, status="Open")
	expect_error(lambda: run_report(USERS["cashier"], "Defective Meters", society=soc))
	d.save_doc(dict(d.get("Society", soc), disconnected_fixed_charges="Stop from the disconnection date"))


@case("QA-83", "FS-BIL-06, FS-MST-20/21, FS-CFG-03: maintenance by plot size without a meter; a billing account's combined bill paid with its number")
def t_flexibility(d: Desk):
	soc = S["society"]
	p = d.save_doc({"doctype": "Property", "society": soc, "property_no": f"M {RUN}", "sector": "M", "property_type": "House", "status": "Occupied",
	                "area": "10", "area_unit": "Marla", "plot_size_class": "10 Marla",
	                "occupants": [{"consumer": S["owner"], "occupant_role": "Owner", "from_date": "2020-01-01", "share_percent": "100",
	                               "is_billed": 1, "liable_for_arrears": 1}]})
	ct = d.save_doc({"doctype": "Charge Type", "charge_name": f"Maintenance {RUN}", "charge_group": "Society", "calculation_basis": "By Plot Size",
	                 "bill_order": "30"})["name"]
	cat = d.save_doc({"doctype": "Consumer Category", "category_name": f"Maintenance {RUN}", "utility": "Maintenance"})["name"]
	t = USERS["bm"].save_doc({"doctype": "Tariff", "naming_series": "TRF-.YYYY.-.####", "society": soc, "utility": "Maintenance", "consumer_category": cat,
	                          "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": "0", "minimum_charge": "0",
	                          "charges": [{"charge_type": ct}],
	                          "plot_rates": [{"charge_type": ct, "plot_size_class": "10 Marla", "rate": "2500"}]})
	t = USERS["gm"].workflow(USERS["bm"].workflow(t, "Submit for Approval"), "Approve")
	eq(t["docstatus"], 1, "maintenance tariff approved without a rate per unit")
	maint = d.save_doc({"doctype": "Connection", "society": soc, "property": p["name"], "consumer": S["owner"], "utility": "Maintenance",
	                    "consumer_category": cat, "status": "Active"})["name"]
	S["maint_conn"] = maint
	ba = d.save_doc({"doctype": "Billing Account", "society": soc, "consumer": S["owner"], "bill_mode": "One combined bill",
	                 "connections": [{"connection": maint}]})
	eq((ba["name"][0], len(ba["name"])), ("9", 10), "billing account number")
	expect_error(lambda: d.save_doc({"doctype": "Billing Account", "society": soc, "consumer": S["owner"], "connections": [{"connection": maint}]}),
	             "billing account")
	run = USERS["bo"].save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": soc, "utility": "Maintenance",
	                            "billing_month": "2026-12-01", "issue_date": "2027-01-01"})
	USERS["bo"].call(f"{BIL}.calculate_run", run=run["name"])
	run = USERS["bo"].get("Bill Run", run["name"])
	eq((run["calculation_status"], run["bill_count"]), ("Calculated", 1), "one maintenance bill, no reading needed")
	USERS["bm"].workflow(USERS["bo"].workflow(run, "Submit for Approval"), "Approve")
	cbs = d.s.get(f"{URL}/api/resource/Combined Bill", params={"filters": json.dumps([["billing_account", "=", ba["name"]]]),
	                                                           "fields": json.dumps(["name", "payable_within_due"])}).json()["data"]
	eq([float(c["payable_within_due"]) for c in cbs], [2500.0], "combined bill made when the run was posted")
	_reader, text = get_pdf_at(USERS["bm"], "utility_billing.utility_billing.billaccounts.download_combined", name=cbs[0]["name"])
	for part in ("Combined Bill", ba["name"], "Maintenance Bill", "2,500"):
		if part not in text:
			raise AssertionError(f"{part!r} missing from the combined bill PDF")
	r = d.save_doc({"doctype": "Payment Receipt", "payment_channel": "Bank / Channel", "reference_no": ba["name"], "amount": "2500",
	                "mode": "Bank Transfer", "channel_txn_id": f"QA83-{RUN}"})
	r = d.save_doc(r, action="Submit")
	eq((r["billing_account"], [(a["connection"], float(a["amount"])) for a in r["allocations"]]), (ba["name"], [(maint, 2500.0)]), "shared payment")
	eq(d.call(f"{ACC}.enquiry", reference_no=maint)["amount_due"], 0.0, "maintenance paid")


# ---------------------------------------------------------------- increment 23
@case("QA-84", "FS-MST-16, FS-RDG-13, gap 13: account changes only by request; reading dashboard; meter store and its reports")
def t_lifecycle_store(d: Desk):
	soc = S["society"]
	saved = d.save_doc(dict(d.get("Society", soc), changes_through_requests=1))
	eq(saved["changes_through_requests"], 1, "society setting saved")
	active = d.s.get(f"{URL}/api/resource/Connection", params={"filters": json.dumps([["society", "=", soc], ["status", "=", "Active"]]),
	                                                           "limit_page_length": 1}).json()["data"]
	name = active[0]["name"]  # the lock is on active accounts (an earlier case disconnects the first one)
	conn = USERS["bm"].get("Connection", name)
	expect_error(lambda: USERS["bm"].save_doc(dict(conn, sanctioned_load_kw="9")), "service request")
	USERS["bm"].save_doc(dict(USERS["bm"].get("Connection", name), phase="Three"))  # other details still change on the form
	dash = USERS["bo"].call("utility_billing.utility_billing.reading_dashboard.get_reading_dashboard", society=soc)
	eq((dash["society"], "cards" in dash or dash.get("no_cycle")), (soc, True), "reading dashboard")
	expect_error(lambda: USERS["cashier"].call("utility_billing.utility_billing.reading_dashboard.get_reading_dashboard", society=soc))
	got = USERS["bo"].save_doc({"doctype": "Meter Store Movement", "society": soc, "utility": "Electricity", "movement_type": "Received",
	                            "posting_date": "2026-11-16", "supplier": "Acme", "reference": f"PO-{RUN}",
	                            "items": [{"serial_no": f"QA-{RUN}-S{n}", "make": "Acme", "meter_type": "Single Phase"} for n in (1, 2)]})
	got = USERS["bo"].save_doc(got, action="Submit")
	meters = [r["meter"] for r in got["items"]]
	eq([d.get("Meter", m)["status"] for m in meters], ["In Store", "In Store"], "meters received into the store")
	out = USERS["bo"].save_doc({"doctype": "Meter Store Movement", "society": soc, "utility": "Electricity", "movement_type": "Issued",
	                            "posting_date": "2026-11-16", "issued_to": "Installer A", "items": [{"meter": meters[0]}]})
	USERS["bo"].save_doc(out, action="Submit")
	eq((d.get("Meter", meters[0])["status"], d.get("Meter", meters[0])["issued_to"]), ("Issued", "Installer A"), "meter issued")
	expect_error(lambda: USERS["bo"].save_doc({"doctype": "Meter Store Movement", "society": soc, "utility": "Electricity", "movement_type": "Scrapped",
	                                          "posting_date": "2026-11-16", "reason": "test", "items": [{"meter": meters[0]}]}), "Issued")
	stock = rows_of(run_report(USERS["bm"], "Meter Stock"))
	eq(sum(int(r.get("Issued") or 0) for r in stock) >= 1, True, "stock shows the issued meter")
	run_report(USERS["bm"], "Meter Store Movements", from_date="2026-01-01", to_date="2026-12-31")
	expect_error(lambda: run_report(USERS["cashier"], "Meter Stock"))


# ---------------------------------------------------------------- increment 24
@case("QA-85", "FS-SAS-02/03, gap 14: a site's usage reaches the control site with its key; invoice, PDF and payment; AI off by default")
def t_licence_and_ai(d: Desk):
	import datetime

	ai, lic = "utility_billing.utility_billing.ai", "utility_billing.utility_billing.licensing"
	last = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).replace(day=1).isoformat()
	settings = d.get("Licence Settings", "Licence Settings")
	d.save_doc(dict(settings, mode="The SoftVerx control site", seller_name="SoftVerx", invoice_day="1"))
	try:
		client = d.save_doc({"doctype": "Licence Client", "client_name": f"QA Client {RUN}", "rate_per_account": "25", "monthly_minimum": "1000",
		                     "currency": "PKR", "tax_percent": "16", "payment_days": "15", "start_date": "2026-01-01"})["name"]
		key = d.call(f"{lic}.new_key", client=client)["key"]
		site = requests.Session()  # a society's site: no login, only its licence key
		site.headers.update({"Host": SITE, "Accept": "application/json"})
		bad = site.post(f"{URL}/api/method/{lic}.receive_usage", json={"month": last, "lines": []}, headers={"X-UMS-Licence-Key": "ums_wrong"})
		eq(bad.status_code in (401, 403), True, f"a wrong key is refused (HTTP {bad.status_code})")
		ok = site.post(f"{URL}/api/method/{lic}.receive_usage", headers={"X-UMS-Licence-Key": key},
		               json={"month": last, "site": "qa-society", "lines": [{"society": S["society"], "utility": "Electricity", "accounts": 120}]})
		eq((ok.status_code, ok.json()["message"]["accepted"], ok.json()["message"]["accounts"]), (200, True, 120), "usage received")
		usage = ok.json()["message"]["usage"]
		inv = d.save_doc({"doctype": "Licence Invoice", "client": client, "month": last, "usage": usage, "accounts": "120",
		                  "invoice_date": datetime.date.today().isoformat()})
		inv = d.save_doc(inv, action="Submit")
		eq((float(inv["amount"]), int(inv["minimum_applied"]), float(inv["total"]), inv["status"]), (3000.0, 0, 3480.0, "Unpaid"), "invoice")
		_reader, text = get_pdf_at(d, f"{lic}.download_invoice", name=inv["name"])
		for part in ("SoftVerx", f"QA Client {RUN}", inv["name"], "3,480.00"):
			if part not in text:
				raise AssertionError(f"{part!r} missing from the licence invoice PDF")
		pay = d.save_doc(d.save_doc({"doctype": "Licence Payment", "client": client, "amount": "3480", "reference": f"QA85-{RUN}"}), action="Submit")
		eq((d.get("Licence Invoice", inv["name"])["status"], float(pay["unallocated"])), ("Paid", 0.0), "invoice paid")
		rows = rows_of(d.call("frappe.desk.query_report.run", report_name="Licence Receivables", filters={}))
		eq(any(r.get("client") == client for r in rows), True, "receivables list the client")
	finally:
		d.save_doc(dict(d.get("Licence Settings", "Licence Settings"), mode="A society's site"))
	# AI: off until a society switches it on; nothing is sent
	got = USERS["bm"].call(f"{ai}.insights", society=S["society"])
	eq((got["ai"], got["summary"], bool(got["facts"])), (False, None, True), "insights are the rule facts only")
	expect_error(lambda: USERS["bm"].call(f"{ai}.ask", society=S["society"], question="Who owes us the most?"), "switched on")
	run_report(USERS["rec"], "Theft Suspicion", min_score="0")
	expect_error(lambda: run_report(USERS["cashier"], "Theft Suspicion"))


@case("QA-86", "Increment 25: action rights decided by the society; Society Users; a record printed to PDF on any server (bug report 1)")
def t_rights_and_users(d: Desk):
	rights, users = "utility_billing.utility_billing.rights", "utility_billing.utility_billing.users"
	m = d.call(f"{rights}.matrix")
	eq(("Access Manager" in m["roles"], "System Manager" in m["roles"], m["can_edit"]), (True, False, True), "the Role Rights screen")
	USERS["cashier"].call("utility_billing.utility_billing.cashdesk.state")  # the cashier may use the cash counter
	d.call(f"{rights}.set_right", action="cash.counter", role="Cashier", allowed=0)
	try:
		expect_error(lambda: USERS["cashier"].call("utility_billing.utility_billing.cashdesk.state"))
	finally:
		d.call(f"{rights}.set_right", action="cash.counter", role="Cashier", allowed=1)
	USERS["cashier"].call("utility_billing.utility_billing.cashdesk.state")
	expect_error(lambda: USERS["cashier"].call(f"{rights}.set_right", action="cash.counter", role="Meter Reader", allowed=1))
	# a user made on the Society Users screen, with the values the form sends
	email = f"qa86.{RUN}@example.com"
	out = d.call(f"{users}.save_user", data=json.dumps({"email": email, "full_name": f"QA86 {RUN}", "roles": ["Cashier"],
	                                                    "societies": [S["society"]], "enabled": 1}))
	row = next(u for u in out["users"] if u["name"] == email)
	eq((row["roles"], row["societies"]), (["Cashier"], [S["society"]]), "user created with role and society")
	expect_error(lambda: d.call(f"{users}.save_user", data=json.dumps({"email": email, "roles": ["System Manager"], "societies": [S["society"]]})))
	# Print > PDF of any record (Frappe's own print path, with the PDF fallbacks)
	_reader, text = get_pdf_at(d, "frappe.utils.print_format.download_pdf", doctype="Connection", name=S["conn"]["name"])
	eq(S["conn"]["name"] in text, True, "the connection's PDF")


@case("QA-87", "Review 2026-11-16: a user limited to one society can't reach another society's data (FS-ACC-01)")
def t_society_isolation(d: Desk):
	other = "QA-ISO"
	if d.s.get(f"{URL}/api/resource/Society/{other}").status_code != 200:
		d.save_doc({"doctype": "Society", "society_name": "QA Isolation Society", "short_name": other})
	user = "qa.iso@example.com"
	name = d.call("frappe.client.get_value", doctype="User Access", filters=json.dumps({"user": user}), fieldname="name")
	ua = d.get("User Access", name["name"]) if name else {"doctype": "User Access", "user": user}
	ua.update({"all_societies": 0, "societies": [{"society": S["society"]}]})
	d.save_doc(ua)
	iso = USERS["iso"]
	dash = "utility_billing.utility_billing.dashboard.get_dashboard"
	eq({x["name"] for x in iso.call(dash)["societies"]}, {S["society"]}, "the dashboard lists only the user's society")
	expect_error(lambda: iso.call(dash, society=other), "access")
	report = "frappe.desk.query_report.run"
	iso.call(report, report_name="Billing Summary by Cycle", filters=json.dumps({"society": S["society"]}))
	expect_error(lambda: iso.call(report, report_name="Billing Summary by Cycle", filters=json.dumps({"society": other})), "access")
	expect_error(lambda: iso.call("utility_billing.utility_billing.reading_review.queue", society=other), "access")
	expect_error(lambda: iso.call("utility_billing.utility_billing.choices.values", kind="block", society=other), "access")
	d.call(dash, society=other)  # an unlimited user still opens any society


@case("QA-88", "Review 2026-11-16: PDFs carry their page header and footer on every page (report and record PDFs)")
def t_pdf_header_footer(d: Desk):
	from pypdf import PdfReader

	html = ('<div id="header-html"><b>QA88 HEADER</b></div><div id="footer-html"><span>QA88 FOOTER</span></div>'
	        + "<h1>QA-88</h1>" + "<p>line</p>" * 200)
	r = d.s.post(f"{URL}/api/method/frappe.utils.print_format.report_to_pdf", data={"html": html, "orientation": "Portrait"})
	if r.status_code != 200 or not r.content.startswith(b"%PDF"):
		raise ServerError(r.status_code, r.text[:300])
	pages = [p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages]
	eq((len(pages) > 1, all("QA88 HEADER" in p and "QA88 FOOTER" in p for p in pages)), (True, True), "header and footer on every page")


BD = "utility_billing.utility_billing.bill_designer"


@case("QA-89", "Increment 27: a ready-made layout chosen, customised and saved by a Billing Manager, approved by a General Manager (maker-checker)")
def t_bill_designer(d: Desk):
	bm, gm = USERS["bm"], USERS["gm"]
	data = bm.call(f"{BD}.designer_data")
	eq(sorted(s["key"] for s in data["starters"]), ["classic_boxed", "compact_grid", "standard", "two_copies"], "ready-made layouts")
	eq({b["type"] for b in data["blocks"]} >= {"header", "details", "charges", "totals", "history", "payment", "text"}, True, "blocks offered")
	st = bm.call(f"{BD}.starter", key="two_copies")
	layout = st["layout"]
	# customise: a free-text block with placeholders, as the designer sends it (all values as strings)
	layout["sections"][0]["rows"][-1]["cells"][0]["blocks"].append({"type": "text", "options": {"text": "Dear {consumer_name}, pay by {due_date}.", "size": "10", "bold": "1"}})
	html = bm.call(f"{BD}.preview", society=S["society"], layout=json.dumps(layout), page=json.dumps(st["page"]))
	eq(("Bank Copy" in html, "Dear Muhammad Ashiq, pay by" in html), (True, True), "live preview with the placeholders filled")
	values = dict({k: str(v) for k, v in st["page"].items()}, template_name=f"QA89 {RUN}", society=S["society"], effective_from="2026-01-01", layout=layout)
	name = bm.call(f"{BD}.save_template", values=json.dumps(values))
	doc = d.get("Bill Template", name)
	eq((doc["docstatus"], doc["workflow_state"], doc["font_size"], doc["pages_per_bill"]), (0, "Draft", 7.5, "1"), "saved as a one-page draft")
	fit = bm.call(f"{BD}.fit_check", society=S["society"], layout=json.dumps(layout), page=json.dumps(st["page"]))
	eq((fit["limit"], fit["long"]), (1, 1), "fits a long bill on one page")
	expect_error(lambda: bm.call(f"{BD}.save_template", values=json.dumps(dict(values, layout={"sections": [{"rows": [{"cells": [
		{"width": "70", "blocks": []}, {"width": "40", "blocks": []}]}]}]}))), "100%")
	bm.workflow(doc, "Submit for Approval")
	expect_error(lambda: bm.workflow(doc, "Approve"))  # the maker can't approve
	gm.workflow(doc, "Approve")
	doc = d.get("Bill Template", name)
	eq((doc["docstatus"], doc["workflow_state"]), (1, "Approved"), "approved")
	expect_error(lambda: bm.call(f"{BD}.save_template", values=json.dumps(dict(values, name=name))), "Amend")
	S["template"] = name


@case("QA-90", "Increment 27: preview and exact PDF of a real bill; an issued bill keeps its format; another society's designer refused")
def t_bill_designer_print(d: Desk):
	from pypdf import PdfReader

	bm = USERS["bm"]
	st = bm.call(f"{BD}.starter", key="compact_grid")
	ref = d.get("Connection", d.get("Utility Bill", S["bill"])["connection"])["reference_no"]
	html = bm.call(f"{BD}.preview", society=S["society"], layout=json.dumps(st["layout"]), page=json.dumps(st["page"]), bill=S["bill"])
	eq((ref in html, S["bill"] in html), (True, True), "a real bill in the preview")
	r = bm.s.post(f"{URL}/api/method/{BD}.preview_pdf", data={"society": S["society"], "layout": json.dumps(st["layout"]), "page": json.dumps(st["page"]),
	                                                         "bill": S["bill"]})
	if r.status_code != 200 or not r.content.startswith(b"%PDF"):
		raise ServerError(r.status_code, r.text[:300])
	pages = PdfReader(io.BytesIO(r.content)).pages
	text = " ".join(" ".join(p.extract_text() for p in pages).split())
	eq((len(pages), ref in text, "Office Copy" in text), (1, True, True), "the exact PDF, one page")
	_, text = get_pdf(USERS["cashier"], "download_bill", name=S["bill"])  # superseded since QA-69 (increment 37): marked so, otherwise exact
	eq((" ".join(text.replace("SUPERSEDED", " ").split()), "SUPERSEDED" in text), (S["bill_text"], True),
	   "the bill issued before the template still reprints exactly, marked superseded")
	# increment 38 (user, 2026-11-21): Frappe's print view prints a superseded bill too; other cancelled documents are still refused
	r = USERS["cashier"].s.get(f"{URL}/printview", params={"doctype": "Utility Bill", "name": S["bill"]})
	eq((r.status_code, "SUPERSEDED" in r.text, "Not allowed to print cancelled" in r.text), (200, True, False), "the print view prints the superseded bill")
	gone = d.s.get(f"{URL}/api/resource/Payment Receipt", params={"filters": json.dumps([["docstatus", "=", 2]]), "limit_page_length": 1}).json()["data"]
	if not gone:
		raise AssertionError("expected a reversed (cancelled) payment receipt from the payment reversal case")
	r = d.s.get(f"{URL}/printview", params={"doctype": "Payment Receipt", "name": gone[0]["name"]})
	eq("Not allowed to print cancelled documents" in r.text, True, "a cancelled payment receipt still doesn't print")
	expect_error(lambda: USERS["iso"].call(f"{BD}.preview", society="QA-ISO", layout=json.dumps(st["layout"])), "access")
	r = Desk().s.post(f"{URL}/api/method/{BD}.preview", data={"society": S["society"], "layout": json.dumps(st["layout"])})
	eq(r.status_code in (401, 403), True, "a visitor can't preview")


POOL = "utility_billing.utility_billing.doctype.solar_rebate_pool.solar_rebate_pool"


@case("QA-91", "Increment 28, spec test case 2 over HTTP: a solar consumer on the society rebate pool, every value entered as the forms send it")
def t_net_metering_pool(d: Desk):
	bm, gm, bo = USERS["bm"], USERS["gm"], USERS["bo"]
	soc = d.save_doc({"doctype": "Society", "society_name": f"QA Solar Society {RUN}", "short_name": f"qn{RUN}", "enable_electricity": 1,
	                  "days_to_due_date": "8", "surcharge_basis": "Percentage of current bill", "surcharge_rate": "10", "rounding_rule": "Nearest rupee",
	                  "net_metering_method": "Society rebate pool", "nm_credit_rounding": "Nearest unit (half up)", "nm_adjustment_units": "Net units",
	                  "charges_fpa": 0, "charges_qta": 0})  # test case 2: this society bills no FPA or QTA
	s = soc["name"]
	eq((soc["net_metering_method"], soc["nm_credit_rounding"]), ("Society rebate pool", "Nearest unit (half up)"), "net-metering settings saved")
	energy = d.save_doc({"doctype": "Charge Type", "charge_name": f"NM Energy {RUN}", "charge_group": "Electricity", "calculation_basis": "Per Unit",
	                     "bill_order": "10", "bill_label": "Electricity", "rounding_rule": "Nearest rupee", "nm_units_basis": "Net units"})["name"]
	society = d.save_doc({"doctype": "Charge Type", "charge_name": f"NM Society {RUN}", "charge_group": "Society", "calculation_basis": "Fixed",
	                      "bill_order": "40", "bill_label": "Society charges", "rounding_rule": "Nearest rupee"})["name"]
	d.save_doc(dict(d.get("Society", s), energy_charge_type=energy))
	cat = d.save_doc({"doctype": "Consumer Category", "category_name": f"Domestic NM {RUN}", "utility": "Electricity"})["name"]
	t = bm.save_doc({"doctype": "Tariff", "naming_series": "TRF-.YYYY.-.####", "society": s, "utility": "Electricity", "consumer_category": cat,
	                 "effective_from": "2026-01-01", "pricing_method": "Flat rate", "flat_rate": "67", "minimum_charge": "0",
	                 "charges": [{"charge_type": society, "rate": "4850"}]})
	gm.workflow(bm.workflow(t, "Submit for Approval"), "Approve")
	owner = d.save_doc({"doctype": "Consumer", "naming_series": "CON-.#####", "consumer_name": f"QA Solar Owner {RUN}"})["name"]
	prop = d.save_doc({"doctype": "Property", "society": s, "property_no": "59-C", "property_type": "House", "status": "Occupied", "area": "1",
	                   "area_unit": "Kanal"})["name"]
	m = d.save_doc({"doctype": "Meter", "serial_no": f"QA-{RUN}-NM", "society": s, "utility": "Electricity", "meter_type": "Net Metering",
	                "status": "In Store", "digits": "5", "multiplier": "1", "registers": "2"})["name"]
	conn = d.save_doc({"doctype": "Connection", "society": s, "property": prop, "consumer": owner, "utility": "Electricity", "consumer_category": cat,
	                   "status": "Active", "phase": "Single", "sanctioned_load_kw": "11",
	                   "meters": [{"meter": m, "installed_on": "2026-07-01", "start_reading": "16538", "start_export_reading": "20111"}]})["name"]
	cycle = bo.save_doc({"doctype": "Reading Cycle", "society": s, "utility": "Electricity", "billing_month": "2026-09-01"})["name"]
	bo.call(f"{RDG}.create_reading_sheet", cycle=cycle)
	rows = bo.s.get(f"{URL}/api/resource/Meter Reading", params={"filters": json.dumps([["reading_cycle", "=", cycle]]), "fields": json.dumps(["name"])}).json()["data"]
	r = bo.get("Meter Reading", rows[0]["name"])
	eq((r["net_metering"], float(r["previous_export_reading"])), (1, 20111.0), "the reading sheet knows the export register")
	r = bo.save_doc(dict(r, present_reading="17517", present_export_reading="21019", reading_date="2026-09-08"))
	eq((float(r["units"]), float(r["export_units"])), (979.0, 908.0), "imported and exported units")
	if r["status"] == "Flagged":
		bo.call(f"{RDG}.approve_reading", name=r["name"], note="First reading on the new meter")
	bo.call(f"{RDG}.close_cycle", cycle=cycle)
	run = bo.save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": s, "utility": "Electricity",
	                   "billing_month": "2026-09-01", "issue_date": "2026-10-11"})
	bo.call(f"{BIL}.calculate_run", run=run["name"])
	run = bo.get("Bill Run", run["name"])
	eq((run["bill_count"], "solar rebate pool" in (run["problems"][0]["problem"] if run["problems"] else "")), (0, True),
	   "solar accounts wait for the month's approved pool")
	pool = bo.save_doc({"doctype": "Solar Rebate Pool", "naming_series": "SRP-.YYYY.-.#####", "society": s, "utility": "Electricity",
	                    "billing_month": "2026-09-15", "residents_import_units": "913289", "disco_import_units": "787200",
	                    "residents_export_units": "408159", "disco_export_units": "213600"})
	eq((float(pool["pool_units"]), float(pool["losses_units"]), round(float(pool["credit_factor"]), 4)), (126089.0, 68470.0, 0.3089), "pool worked out")
	pool = bo.workflow(pool, "Submit for Approval")
	expect_error(lambda: bo.workflow(pool, "Approve"))  # the maker can't approve
	pool = gm.workflow(pool, "Approve")
	eq((pool["docstatus"], pool["workflow_state"]), (1, "Approved"), "pool approved by the General Manager")
	bo.call(f"{BIL}.calculate_run", run=run["name"])
	bills = bo.s.get(f"{URL}/api/resource/Utility Bill", params={"filters": json.dumps([["bill_run", "=", run["name"]]]), "fields": json.dumps(["name"])}).json()["data"]
	b = bo.get("Utility Bill", bills[0]["name"])
	eq((float(b["import_units"]), float(b["export_units"]), float(b["credited_units"]), float(b["units"])), (979.0, 908.0, 281.0, 698.0), "units")
	eq((float(b["current_amount"]), float(b["surcharge_amount"]), float(b["payable_after_due"])), (51616.0, 5162.0, 56778.0), "test case 2 amounts")
	run = bm.workflow(bo.workflow(bo.get("Bill Run", run["name"]), "Submit for Approval"), "Approve")
	reader, text = get_pdf(USERS["cashier"], "download_bill", name=b["name"])
	eq((len(reader.pages), "Export (solar)" in text, "281 credited" in text, "56,778" in text), (1, True, True, True), "the bill PDF shows the solar credit")
	data = d.call("frappe.desk.query_report.run", report_name="Net Metering Register", filters=json.dumps({"society": s, "month": "2026-09-01"}))
	row = [x for x in data["result"] if isinstance(x, dict) and x.get("connection") == conn][0]
	eq((row["credited_units"], row["units_billed"]), (281, 698), "the register")
	solar = d.call("utility_billing.utility_billing.consumer360.view", consumer=owner, account=conn)["overview"]["bill"]["solar"]
	eq((solar["imported"], solar["exported"], solar["credited"]), (979.0, 908.0, 281.0), "Consumer 360 shows the solar figures")
	S["nm_society"], S["nm_conn"] = s, conn


@case("QA-92", "Increment 28: the reading upload takes an Export Reading column; the next month continues from the last export reading")
def t_net_metering_readings(d: Desk):
	bo = USERS["bo"]
	s = S["nm_society"]
	cycle = bo.save_doc({"doctype": "Reading Cycle", "society": s, "utility": "Electricity", "billing_month": "2026-10-01"})["name"]
	bo.call(f"{RDG}.create_reading_sheet", cycle=cycle)
	csv = f"Reference No,Present Reading,Export Reading,Reading Date\n{S['nm_conn']},17800,21500,2026-10-08\n{S['conn']['name']},310,5,2026-10-08\n"
	up = bo.s.post(f"{URL}/api/method/upload_file", files={"file": ("nm.csv", csv.encode())},
	               data={"is_private": 1, "doctype": "Reading Cycle", "docname": cycle})
	res = bo.call(f"{RDG}.upload_readings", cycle=cycle, file_url=up.json()["message"]["file_url"])
	eq(res["updated"], 1, "the solar meter's row applied")  # the other row is another society's account: not in this cycle
	rows = bo.s.get(f"{URL}/api/resource/Meter Reading", params={"filters": json.dumps([["reading_cycle", "=", cycle]]),
	                                                             "fields": json.dumps(["units", "export_units", "previous_export_reading"])}).json()["data"]
	eq((float(rows[0]["previous_export_reading"]), float(rows[0]["units"]), float(rows[0]["export_units"])), (21019.0, 283.0, 481.0),
	   "October continues from September's registers")


MIG = "utility_billing.utility_billing.doctype.migration_batch.migration_batch"
PRM = "utility_billing.utility_billing.doctype.parallel_run_month.parallel_run_month"


def _upload_xlsx(desk, sheets: dict, doctype, docname) -> str:
	"""An .xlsx built like a society's export, uploaded as the form's Attach field does; returns its file_url."""
	from openpyxl import Workbook

	wb = Workbook()
	wb.remove(wb.active)
	for name, rows in sheets.items():
		ws = wb.create_sheet(name)
		for r in rows:
			ws.append(r)
	out = io.BytesIO()
	wb.save(out)
	up = desk.s.post(f"{URL}/api/method/upload_file", files={"file": (f"migration-{RUN}-{random.randint(100, 999)}.xlsx", out.getvalue())},
	                 data={"is_private": 1, "doctype": doctype, "docname": docname})
	if up.status_code != 200:
		raise AssertionError(f"upload failed: HTTP {up.status_code} {up.text[:200]}")
	return up.json()["message"]["file_url"]


def _mig_book(bad=False):
	acc = [f"Q{RUN}-1", f"Q{RUN}-2"]
	conns = [["Account No", "consumer_name", "cnic", "House No", "Tariff", "Meter No", "meter_type", "plot_area", "plot_unit"],
	         [acc[0], "QA Migrated One", "35202-7654321-1", "59-C", "RES", f"QM{RUN}-1", "", "5", "Marla"],
	         [acc[1], "QA Migrated Two", "", "7-N", "RES", f"QM{RUN}-2", "NM", "10", "Marla"]]
	if bad:
		conns.append([f"Q{RUN}-3", "", "", "x", "ZZZ", f"QM{RUN}-1", "", "", ""])  # no name, bad house, unknown category, a meter twice
	return acc, {
		"Connections": conns,
		"Readings": [["old_account_no", "last_reading", "last_export_reading", "reading_date"], [acc[0], "1677", "", "25/10/2026"],
		             [acc[1], "900", "410", "25/10/2026"]],
		"Balances": [["old_account_no", "current_bill", "arrears", "credit"], [acc[0], "5355", "1,200", ""], [acc[1], "", "", "750"]],
		"History": [["old_account_no", "month", "units", "bill_amount", "paid_on", "paid_amount"], [acc[0], "01/09/2026", "63", "3130", "10/10/2026", "2570"],
		            [acc[0], "01/10/2026", "70", "5355", "", ""]],
	}


def _list(desk, doctype, filters, fields=("name",)):
	return desk.s.get(f"{URL}/api/resource/{doctype}", params={"filters": json.dumps(filters), "fields": json.dumps(list(fields))}).json()["data"]


@case("QA-93", "Increment 29: a Migration Profile saved through the form keeps its house-number pattern; Fill SAP IS-U Columns; the template workbook")
def t_migration_profile(d: Desk):
	ao = USERS["ao"]
	soc = d.save_doc({"doctype": "Society", "society_name": f"QA Migration Society {RUN}", "short_name": f"qm{RUN}", "enable_electricity": 1})
	cat = d.save_doc({"doctype": "Consumer Category", "category_name": f"Residential MIG {RUN}", "utility": "Electricity"})["name"]
	p = ao.save_doc({"doctype": "Migration Profile", "profile_name": f"QA source {RUN}", "society": soc["name"], "source_system": "QA Billing",
	                 "date_format": "Day first (31-12-2026)", "default_utility": "Electricity",
	                 "house_pattern": "(?P<house>\\d+)-?(?P<block>[A-Z][A-Z0-9]*)",
	                 "columns": [{"sheet": "Connections", "source_column": "Account No", "target": "old_account_no"},
	                             {"sheet": "Connections", "source_column": "House No", "target": "house"},
	                             {"sheet": "Connections", "source_column": "Tariff", "target": "category"},
	                             {"sheet": "Connections", "source_column": "Meter No", "target": "meter_no"}],
	                 "value_maps": [{"target": "category", "source_value": "RES", "our_value": f"Residential MIG {RUN}"},
	                                {"target": "meter_type", "source_value": "NM", "our_value": "Net Metering"}]})
	eq(p["house_pattern"], "(?P<house>\\d+)-?(?P<block>[A-Z][A-Z0-9]*)", "the pattern survives the form's save")
	expect_error(lambda: ao.save_doc(dict(ao.get("Migration Profile", p["name"]), house_pattern="(\\d+)")), "named house")
	fill = "utility_billing.utility_billing.doctype.migration_profile.migration_profile.fill_sap_isu"
	eq(ao.call(fill, name=p["name"]) > 20, True, "SAP IS-U columns added")
	eq(ao.call(fill, name=p["name"]), 0, "added once only")
	r = ao.s.get(f"{URL}/api/method/{MIG}.template")
	eq((r.status_code, r.content[:2]), (200, b"PK"), "the template is an Excel workbook")
	S["mig_soc"], S["mig_profile"] = soc["name"], p["name"]


@case("QA-94", "Increment 29: a Migration Batch checked, loaded, undone, loaded again and approved by another person; old numbers at the counter")
def t_migration_batch(d: Desk):
	ao, gm, cashier = USERS["ao"], USERS["gm"], USERS["cashier"]
	s = S["mig_soc"]
	b = ao.save_doc({"doctype": "Migration Batch", "naming_series": "MIG-.YYYY.-.####", "society": s, "profile": S["mig_profile"],
	                 "as_of_date": "2026-10-31", "control_accounts": "2", "control_receivable": "5805"})
	acc, bad = _mig_book(bad=True)
	ao.save_doc(dict(ao.get("Migration Batch", b["name"]), workbook=_upload_xlsx(ao, bad, "Migration Batch", b["name"])))
	eq(ao.call(f"{MIG}.check", name=b["name"])["errors"] >= 3, True, "the bad workbook's errors are listed")
	eq((ao.call(f"{MIG}.load", name=b["name"])["loaded"], _list(d, "Connection", [["society", "=", s]])), (False, []),
	   "errors stop the load and nothing is created")
	acc, good = _mig_book()
	ao.save_doc(dict(ao.get("Migration Batch", b["name"]), workbook=_upload_xlsx(ao, good, "Migration Batch", b["name"])))
	res = ao.call(f"{MIG}.check", name=b["name"])
	b = ao.get("Migration Batch", b["name"])
	eq((res["errors"], b["account_count"], float(b["receivable_total"]), b["accounts_difference"], float(b["receivable_difference"])),
	   (0, 2, 5805.0, 0, 0.0), "reconciled with the control figures")
	res = ao.call(f"{MIG}.load", name=b["name"])
	eq((res["loaded"], res["accounts"]), (True, 2), "loaded")
	enq = cashier.call("utility_billing.utility_billing.account.enquiry", reference_no=acc[0])
	eq(enq["consumer_name"], "QA Migrated One", "the old account number works at the counter")
	c = d.get("Connection", _list(d, "Connection", [["legacy_account_no", "=", acc[1]]])[0]["name"])
	eq((float(c["meters"][0]["start_reading"]), float(c["meters"][0]["start_export_reading"])), (900.0, 410.0), "start readings from the last readings")
	ao.call(f"{MIG}.undo", name=b["name"])
	eq(_list(d, "Connection", [["society", "=", s]]), [], "undo removed the accounts")
	eq(ao.call(f"{MIG}.load", name=b["name"])["loaded"], True, "loaded again")
	b = ao.workflow(ao.get("Migration Batch", b["name"]), "Submit for Approval")
	expect_error(lambda: ao.workflow(b, "Approve"))  # the maker can't approve
	b = gm.workflow(b, "Approve")
	eq((b["docstatus"], b["workflow_state"], b["load_status"]), (1, "Approved", "Loaded"), "approved by the General Manager")
	expect_error(lambda: ao.call(f"{MIG}.undo", name=b["name"]), "draft or rejected")
	imp = d.get("Opening Balance Import", b["opening_import"])
	eq((imp["row_count"], imp["error_count"], float(imp["total_receivable"])), (2, 0, 5805.0), "the opening balances are ready for their approval")
	S["mig_acc"] = acc


@case("QA-95", "Increment 29: a Parallel Run Month records the old system's payments once and lists the accounts against our bill run")
def t_parallel_run(d: Desk):
	ao = USERS["ao"]
	s, acc = S["mig_soc"], S["mig_acc"]
	run = USERS["bo"].save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": s, "utility": "Electricity",
	                            "billing_month": "2026-11-01", "issue_date": "2026-11-05"})
	prm = ao.save_doc({"doctype": "Parallel Run Month", "society": s, "billing_month": "2026-11-01", "bill_run": run["name"], "tolerance": "1"})
	url = _upload_xlsx(ao, {"Payments": [["old_account_no", "paid_on", "amount", "reference"], [acc[0], "12/11/2026", "3,960", f"QTX{RUN}"],
	                                     ["NOPE-1", "12/11/2026", "10", f"QTY{RUN}"]],
	                        "Old Bills": [["old_account_no", "units", "current_bill", "arrears", "payable"], [acc[0], "70", "3400", "560", "3960"]]},
	                   "Parallel Run Month", prm["name"])
	ao.save_doc(dict(ao.get("Parallel Run Month", prm["name"]), workbook=url))
	eq(ao.call(f"{PRM}.load_payments", name=prm["name"]), {"recorded": 1, "skipped": 1}, "one payment recorded, the unknown account skipped")
	eq(ao.call(f"{PRM}.load_payments", name=prm["name"]), {"recorded": 1, "skipped": 1}, "loading again records nothing more")
	receipts = _list(d, "Payment Receipt", [["channel_txn_id", "=", f"QTX{RUN}"]], ("amount", "docstatus"))
	eq([(float(r["amount"]), r["docstatus"]) for r in receipts], [(3960.0, 1)], "the payment is posted once")
	res = ao.call(f"{PRM}.compare", name=prm["name"])
	prm = ao.get("Parallel Run Month", prm["name"])
	eq((res["compared"], prm["missing_here"], prm["comparison_status"]), (1, 1, "Compared"), "not yet billed here: listed as a difference")


@case("QA-96", "Increment 31: a DISCO's 14-digit numbers kept as reference numbers, missing house numbers filled in, accounts found by consumer ID")
def t_migration_keep_numbers(d: Desk):
	ao, cashier = USERS["ao"], USERS["cashier"]
	s = S["mig_soc"]
	p = ao.save_doc({"doctype": "Migration Profile", "profile_name": f"QA DISCO {RUN}", "society": s, "source_system": "LESCO",
	                 "date_format": "Day first (31-12-2026)", "default_utility": "Electricity", "keep_old_reference": 1,
	                 "house_pattern": "(?P<house>\\d+)-?(?P<block>[A-Z][A-Z0-9]*)", "missing_house": "Use the old account number",
	                 "default_block": "Gulberg",
	                 "columns": [{"sheet": "Connections", "source_column": "Account No", "target": "old_account_no"},
	                             {"sheet": "Connections", "source_column": "House No", "target": "house"},
	                             {"sheet": "Connections", "source_column": "Tariff", "target": "category"},
	                             {"sheet": "Connections", "source_column": "Meter No", "target": "meter_no"}],
	                 "value_maps": [{"target": "category", "source_value": "RES", "our_value": f"Residential MIG {RUN}"}]})
	eq((p["keep_old_reference"], p["missing_house"], p["default_block"]), (1, "Use the old account number", "Gulberg"), "the profile keeps its settings")
	refs = [f"0711{RUN}{n:06d}" for n in (1, 2, 3)]  # 14 digits, as on a LESCO bill
	cnic = f"35202-{RUN}111-1"  # a CNIC of this run only: the same CNIC from an earlier run would join that run's consumer
	book = {"Connections": [["Account No", "consumer_name", "cnic", "House No", "Tariff", "Meter No"],
	                        [refs[0], "QA Disco One", cnic, "", "RES", f"QD{RUN}-1"],
	                        [refs[1], "QA Disco One", cnic, "", "RES", f"QD{RUN}-2"],
	                        [refs[2], "QA Disco Two", "", "12-B", "RES", f"QD{RUN}-3"]],
	        "Readings": [["old_account_no", "last_reading", "reading_date"], [refs[0], "100", "25/10/2026"], [refs[1], "200", "25/10/2026"],
	                     [refs[2], "300", "25/10/2026"]]}
	b = ao.save_doc({"doctype": "Migration Batch", "naming_series": "MIG-.YYYY.-.####", "society": s, "profile": p["name"], "as_of_date": "2026-10-31"})
	ao.save_doc(dict(ao.get("Migration Batch", b["name"]), workbook=_upload_xlsx(ao, book, "Migration Batch", b["name"])))
	res = ao.call(f"{MIG}.load", name=b["name"])
	eq((res["loaded"], res["accounts"]), (True, 3), "loaded, with no house numbers for two accounts")
	c1, c3 = d.get("Connection", refs[0]), d.get("Connection", refs[2])
	eq((c1["reference_no"], c1["legacy_account_no"]), (refs[0], refs[0]), "the 14-digit number is the account's reference number")
	eq([(x["property_no"], x["block"]) for x in (d.get("Property", c1["property"]), d.get("Property", c3["property"]))],
	   [(f"Gulberg-{refs[0]}", "Gulberg"), ("B-12", "B")], "a missing house number: the old number and the default block; a real one kept")
	look = "utility_billing.utility_billing.cashdesk.lookup"
	eq(cashier.call(look, reference=refs[2])["connection"], refs[2], "the 14-digit number at the counter")
	eq(cashier.call(look, reference=c3["consumer"])["connection"], refs[2], "a consumer with one account, found by consumer ID")
	pick = cashier.call(look, reference=c1["consumer"])
	eq(sorted(r["reference"] for r in pick["choose"]), refs[:2], "a consumer with two accounts: the counter offers both")
	expect_error(lambda: cashier.call("utility_billing.utility_billing.account.enquiry", reference_no=c1["consumer"]), "has 2 accounts")


@case("QA-97", "Increment 32: feeders and transformers on sheets of their own; a plot size without a unit takes the profile's unit")
def t_migration_network_sheets(d: Desk):
	ao = USERS["ao"]
	s = S["mig_soc"]
	cat = d.call("frappe.client.get_value", doctype="Consumer Category", filters={"category_name": f"Residential MIG {RUN}"}, fieldname="name")["name"]
	p = ao.save_doc({"doctype": "Migration Profile", "profile_name": f"QA LESCO net {RUN}", "society": s, "source_system": "LESCO",
	                 "date_format": "Day first (31-12-2026)", "default_utility": "Electricity", "default_category": cat,
	                 "default_plot_unit": "Kanal", "missing_house": "Use the old account number", "default_block": "Model Town"})
	eq(p["default_plot_unit"], "Kanal", "the profile keeps its default plot unit")
	feeder, ref = int(f"9{RUN}01"), f"0712{RUN}000001"  # LESCO's feeder codes are numbers: Excel hands them back as numbers
	ref2 = f"0712{RUN}000002"  # its transformer and the transformer's meter are on the consumer's row, as LESCO gives them
	book = {"Connections": [["old_account_no", "consumer_name", "meter_no", "plot_area", "feeder", "transformer", "transformer_meter_no"],
	                        [ref, "QA LESCO Net", f"QN{RUN}-1", 1, feeder, f"T{RUN}-1", ""],
	                        [ref2, "QA LESCO Net Two", f"QN{RUN}-2", "", feeder, f"T{RUN}-2", f"QTM{RUN}-2"]],
	        "Feeders": [["code", "name"], [feeder, "QA feeder"]],
	        "Transformers": [["code", "feeder", "kva", "location", "meter_no", "multiplier", "last_reading"],
	                         [f"T{RUN}-1", feeder, 200, "Park corner", f"QTM{RUN}-1", "40", "52310"]]}
	b = ao.save_doc({"doctype": "Migration Batch", "naming_series": "MIG-.YYYY.-.####", "society": s, "profile": p["name"], "as_of_date": "2026-10-31"})
	ao.save_doc(dict(ao.get("Migration Batch", b["name"]), workbook=_upload_xlsx(ao, book, "Migration Batch", b["name"])))
	res = ao.call(f"{MIG}.load", name=b["name"])
	eq((res["loaded"], res["accounts"]), (True, 2), "loaded from the Feeders and Transformers sheets")
	c = d.get("Connection", d.call("frappe.client.get_value", doctype="Connection", filters={"legacy_account_no": ref}, fieldname="name")["name"])
	eq((c["feeder"], c["transformer"]), (f"{s}-{feeder}", f"{s}-T{RUN}-1"), "the account is on the new feeder and transformer")
	t = d.get("Transformer", c["transformer"])
	eq((t["feeder"], t["kva_rating"], t["location"], t["check_meter"]), (f"{s}-{feeder}", 200, "Park corner", f"QTM{RUN}-1"),
	   "the transformer hangs on its feeder, with its own meter")
	m = d.get("Meter", f"QTM{RUN}-1")
	eq((m["meter_type"], m["multiplier"], m["start_reading"]), ("Bulk / Check Meter", 40, 52310), "the transformer's meter: a check meter from its last reading")
	c2 = d.get("Connection", d.call("frappe.client.get_value", doctype="Connection", filters={"legacy_account_no": ref2}, fieldname="name")["name"])
	t2 = d.get("Transformer", c2["transformer"])
	eq((t2["transformer_code"], t2["feeder"], t2["check_meter"]), (f"T{RUN}-2", f"{s}-{feeder}", f"QTM{RUN}-2"),
	   "a transformer given on the consumer's row is made from it, with its meter")
	prop = d.get("Property", c["property"])
	eq((prop["area"], prop["area_unit"]), (1, "Kanal"), "a plot size without a unit takes the profile's unit")


@case("QA-98", "Increment 34: LESCO's own workbook: its column names, reference numbers held as numbers, houses in the address, NTN, deferred and total receivable")
def t_migration_lesco_layout(d: Desk):
	ao = USERS["ao"]
	s = S["mig_soc"]
	cat = d.call("frappe.client.get_value", doctype="Consumer Category", filters={"category_name": f"Residential MIG {RUN}"}, fieldname="name")["name"]
	p = ao.save_doc({"doctype": "Migration Profile", "profile_name": f"QA LESCO layout {RUN}", "society": s, "source_system": "LESCO",
	                 "date_format": "Day first (31-12-2026)", "default_utility": "Electricity", "default_category": cat, "keep_old_reference": 1,
	                 "old_number_digits": "14", "house_pattern": "(?P<house>\\d+)-(?P<block>[A-Z][A-Z0-9]*)",
	                 "missing_house": "Use the old account number", "default_block": "MTE", "default_plot_unit": "Marla"})
	eq(p["old_number_digits"], 14, "the profile keeps the number of digits")
	n1, n2 = int(f"71{RUN}0000001"), int(f"71{RUN}0000002")  # 13 digits: a number column dropped the leading 0
	r1, r2 = f"0{n1}", f"0{n2}"
	book = {"Connections": [["Ref_no", "Customer_id", "old_ref_no", "consumer_name", "ntn", "address", "house", "block", "plot_area", "meter_no"],
	                        [n1, int(f"41{RUN}0001"), "", "QA Lesco One", f"{RUN}567-8", f"{RUN}-C, Model Town Extension, Lahore", "", "", 5, f"QL{RUN}-1"],
	                        [n2, int(f"41{RUN}0002"), "", "QA Lesco Two", "", "Model Town Extension, Lahore", "", "", "", f"QL{RUN}-2"]],
	        "Readings": [["Ref_no", "meter_no", "last_import_reading", "last_export_reading", "reading_date"],
	                     [n1, "", 8452, "", "25/10/2026"], [n2, "", 100, "", "25/10/2026"]],
	        "Balances": [["Ref_no", "current_bill", "arrears", "surcharge", "Deferred", "credit_balance", "security_deposit",
	                      "Remaining_instalments_amount", "installments (nos)", "solar_credit_units (balance)", "Total Receivable"],
	                     [n1, 5355, 45, 0, 1000, 0, 3000, 0, 0, 0, 6400], [n2, 0, 0, 0, 0, 150, 0, 0, 0, 0, -150]],  # n2 in credit
	        "History": [["Ref_no", "month", "units", "current_amount", "arrears", "adjustment", "surcharge", "bill_amount", "paid_on", "paid_amount",
	                     "balance"], [n1, "01/10/2026", 190, 5355, 45, 0, 0, 5400, "", 0, 5400]]}
	b = ao.save_doc({"doctype": "Migration Batch", "naming_series": "MIG-.YYYY.-.####", "society": s, "profile": p["name"], "as_of_date": "2026-10-31"})
	ao.save_doc(dict(ao.get("Migration Batch", b["name"]), workbook=_upload_xlsx(ao, book, "Migration Batch", b["name"])))
	res = ao.call(f"{MIG}.load", name=b["name"])
	eq((res["loaded"], res["accounts"]), (True, 2), "LESCO's workbook loads with its own column names")
	c1, c2 = d.get("Connection", r1), d.get("Connection", r2)
	eq(c1["reference_no"], r1, "the 14-digit reference number, its leading 0 restored")
	eq((d.get("Property", c1["property"])["property_no"], d.get("Property", c2["property"])["property_no"]), (f"C-{RUN}", f"MTE-{r2}"),
	   "the house found in the address; none found: the old number and the default block")
	eq([d.get("Consumer", c["consumer"])["tax_status"] for c in (c1, c2)], ["Filer", "Non-filer"], "the NTN decides the filer status")
	batch = d.get("Migration Batch", b["name"])
	eq(batch["receivable_total"], 6250, "the receivable includes the deferred 1,000, as LESCO's total does (increment 35)")
	imp = d.get("Opening Balance Import", batch["opening_import"])
	eq((float(imp["total_receivable"]), float(imp["total_held"]), imp["error_count"]), (5250.0, 1000.0, 0),
	   "the deferred amount goes to the opening balances as held")
	S["lesco"] = {"import": imp["name"], "refs": (r1, r2)}


@case("QA-99", "Increment 35: an amount held by court order: posted at go-live, kept off the bill, released or written off")
def t_held_amounts(d: Desk):
	ao, gm, bm, bo = USERS["ao"], USERS["gm"], USERS["bm"], USERS["bo"]
	s, lesco = S["mig_soc"], S["lesco"]
	for code, name, kind in (("1201", "Receivables (migrated)", "Receivable"), ("1250", "Held receivables (court orders)", "Receivable"),
	                         ("3900", "Opening balance equity", "Equity"), ("6100", "Bad debts", "Expense"),
	                         ("2201", "Security deposits held (migrated)", "Liability")):  # LESCO's sample account has a deposit
		ao.save_doc({"doctype": "Ledger Account", "account_code": acct(code), "account_name": name, "account_type": kind})
	d.save_doc(dict(d.get("Society", s), receivables_account=acct("1201"), opening_balance_account=acct("3900"),
	                held_receivables_account=acct("1250"), write_off_account=acct("6100"), deposit_liability_account=acct("2201")))
	imp = ao.workflow(d.get("Opening Balance Import", lesco["import"]), "Submit for Approval")
	imp = gm.workflow(imp, "Approve")
	eq((imp["docstatus"], bool(imp["held_journal"])), (1, True), "the opening balances are posted, with a journal for the held amount")
	j = d.get("GL Journal", imp["held_journal"])
	eq(sorted((x["account"], float(x["debit"]), float(x["credit"])) for x in j["lines"]),
	   sorted([(acct("1250"), 1000.0, 0.0), (acct("3900"), 0.0, 1000.0)]), "held receivables against the opening balances")
	r1 = lesco["refs"][0]
	h = d.call("frappe.client.get_value", doctype="Held Amount", filters={"connection": r1}, fieldname=["name", "status", "amount"])
	eq((h["status"], float(h["amount"])), ("Held", 1000.0), "a held amount for the account")
	enq = USERS["cashier"].call("utility_billing.utility_billing.account.enquiry", reference_no=r1)
	eq(float(enq["balance"]), 5400.0, "what the account owes leaves the held 1,000 out")
	def decide(user, method, remarks):  # as the form's Release and Write Off buttons call it
		return user.s.post(f"{URL}/api/method/run_doc_method", data={"dt": "Held Amount", "dn": h["name"], "method": method,
		                                                            "args": json.dumps({"remarks": remarks})})

	eq(decide(bo, "release", "Court dismissed the petition").status_code in (403, 417), True, "a Billing Officer can't release it")
	eq(decide(bm, "write_off", "Court quashed the demand").status_code in (403, 417), True, "a Billing Manager can't write it off")
	r = decide(bm, "release", "Court dismissed the petition")
	eq(r.status_code, 200, f"the Billing Manager releases it ({r.text[:120]})")
	h2 = d.get("Held Amount", h["name"])
	e = d.get("Customer Ledger Entry", h2["ledger_entry"])
	eq((h2["status"], e["entry_type"], float(e["amount"]), float(e["arrears_balance"])), ("Released", "Hold released", 1000.0, 1045.0),
	   "released: the amount is in the arrears now")
	eq(float(USERS["cashier"].call("utility_billing.utility_billing.account.enquiry", reference_no=r1)["balance"]), 6400.0, "and is owed")
	eq(decide(bm, "release", "again please").status_code in (403, 417), True, "decided once only")


@case("QA-100", "Increment 36: a court hold after go-live: approved like a bill revision, a revised bill without it, counted in the total receivable")
def t_court_hold(d: Desk):
	from datetime import date

	ao, bo, bm, gm, cashier = USERS["ao"], USERS["bo"], USERS["bm"], USERS["gm"], USERS["cashier"]
	hold = {"doctype": "Court Hold", "amount": "2500", "court_order": f"WP {RUN}/2026 (LHC)", "court_order_date": "2026-11-05",
	        "remarks": "The court set the disputed amount aside"}
	expect_error(lambda: bo.save_doc(dict(hold, connection=S["conn"]["name"])), "installment plan")  # QA-71's account has a plan
	s, conn = S["nm_society"], S["nm_conn"]  # QA-91's solar account: its September bill is posted
	ao.save_doc({"doctype": "Ledger Account", "account_code": acct("1252"), "account_name": "Held receivables (solar society)", "account_type": "Receivable"})
	d.save_doc(dict(d.get("Society", s), held_receivables_account=acct("1252")))
	ref = d.get("Connection", conn)["reference_no"]
	enquiry = lambda: cashier.call("utility_billing.utility_billing.account.enquiry", reference_no=ref)  # noqa: E731
	before = enquiry()
	owed, old_bill = float(before["balance"]), before["bill"]
	hold["connection"] = conn
	expect_error(lambda: cashier.save_doc(dict(hold)))
	expect_error(lambda: bo.save_doc(dict(hold, amount=str(owed + 1000))), "no more than")
	saved = bo.save_doc(dict(hold))
	eq((saved["bill"], float(saved["owed_now"]), float(saved["payable_after"])), (old_bill, owed, round(owed - 2500, 2)), "the form shows what the hold does")
	saved = bo.workflow(saved, "Submit for Approval")
	expect_error(lambda: bo.workflow(saved, "Approve"))  # a Billing Officer doesn't approve
	done = bm.workflow(saved, "Approve")
	eq((done["docstatus"], bool(done["held_amount"]), bool(done["revised_bill"])), (1, True, True), "approved: held, and the bill revised")
	new, old = d.get("Utility Bill", done["revised_bill"]), d.get("Utility Bill", old_bill)
	eq((new["revises"], float(new["held_deducted"]), float(new["court_held"]), float(new["payable_within_due"]), new["status"], old["status"]),
	   (old_bill, 2500.0, 2500.0, round(owed - 2500, 2), "Posted", "Superseded"), "the revised bill")
	after = enquiry()
	eq((round(float(after["balance"]), 2), after["bill"]), (round(owed - 2500, 2), new["name"]), "payment channels ask for the revised bill")
	_reader, text = get_pdf(cashier, "download_bill", name=new["name"])
	eq(("Less: held by court order" in text, "Held by court order, not payable" in text), (True, True), "the revised bill prints the hold")
	total = rows_of(run_report(gm, "Arrears Ageing", society=s, as_of=date.today().isoformat()))[-1]
	eq((float(total["held"]), round(float(total["receivable"]), 2)), (2500.0, round(float(total["total"]) + 2500, 2)),
	   "the arrears ageing adds the held amount to the total receivable")
	# increment 39: the defaulters report shows it beside what is owed; run 40 days on, past the revised bill's new due date
	from datetime import timedelta

	later = (date.today() + timedelta(days=40)).isoformat()
	row = [r for r in rows_of(run_report(gm, "Defaulters", society=s, as_of=later)) if r.get("connection") == conn]
	eq((bool(row), float(row[0]["held"]) if row else None, round(float(row[0]["receivable"]) - float(row[0]["owed"]), 2) if row else None),
	   (True, 2500.0, 2500.0), "the defaulters report shows the held amount and the total receivable")
	# increment 40: the Defaulters and Recovery page too, beside what is owed and in a summary card
	page = d.call("utility_billing.utility_billing.recovery.get_recovery", society=s, as_of=later, show="all")
	eq((float(page["cards"]["held"]), [float(r["held"]) for r in page["rows"] if r["connection"] == conn]), (2500.0, [2500.0]),
	   "the Defaulters and Recovery page shows the held amount")
	revised = d.get("Utility Bill", done["revised_bill"])
	eq(revised["due_date"], old["due_date"], "the court hold kept the bill's due date (user, 2026-11-21)")
	held = d.get("Held Amount", done["held_amount"])
	eq((held["status"], float(held["amount"]), held["court_hold"]), ("Held", 2500.0, done["name"]), "the held amount, released or written off from its own form")
	# increment 41 (user, 2026-11-21: "add held amounts to the MIS reports too")
	reg = [r for r in rows_of(run_report(gm, "Held Amounts Register", society=s, as_of=date.today().isoformat())) if r.get("name") == held["name"]]
	eq([(r["connection"], float(r["amount"]), r["status"], float(r["still_held"]), r["court_order"]) for r in reg],
	   [(conn, 2500.0, "Held", 2500.0, hold["court_order"])], "the held amounts register lists it, still held")
	reg_bo = rows_of(run_report(bo, "Held Amounts Register", society=s, as_of=date.today().isoformat(), status="Released"))
	eq([r for r in reg_bo if r.get("name") == held["name"]], [], "filtered to released, it isn't listed")
	cr = [r for r in rows_of(run_report(gm, "Corrections Register", society=s, from_date=date.today().replace(day=1).isoformat(),
	                                    to_date=date.today().isoformat(), kind="Court hold")) if r.get("name") == done["name"]]
	eq([(r["kind"], float(r["amount"]), r["status"]) for r in cr], [("Court hold", -2500.0, "Approved")], "the corrections register lists the court hold")
	for name, field in (("Disconnection List", "held"), ("Security Deposits Held", "court_held")):
		cols = {c["fieldname"]: c["label"] for c in run_report(gm, name, society=s)["columns"] if isinstance(c, dict)}
		eq(cols.get(field), "Held by Court Order", f"{name} has a Held by Court Order column")


@case("QA-101", "Increment 42: users land straight in the app, on their role's screen, and see the product's name, not the platform's")
def t_landing_and_identity(d: Desk):
	import re as _re

	cashier = USERS["cashier"]
	r = cashier.s.get(f"{URL}/login", allow_redirects=False)  # signed in: the login page sends the user on, as after signing in
	eq((r.status_code in (301, 302, 303, 307), r.headers.get("Location", "").split("?")[0].replace(URL, "")), (True, "/desk/ub-dashboard"),
	   "after login the app opens (the dashboard sends a cashier to the cash counter)")
	html = cashier.s.get(f"{URL}/desk").text
	eq(bool(_re.search(r'"home_page":\s*"ub-cash"', html)), True, "/desk opens the cashier's own screen, not the apps screen")
	eq(("<title>Utility Management System</title>" in html, "utility-billing-logo.svg" in html, "frappe-favicon" in html),
	   (True, True, False), "the product's name and icon, not the platform's")
	# increment 44: the cashier's sidebar lists the counter's screens, not every list the role may read
	# read only the app's own sidebar from the boot data: the page also carries the platform's other sidebars
	boot = json.JSONDecoder().raw_decode(html, html.index("frappe.boot = ") + len("frappe.boot = "))[0]
	app_links = {i.get("link_to") for i in boot["module_sidebars"]["Utility Management System"]["items"]}
	in_menu = lambda target: target in app_links  # noqa: E731
	eq((in_menu("ub-cash"), in_menu("Payment Receipt"), in_menu("Bill Run"), in_menu("User"), in_menu("ub-dashboard")),
	   (True, True, False, False, False), "the cashier's sidebar: Cash Counter and receipts, not Bill Run, Users or the dashboard")
	# opening the desk gives the session a CSRF token, which the browser then sends with every call: so do we
	token = _re.search(r'csrf_token\s*=\s*"([^"]+)"', html)
	if token:
		cashier.s.headers["X-Frappe-CSRF-Token"] = token.group(1)
	apps = [a["name"] for a in cashier.call("frappe.apps.get_apps")]
	eq(apps, ["utility_billing"], "the apps a cashier is offered")
	for page in (f"{URL}/login", f"{URL}/no-such-page"):
		text = requests.get(page, timeout=30).text
		eq(("frappe-favicon" in text, "utility-billing-logo.svg" in text), (False, True), f"{page.replace(URL, '')} carries the product's icon")


@case("QA-102", "Increment 45: a dispute taken to court closes when its court hold is approved; Held Amount is reached from the hold, not the menu")
def t_dispute_to_court(d: Desk):
	bo, bm, gm, cashier = USERS["bo"], USERS["bm"], USERS["gm"], USERS["cashier"]
	conn = S["nm_conn"]  # QA-100's account: its bill was revised by the first court hold
	ref = d.get("Connection", conn)["reference_no"]
	info = cashier.call("utility_billing.utility_billing.account.enquiry", reference_no=ref)
	amount = min(500, int(float(info["balance"])))
	dispute = bo.save_doc({"doctype": "Bill Dispute", "connection": conn, "bill": info["bill"], "disputed_amount": str(amount),
	                       "reason": "The consumer denies the detection charges"})
	hold = {"doctype": "Court Hold", "connection": conn, "amount": str(amount), "court_order": f"CS {RUN}/2026", "court_order_date": "2026-11-12",
	        "remarks": f"Dispute {dispute['name']}: the consumer went to court", "bill_dispute": dispute["name"]}
	expect_error(lambda: bo.save_doc(dict(hold, connection=S["conn"]["name"])), "isn't a dispute of account")
	saved = bo.workflow(bo.save_doc(dict(hold)), "Submit for Approval")
	eq(d.get("Bill Dispute", dispute["name"])["status"], "Open", "the dispute stays open while the hold waits for approval")
	done = bm.workflow(saved, "Approve")
	after = d.get("Bill Dispute", dispute["name"])
	eq((after["status"], after.get("court_hold"), after.get("resolved_by")), ("Referred to court", done["name"], EMAIL["bm"]), "the dispute closed with the hold")
	eq(done["name"] in after["resolution"] and hold["court_order"] in after["resolution"], True, "the dispute says where it went")
	expect_error(lambda: doc_method(bm, after, "resolve", {"outcome": "Withdrawn", "remarks": "decided twice"}), "already decided")
	expect_error(lambda: bo.save_doc(dict(hold, court_order=f"CS {RUN}/2026 B")), "already decided")
	# the menu: Bill Dispute and Court Hold, not Held Amount, which is still reached from the hold
	html = gm.s.get(f"{URL}/desk").text
	boot = json.JSONDecoder().raw_decode(html, html.index("frappe.boot = ") + len("frappe.boot = "))[0]
	links = {i.get("link_to") for i in boot["module_sidebars"]["Utility Management System"]["items"]}
	eq(("Bill Dispute" in links, "Court Hold" in links, "Held Amount" in links, "Held Amounts Register" in links), (True, True, False, True), "the Corrections menu")
	eq(gm.get("Held Amount", done["held_amount"])["status"], "Held", "the Held Amount still opens from the court hold")
	# left open on purpose: the browser test BR-15 opens it and presses Taken to Court
	left = int(float(cashier.call("utility_billing.utility_billing.account.enquiry", reference_no=ref)["balance"]))
	if left >= 1:
		bo.save_doc({"doctype": "Bill Dispute", "connection": conn, "bill": latest_bill(conn), "disputed_amount": "1", "reason": "Left open for the browser test"})


@case("QA-103", "Increment 47: Reading Entry saves typed readings; a smart meter takes the MDM's file, not typing, unless it isn't communicating")
def t_reading_entry(d: Desk):
	bo, reader, cashier = USERS["bo"], USERS["reader"], USERS["cashier"]
	entry = "utility_billing.utility_billing.reading_entry"
	rd = bo.s.get(f"{URL}/api/resource/Meter Reading", params={
		"filters": json.dumps([["connection", "=", S["nm_conn"]]]), "fields": json.dumps(["name", "reading_cycle", "meter", "previous_reading"]),
		"order_by": "creation desc", "limit_page_length": 1}).json()["data"][0]  # QA-92's October reading of the solar account, its cycle still open
	cycle, prev = rd["reading_cycle"], int(float(rd["previous_reading"]))
	sheet = bo.call(f"{entry}.get_sheet", cycle=cycle)
	eq((sheet["status"], sheet["can_write"], [r["name"] for r in sheet["rows"]], sheet["totals"]["records"]), ("Open", True, [rd["name"]], 1), "the cycle's sheet")

	def typed(desk, **kw):
		return desk.call(f"{entry}.save_rows", cycle=cycle, rows=json.dumps([{"name": rd["name"], "exception_code": "", **kw}]))

	# a cashier may read readings (as before) but can't type them: the sheet is read-only for them and a save is refused
	eq(cashier.call(f"{entry}.get_sheet", cycle=cycle)["can_write"], False, "a cashier sees the sheet read-only")
	no = typed(cashier, present_reading=str(prev + 1))
	eq((no["saved"], no["rows"][0]["ok"]), (0, False), "a cashier's typing isn't saved")

	def mdm_file(reading, day):
		text = f"Meter No|Reading|Export Reading|Reading Date\n{rd['meter']}|{reading}|21500|{day}\n"
		up = bo.s.post(f"{URL}/api/method/upload_file", files={"file": (f"mdm-{RUN}-{random.randint(100, 999)}.txt", text.encode())},
		               data={"is_private": 1, "doctype": "Reading Cycle", "docname": cycle})
		return bo.call(f"{RDG}.upload_readings", cycle=cycle, file_url=up.json()["message"]["file_url"], source="MDM")

	out = typed(reader, present_reading=str(prev + 290), present_export_reading="21500", reading_date="2026-10-08")
	row = out["rows"][0]
	eq((out["saved"], row["ok"], float(row["units"]), row["source"], out["totals"]["entered"]), (1, True, 290.0, "Office", 1), "a meter reader types a reading on the screen")
	eq(mdm_file(prev + 283, "2026-10-08")["updated"], 0, "the MDM's file isn't taken for an ordinary meter")
	# the meter becomes a smart meter: its reading is locked on the screen at once
	d.save_doc(dict(d.get("Meter", rd["meter"]), smart_meter=1))
	eq(bo.call(f"{entry}.get_sheet", cycle=cycle)["rows"][0]["smart_meter"], 1, "the sheet shows it as a smart meter")
	refused = typed(bo, present_reading=str(prev + 300))["rows"][0]
	eq((refused["ok"], "is a smart meter" in refused.get("error", "")), (False, True), "typing a smart meter's reading is refused")
	res = mdm_file(prev + 283, "2026-10-08")
	after = d.get("Meter Reading", rd["name"])
	eq((res["updated"], float(after["units"]), after["source"]), (1, 283.0, "MDM"), "the MDM's text file gives the smart meter its reading, by meter number")
	# not communicating: the meter reader's reading is taken, marked, and goes to review; the MDM's later reading doesn't replace it
	hand = typed(reader, present_reading=str(prev + 285), present_export_reading="21500", reading_date="2026-10-09", smart_meter_mute=1)["rows"][0]
	eq((hand["ok"], float(hand["units"]), hand["status"], hand["source"], "read by hand" in (hand.get("checks") or "")), (True, 285.0, "Flagged", "Office", True),
	   "a mute smart meter is read by hand and sent to review")
	late = mdm_file(prev + 284, "2026-10-10")
	kept = d.get("Meter Reading", rd["name"])
	eq((late["updated"], "hand reading is kept" in late["errors"][0]["error"], float(kept["units"]), "The MDM then sent" in kept["checks"]), (0, True, 285.0, True),
	   "the hand reading is kept when the MDM reports later")


@case("QA-104", "Increment 53: the bill run wizard decides many exceptions at once (accept, hold, undo), as its buttons send them")
def t_wizard_bulk(d: Desk):
	bo, wiz = USERS["bo"], "utility_billing.utility_billing.billrun_wizard"
	run = bo.save_doc({"doctype": "Bill Run", "naming_series": "BRUN-.YYYY.-.####", "society": S["society"], "utility": "Maintenance",
	                   "billing_month": "2027-02-01", "issue_date": "2027-03-01"})
	keys = json.dumps([f"problem:{S['maint_conn']}", f"problem:{S['maint_conn']}"])
	eq(bo.call(f"{wiz}.decide_many", name=run["name"], keys=keys, decision="Accepted")["changed"], 0, "an account with no bill isn't accepted")
	out = bo.call(f"{wiz}.decide_many", name=run["name"], keys=keys, decision="Held")
	run = bo.get("Bill Run", run["name"])
	eq((out["changed"], [x["connection"] for x in run["exclusions"]], run["calculation_status"]), (2, [S["maint_conn"]], "Not calculated"),
	   "held out of the run once, and the run must be calculated again")
	eq(bo.call(f"{wiz}.decide_many", name=run["name"], keys=keys, decision="")["changed"], 2, "decisions undone")
	expect_error(lambda: USERS["cashier"].call(f"{wiz}.decide_many", name=run["name"], keys=keys, decision="Held"))
	d.s.post(f"{URL}/api/method/frappe.client.delete", data={"doctype": "Bill Run", "name": run["name"]}).raise_for_status()


def _notice_pdf(desk, run):
	from pypdf import PdfReader

	r = desk.s.get(f"{URL}/api/method/utility_billing.utility_billing.recovery.download_notices", params={"run": run})
	if r.status_code != 200 or not r.content.startswith(b"%PDF"):
		raise ServerError(r.status_code, r.text[:300])
	reader = PdfReader(io.BytesIO(r.content))
	return reader, " ".join(" ".join(p.extract_text() for p in reader.pages).split())


# ---------------------------------------------------------------- runner
def main():
	global RUN
	d = Desk()
	d.login()
	# a run number no earlier run used (2026-11-21: a random repeat, 5060, found its records already there and 87 cases failed)
	while d.call("frappe.client.get_count", doctype="Society", filters=json.dumps([["name", "like", f"%{RUN}%"]])):
		RUN = f"{random.randint(1000, 9999)}"
	make_user(d, "bm", ["Billing Manager"])
	make_user(d, "gm", ["General Manager"])
	make_user(d, "bmgm", ["Billing Manager", "General Manager"])
	make_user(d, "bo", ["Billing Officer"])
	make_user(d, "cashier", ["Cashier"])
	make_user(d, "reader", ["Meter Reader"])
	make_user(d, "ao", ["Accounts Officer"])
	make_user(d, "mgmt", ["Management"])
	make_user(d, "auditor", ["Billing Auditor"])
	make_user(d, "rec", ["Recovery Officer"])
	make_user(d, "iso", ["Billing Manager"])
	results = []
	for qa_id, req, fn in CASES:
		try:
			fn(d)
			results.append((qa_id, req, "PASS", ""))
		except Exception as e:
			detail = str(e) if isinstance(e, (AssertionError, ServerError)) else traceback.format_exc(limit=2)
			results.append((qa_id, req, "FAIL", detail.strip().replace("\n", " ")[:400]))
	width = max(len(r[1]) for r in results)
	for qa_id, req, status, detail in results:
		print(f"{status}  {qa_id}  {req.ljust(width)}  {detail}")
	failed = [r for r in results if r[2] == "FAIL"]
	print(f"\nsite={SITE} run={RUN}  {len(results) - len(failed)} passed, {len(failed)} failed")
	(Path(__file__).parent / "last_run.json").write_text(json.dumps(
		{"site": SITE, "run": RUN, "results": [dict(zip(("id", "requirement", "status", "detail"), r)) for r in results]},
		indent=1))
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	main()
