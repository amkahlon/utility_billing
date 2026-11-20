# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Consumer 360 (FS-MST-13, 15): one view of a consumer with every connection, meter, flag, deposit, bill, payment, ledger entry and
reading, and a combined statement across the consumer's accounts. Data for the page ub-consumer-360."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from utility_billing.utility_billing import access, flags
from utility_billing.utility_billing import account as acct  # (view() has a parameter "account" and a variable "ledger")
from utility_billing.utility_billing import rights

STAFF = {"System Manager", "Society Admin", "Billing Manager", "General Manager", "Billing Officer", "Cashier", "Recovery Officer",
         "Accounts Officer", "Management", "Billing Auditor"}


def _require():
	if not rights.can("consumer360.view"):
		raise frappe.PermissionError(_("You can't open the consumer view."))


@frappe.whitelist()
def search(text: str, limit: int = 20):
	"""Consumers matching a name, CNIC, phone, house number, reference number or meter number (FS-MST-12), one row per account, with
	its house, utility, status and balance, and what matched, so the right account opens straight away (Consumer 360 redesign)."""
	_require()
	text = (text or "").strip()
	if len(text) < 2:
		return []
	like = f"%{text}%"
	mine = access.societies_of()  # a user limited to some societies finds only their accounts (review 2026-11-16)
	rows = frappe.db.sql(
		"""select c.name, c.consumer_name, c.mobile_no, c.id_number, k.name account, k.reference_no, k.current_meter, k.utility,
		          k.status account_status, p.property_no house,
		          case when k.reference_no like %(l)s then 'reference' when k.current_meter like %(l)s then 'meter'
		               when k.legacy_account_no like %(l)s or k.legacy_collection_no like %(l)s or c.legacy_customer_id like %(l)s then 'old number'
		               when p.property_no like %(l)s then 'house' when c.id_number like %(l)s then 'cnic'
		               when c.mobile_no like %(l)s or c.phone like %(l)s then 'phone' else 'name' end matched
		from `tabConsumer` c left join `tabConnection` k on k.consumer = c.name left join `tabProperty` p on p.name = k.property
		where (c.consumer_name like %(l)s or c.id_number like %(l)s or c.mobile_no like %(l)s or c.phone like %(l)s
		   or k.reference_no like %(l)s or k.current_meter like %(l)s or p.property_no like %(l)s
		   or k.legacy_account_no like %(l)s or k.legacy_collection_no like %(l)s or c.legacy_customer_id like %(l)s)
		   and (%(all)s or k.society in %(mine)s)
		order by (k.reference_no = %(t)s or k.current_meter = %(t)s or p.property_no = %(t)s or k.legacy_account_no = %(t)s) desc, c.consumer_name, k.name
		limit %(n)s""", {"l": like, "t": text, "n": int(limit), "all": mine is None, "mine": mine or [""]}, as_dict=True)
	for r in rows:
		r["balance"] = acct.balance(r.account) if r.account else None
	return access.mask_rows(rows)


def _connections(consumer):
	return frappe.get_all("Connection", filters={"consumer": consumer},
	                      fields=["name", "reference_no", "society", "property", "utility", "consumer_category", "status", "connection_date",
	                              "sanctioned_load_kw", "last_mdi_kw", "phase", "current_meter", "transformer", "feeder", "route", "security_deposit"],
	                      order_by="society, name")


@frappe.whitelist()
def consumer_of(account: str):
	"""The consumer an account belongs to, for opening the page from a link (/app/ub-consumer-360?account=...)."""
	_require()
	ref = (account or "").strip()
	conn = account_mod_connection(ref)
	if not conn:
		frappe.throw(_("No account with number {0}.").format(ref))
	return {"consumer": frappe.db.get_value("Connection", conn, "consumer"), "account": conn}


def account_mod_connection(ref):
	return acct.connection_of(ref) or (ref if frappe.db.exists("Connection", ref) else None)


def twelve_months(rows) -> list[dict]:
	"""The last twelve months up to the latest one read, each with its units (None when there was no reading), as the chart shows them."""
	by = {getdate(r.billing_month).replace(day=1): r for r in rows}
	last = max(by) if by else getdate(nowdate()).replace(day=1)
	out = []
	for i in range(11, -1, -1):
		m = frappe.utils.add_months(last, -i)
		r = by.get(m)
		out.append({"month": m.strftime("%b"), "full": m.strftime("%B %Y"), "units": flt(r.units) if r else None, "estimated": cint(r.estimated) if r else 0})
	return out


