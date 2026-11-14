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

import json
import os
import random
import sys
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
			raise ServerError(r.status_code, _message(data) or r.text[:300])
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
			raise ServerError(r.status_code, r.text[:300])
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
			raise ServerError(r.status_code, _message(data))
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
	raise AssertionError("expected the server to refuse this, but it was accepted")


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
		"reading_window_days": "5", "days_to_due_date": "14", "grace_days": "0", "minimum_bill": "0",
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
	other = d.save_doc({"doctype": "Society", "society_name": "Other", "short_name": f"qo{RUN}", "enable_electricity": 1,
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

	for _ in range(20):  # notifications are created by a background job
		notes = USERS["bm"].s.get(f"{URL}/api/resource/Notification Log", params={
			"filters": json.dumps([["document_name", "=", t["name"]]]), "fields": json.dumps(["subject"])}).json()["data"]
		if any("rejected" in n["subject"].lower() for n in notes):
			break
		time.sleep(1)
	else:
		raise AssertionError(f"maker not notified of rejection within 20 s: {notes}")


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


@case("QA-36", "FS-BIL-10: a posted bill can't be changed or cancelled on its own")
def t_bill_locked(d: Desk):
	bill = d.get("Utility Bill", S["bill"])
	expect_error(lambda: d.save_doc(dict(bill, current_amount="1"), action="Update"))
	r = d.s.post(f"{URL}/api/method/frappe.client.cancel", data={"doctype": "Utility Bill", "name": S["bill"]})
	if r.status_code == 200:
		raise AssertionError("a posted bill was cancelled directly")
	eq(USERS["cashier"].get("Utility Bill", S["bill"])["status"], "Posted", "cashier can read the posted bill")


# ---------------------------------------------------------------- runner
def main():
	d = Desk()
	d.login()
	make_user(d, "bm", ["Billing Manager"])
	make_user(d, "gm", ["General Manager"])
	make_user(d, "bmgm", ["Billing Manager", "General Manager"])
	make_user(d, "bo", ["Billing Officer"])
	make_user(d, "cashier", ["Cashier"])
	make_user(d, "reader", ["Meter Reader"])
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
