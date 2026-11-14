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
	if not rows_of(meters) or len(meters["report_summary"]) != 4:
		raise AssertionError("meter register is empty or has no status counts")
	census = rows_of(run_report(bm, "Consumer Census"))
	if census[-1]["total"] < 1:
		raise AssertionError(f"census: {census}")
	kpis = d.s.get(f"{URL}/api/resource/KPI Definition", params={"limit_page_length": 100, "fields": json.dumps(["name", "formula"])}).json()["data"]
	if len(kpis) < 15 or not all(k["formula"] for k in kpis):
		raise AssertionError(f"KPI dictionary has {len(kpis)} entries")


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
	make_user(d, "ao", ["Accounts Officer"])
	make_user(d, "mgmt", ["Management"])
	make_user(d, "auditor", ["Billing Auditor"])
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