def overview(conn_name) -> dict:
	"""One account as the mock-up's Overview shows it (Consumer 360 redesign, 2026-11-16): what is payable and by when, the latest
	bill line by line, the connection and meter, twelve months of units, what happened lately, and what needs attention."""
	k = frappe.get_doc("Connection", conn_name)
	today = getdate(nowdate())
	enq = acct.enquiry_of(conn_name)
	bill = frappe.get_doc("Utility Bill", enq["bill"]) if enq.get("bill") else None
	reading = frappe.db.get_value("Meter Reading", bill.meter_reading, ["previous_reading", "present_reading", "reading_date", "photo"],
	                              as_dict=True) if bill and bill.meter_reading else None
	last_pay = frappe.db.get_value("Payment Receipt", {"connection": conn_name, "docstatus": 1},
	                               ["name", "payment_date", "amount", "mode", "payment_channel"], order_by="payment_date desc, creation desc", as_dict=True)
	meter = frappe.db.get_value("Meter", k.current_meter, ["serial_no", "status", "multiplier", "meter_type"], as_dict=True) if k.current_meter else None
	consumer = frappe.db.get_value("Consumer", k.consumer, ["notify_app", "notify_sms", "notify_email", "notify_whatsapp", "preferred_channel"],
	                               as_dict=True) or {}
	prop = frappe.db.get_value("Property", k.property, ["property_no", "block", "sector", "plot_size_class"], as_dict=True) or {}
	months = frappe.db.sql("""select billing_month, sum(units) units, max(status = 'Estimated') estimated from `tabMeter Reading`
	                          where connection = %s and status in ('Valid', 'Approved', 'Estimated') and billing_month > %s
	                          group by billing_month order by billing_month""", (conn_name, frappe.utils.add_months(today, -12)), as_dict=True)
	read = {getdate(m.billing_month) for m in months}
	# months billed by the previous system before migration (increment 29) fill the chart where there's no reading here
	months += [frappe._dict(billing_month=lb.billing_month, units=lb.units, estimated=0) for lb in frappe.get_all(
		"Legacy Bill", filters={"connection": conn_name, "billing_month": [">", frappe.utils.add_months(today, -12)]},
		fields=["billing_month", "units"]) if getdate(lb.billing_month) not in read]
	months.sort(key=lambda m: getdate(m.billing_month))
	held = flt(frappe.db.get_value("Connection", conn_name, "security_deposit"))
	alerts = [{"tone": "amber", "text": _("Flag: {0}").format(f.name)} for f in flags.active(conn_name, today)]
	for dt, label, cond in (("Bill Dispute", _("Bill dispute"), {"status": ["not in", ["Closed", "Resolved", "Decided", "Cancelled"]]}),
	                        ("Installment Plan", _("Installment plan"), {"plan_status": "Active", "docstatus": 1})):
		for n in frappe.get_all(dt, filters={"connection": conn_name, **cond}, pluck="name", limit=3):
			alerts.append({"tone": "blue", "text": f"{label} {n}", "go": [dt, n]})
	court_held = frappe.get_all("Held Amount", filters={"connection": conn_name, "status": "Held"}, fields=["name", "amount", "court_order"])
	for h in court_held:  # owed, but set aside by a court order: not on the bills (increment 35)
		alerts.append({"tone": "amber", "go": ["Held Amount", h.name],
		               "text": _("Rs {0} held by court order{1}: not billed").format(f"{flt(h.amount):,.0f}", f" ({h.court_order})" if h.court_order else "")})
	case = frappe.db.get_value("Recovery Case", {"connection": conn_name, "status": ["!=", "Closed"]}, ["name", "last_stage", "assigned_to"], as_dict=True)
	if case:
		alerts.append({"tone": "red", "text": _("Recovery: {0}").format(case.last_stage or _("case open")) + (f" · {case.assigned_to}" if case.assigned_to else ""),
		               "go": ["Recovery Case", case.name]})
	if flt(k.last_mdi_kw) > flt(k.sanctioned_load_kw) > 0:
		alerts.append({"tone": "amber", "text": _("Maximum demand {0} kW is above the sanctioned {1} kW").format(flt(k.last_mdi_kw), flt(k.sanctioned_load_kw))})
	if meter and meter.status == "Defective":
		alerts.append({"tone": "red", "text": _("Meter {0} is marked defective").format(meter.serial_no)})
	activity = []
	for b in frappe.get_all("Utility Bill", filters={"connection": conn_name, "docstatus": 1}, fields=["name", "billing_month", "issue_date", "payable_within_due",
	                                                                                                  "delivery_status"], order_by="issue_date desc", limit=4):
		activity.append({"date": b.issue_date, "kind": "bill", "go": ["Utility Bill", b.name],
		                 "text": _("{0} bill issued: Rs {1}").format(getdate(b.billing_month).strftime("%B"), f"{flt(b.payable_within_due):,.0f}"),
		                 "note": b.delivery_status or ""})
	for r in frappe.get_all("Meter Reading", filters={"connection": conn_name, "reading_date": ["is", "set"]},
	                        fields=["name", "reading_date", "present_reading", "status", "photo", "route"], order_by="reading_date desc", limit=3):
		activity.append({"date": r.reading_date, "kind": "reading", "go": ["Meter Reading", r.name],
		                 "text": _("Reading {0} taken").format(f"{flt(r.present_reading):g}") + (_(", photo") if r.photo else ""),
		                 "note": " · ".join(x for x in (_(r.status), _("route {0}").format(r.route) if r.route else "") if x)})
	for p in frappe.get_all("Payment Receipt", filters={"connection": conn_name, "docstatus": 1}, fields=["name", "payment_date", "amount", "mode"],
	                        order_by="payment_date desc", limit=3):
		activity.append({"date": p.payment_date, "kind": "payment", "go": ["Payment Receipt", p.name],
		                 "text": _("Payment Rs {0} by {1}").format(f"{flt(p.amount):,.0f}", p.mode), "note": p.name})
	for s in frappe.get_all("Service Request", filters={"connection": conn_name}, fields=["name", "request_title", "status", "requested_on"],
	                        order_by="requested_on desc", limit=3):
		activity.append({"date": s.requested_on, "kind": "request", "go": ["Service Request", s.name], "text": s.request_title or s.name,
		                 "note": _(s.status)})
	messages = frappe.get_all("Message Log", filters={"connection": conn_name}, fields=["name", "creation", "event", "channel", "status", "subject", "body"],
	                          order_by="creation desc", limit=20)
	activity.sort(key=lambda a: getdate(a["date"]), reverse=True)
	return {
		"name": k.name, "reference_no": k.reference_no, "utility": k.utility, "status": k.status, "category": k.consumer_category,
		"category_name": frappe.db.get_value("Consumer Category", k.consumer_category, "category_name"), "society": k.society,
		"property_no": prop.get("property_no"), "block": prop.get("block"), "sector": prop.get("sector"), "plot": prop.get("plot_size_class"),
		"load": flt(k.sanctioned_load_kw), "phase": k.phase,
		"payable": {"amount_due": flt(enq["amount_due"]), "within_due": flt(enq["payable_within_due"]), "after_due": flt(enq["payable_after_due"]),
		            "due_date": enq.get("due_date"), "current": flt(bill.current_amount) if bill else 0, "arrears": flt(bill.arrears) if bill else 0,
		            "balance": flt(enq["balance"]), "former": flt(enq.get("former_balance")), "deposit": held,
		            "court_held": round(sum(flt(h.amount) for h in court_held), 2),
		            "last_payment": last_pay, "bill_month": getdate(bill.billing_month).strftime("%B %Y") if bill else None},
		"bill": {"name": bill.name, "month": getdate(bill.billing_month).strftime("%B %Y"), "units": flt(bill.units), "consumption_type": bill.consumption_type,
		         "previous": flt(reading.previous_reading) if reading else None, "present": flt(reading.present_reading) if reading else None,
		         "lines": [{"label": ln.label, "amount": flt(ln.amount), "note": ln.note, "rate": flt(ln.rate), "quantity": flt(ln.quantity)}
		                   for ln in sorted(bill.lines, key=lambda x: (x.line_order or 0, x.idx))],
		         "current": flt(bill.current_amount), "arrears": flt(bill.arrears), "within_due": flt(bill.payable_within_due),
		         "surcharge": flt(bill.surcharge_amount), "after_due": flt(bill.payable_after_due), "tax": flt(bill.tax_amount),
		         "installment": flt(bill.installment_due), "due_date": str(bill.extended_due_date or bill.due_date), "final": cint(bill.final_bill),
		         "note": bill.period_note,
		         # net metering (increment 28): what was exported and what became of it
		         "solar": {"method": bill.nm_method, "imported": flt(bill.import_units), "exported": flt(bill.export_units), "credited": flt(bill.credited_units),
		                   "factor": flt(bill.nm_credit_factor), "carried_in": flt(bill.units_carried_in), "set_off": flt(bill.units_set_off),
		                   "carried_out": flt(bill.units_carried_out), "buyback_credit": flt(bill.buyback_credit)} if bill.get("nm_method") else None} if bill else None,
		"meter": {"serial_no": meter.serial_no, "status": meter.status, "multiplier": flt(meter.multiplier) or 1, "type": meter.meter_type,
		          "installed": k.current_meter} if meter else None,
		"network": {"feeder": frappe.db.get_value("Feeder", k.feeder, "feeder_name") if k.feeder else None,
		            "transformer": k.transformer, "kva": frappe.db.get_value("Transformer", k.transformer, "kva_rating") if k.transformer else None,
		            "route": k.route, "mdi": flt(k.last_mdi_kw)},
		"notifications": [ch for ch, on in (("App", consumer.get("notify_app")), ("SMS", consumer.get("notify_sms")), ("Email", consumer.get("notify_email")),
		                                    ("WhatsApp", consumer.get("notify_whatsapp"))) if cint(on)],
		"consumption": twelve_months(months),
		"alerts": alerts, "activity": activity[:8], "messages": messages,
	}


@frappe.whitelist()
def view(consumer: str, account: str | None = None):
	_require()
	c = frappe.get_doc("Consumer", consumer)
	conns = _connections(consumer)
	mine = access.societies_of()
	if mine is not None:
		conns = [k for k in conns if k.society in mine]
		if not conns:
			raise frappe.PermissionError(_("This consumer has no account in your societies."))
	names = [k.name for k in conns] or [""]
	today = getdate(nowdate())
	accounts = []
	for k in conns:
		bal = {b: flt(v) for b, v in acct.balances(k.name).items()}
		prop = frappe.db.get_value("Property", k.property, ["property_no", "block", "sector"], as_dict=True) or {}
		accounts.append({**k, "property_no": prop.get("property_no"), "block": prop.get("block"), "balances": bal,
		                 "owes": flt(acct.balance(k.name)), "flags": [f.name for f in flags.active(k.name, today)]})
	bills = frappe.get_all("Utility Bill", filters={"connection": ["in", names], "docstatus": 1},
	                       fields=["name", "connection", "billing_month", "issue_date", "due_date", "units", "current_amount", "payable_within_due",
	                               "status", "surcharge_status", "delivery_status", "nm_method", "import_units", "export_units", "units_carried_out"],
	                       order_by="issue_date desc", limit=24)
	payments = frappe.get_all("Payment Receipt", filters={"connection": ["in", names], "docstatus": 1},
	                          fields=["name", "connection", "payment_date", "amount", "mode", "payment_channel"], order_by="payment_date desc", limit=24)
	ledger = frappe.get_all("Customer Ledger Entry", filters={"connection": ["in", names]},
	                        fields=["name", "connection", "posting_date", "entry_type", "voucher_type", "voucher_no", "amount", "balance"],
	                        order_by="posting_date desc, seq desc", limit=40)
	readings = frappe.get_all("Meter Reading", filters={"connection": ["in", names]},
	                          fields=["name", "connection", "billing_month", "reading_date", "present_reading", "units", "status", "consumption_type",
	                                  "net_metering", "present_export_reading", "export_units"],
	                          order_by="billing_month desc", limit=24)
	deposits = frappe.get_all("Security Deposit", filters={"connection": ["in", names], "docstatus": 1},
	                          fields=["name", "connection", "deposit_type", "amount", "posting_date"], order_by="posting_date desc", limit=20)
	occupancy = frappe.get_all("Property Occupancy", filters={"consumer": consumer, "parenttype": "Property"},
	                           fields=["parent", "occupant_role", "from_date", "to_date", "share_percent", "is_billed"], order_by="from_date desc")
	open_items = []
	for dt, label in (("Bill Dispute", "Dispute"), ("Recovery Case", "Recovery case"), ("Installment Plan", "Installment plan"),
	                  ("Held Amount", "Held by court order")):
		meta = frappe.get_meta(dt)
		filters = {"connection": ["in", names]}
		if meta.has_field("status"):
			filters["status"] = ["not in", ["Closed", "Resolved", "Decided", "Cancelled", "Released", "Written off"]]
		if meta.has_field("plan_status"):
			filters["plan_status"] = "Active"
		for n in frappe.get_all(dt, filters=filters, pluck="name", limit=10):
			open_items.append({"doctype": dt, "name": n, "label": _(label)})
	if not access.sees_personal():
		c.id_number, c.mobile_no = access.mask(c.id_number), access.mask(c.mobile_no)
	mine = [a["name"] for a in accounts]
	# the account asked for; else the one owing most among the active ones; else the first
	selected = account if account in mine else (max((a for a in accounts if a["status"] == "Active"), key=lambda a: a["owes"], default=None) or
	                                            (accounts[0] if accounts else None))
	selected = selected["name"] if isinstance(selected, dict) else selected
	return {
		"consumer": {"name": c.name, "consumer_name": c.consumer_name, "consumer_type": c.consumer_type, "id_type": c.id_type, "id_number": c.id_number,
		             "mobile_no": c.mobile_no, "email": c.email, "address": c.address, "preferred_language": c.preferred_language,
		             "preferred_channel": c.preferred_channel, "tax_status": c.tax_status, "cash_only": c.cash_only,
		             "registration_no": c.get("registration_no"), "tax_no": c.get("tax_no"),
		             "representatives": [{"name": r.rep_name, "relation": r.relation, "pay": r.can_pay, "notices": r.can_receive_notices,
		                                  "sign": r.can_sign, "changes": r.can_request_changes} for r in c.get("representatives") or []]},
		"accounts": accounts, "total_owed": round(sum(a["owes"] for a in accounts), 2),
		"total_deposit": round(sum(flt(a["security_deposit"]) for a in accounts), 2),
		"bills": bills, "payments": payments, "ledger": ledger, "readings": readings, "deposits": deposits, "occupancy": occupancy,
		"open_items": open_items,
		"service_requests": frappe.get_all("Service Request", filters={"connection": ["in", names]},
		                                   fields=["name", "request_title", "kind", "status", "requested_on", "due_by"], order_by="requested_on desc", limit=20),
		"complaints": frappe.get_all("Bill Dispute", filters={"connection": ["in", names]}, fields=["*"], order_by="creation desc", limit=20),
		# the account the page shows (the mock-up's view of one account; a consumer with several switches between them)
		"selected": selected, "overview": overview(selected) if selected else None,
		"can": {"pay": rights.can("cash.counter") or bool(frappe.has_permission("Payment Receipt", "create")),
		        "request": bool(frappe.has_permission("Service Request", "create")),
		        "print": bool(frappe.has_permission("Utility Bill", "print"))},
	}


def combined_statement(consumer, from_date=None, to_date=None) -> list[dict]:
	"""Every ledger entry of every account of the consumer, by date, with each account's running balance (FS-MST-15)."""
	names = [k.name for k in _connections(consumer)] or [""]
	filters = {"connection": ["in", names]}
	if to_date:
		filters["posting_date"] = ["<=", getdate(to_date)]
	rows = frappe.get_all("Customer Ledger Entry", filters=filters,
	                      fields=["connection", "posting_date", "entry_type", "voucher_no", "remarks", "amount", "balance", "seq"],
	                      order_by="posting_date, connection, seq")
	if from_date:
		start = getdate(from_date)
		opening = {}
		for r in rows:
			if getdate(r.posting_date) < start:
				opening[r.connection] = opening.get(r.connection, 0) + flt(r.amount)
		rows = [{"connection": k, "posting_date": start, "entry_type": _("Opening balance"), "voucher_no": "", "remarks": "", "amount": 0,
		         "balance": round(v, 2)} for k, v in sorted(opening.items())] + [r for r in rows if getdate(r.posting_date) >= start]
	return rows
