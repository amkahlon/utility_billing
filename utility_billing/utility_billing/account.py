# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Customer account (subledger) service: FS-REC-01, 03, 10-12, 14; FS-PAY-05, 07, 08, 12.

The account is the connection (its reference number). Every bill, surcharge, payment, fee, credit or
debit note and reversal is a Customer Ledger Entry, written only here, numbered in sequence per
account, with the balances after it. Entries are never edited or deleted; mistakes are reversed.
The rules for splitting amounts over the balances are in account_engine.py.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import account_engine as engine
from utility_billing.utility_billing import flags, periods
from utility_billing.utility_billing import rights

FIELDS = {"current": "current_balance", "arrears": "arrears_balance", "surcharge": "surcharge_balance",
          "fees": "fees_balance", "credit": "credit_balance"}
BUCKET_OF = {"Current bill": "current", "Arrears": "arrears", "Surcharge": "surcharge", "Fees": "fees"}
REVERSERS = {"System Manager", "Billing Manager"}


# ---------------------------------------------------------------- reading the account
def last_entry(connection):
	return frappe.db.get_value(
		"Customer Ledger Entry", {"connection": connection},
		["name", "seq", "balance", *FIELDS.values()], order_by="seq desc", as_dict=True,
	)


def balances(connection) -> dict:
	last = last_entry(connection)
	if not last:
		return engine.empty()
	return {b: engine.D(last[f]) for b, f in FIELDS.items()}


def balance(connection) -> float:
	"""What the consumer owes now (negative = in credit)."""
	return flt(engine.total(balances(connection)))


def balance_as_of(connection, date, exclude=None) -> float:
	"""What was owed at the end of `date`, by transaction date (FS-PAY-08)."""
	filters = {"connection": connection, "posting_date": ["<=", getdate(date)]}
	if exclude:
		filters["name"] = ["not in", list(exclude)]
	return flt(sum(flt(x) for x in frappe.get_all("Customer Ledger Entry", filters=filters, pluck="amount")))


def posting_order(society):
	return engine.order_of(frappe.get_cached_value("Society", society, "payment_posting_order"))


# ---------------------------------------------------------------- writing entries
def lock(connection):
	"""Serialise postings to one account, so balances and sequence numbers never interleave."""
	frappe.db.sql("select name from `tabConnection` where name = %s for update", connection)


def entry_fields(connection, conn, last, before, after, deltas, entry_type, voucher_type, voucher_no, posting_date, remarks=None, reverses=None):
	"""The fields of one ledger entry: `conn` the account's consumer, name and society, `last` its last entry, `before` and `after` the
	balances around this entry."""
	return {
		"doctype": "Customer Ledger Entry", "connection": connection, "consumer": conn.consumer,
		"consumer_name": conn.consumer_name, "society": conn.society, "posting_date": posting_date,
		"posted_on": now_datetime(), "period": posting_date.strftime("%Y-%m"), "seq": cint(last.seq if last else 0) + 1,
		"entry_type": entry_type, "voucher_type": voucher_type, "voucher_no": voucher_no,
		"amount": flt(engine.total(after) - engine.total(before)), "reverses": reverses, "remarks": remarks,
		"balance": flt(engine.total(after)),
		**{f"d_{b}": flt(deltas.get(b)) for b in engine.ALL},
		**{f: flt(after[b]) for b, f in FIELDS.items()},
	}


def bill_entry_fields(bill, conn, last, order) -> dict:
	"""The ledger entry of a posted bill, as post_bill writes it, for a bill run that writes its entries a group at a time (the
	performance step). The run has locked the account, read `last` and checked the period."""
	before = balances_of(last)
	deltas = engine.bill(before, bill.current_amount, order)
	return entry_fields(bill.connection, conn, last, before, engine.apply(before, deltas), deltas, "Bill", "Utility Bill", bill.name,
	                    getdate(bill.issue_date), _("Bill for {0}").format(getdate(bill.billing_month).strftime("%B %Y")))


def post(connection, entry_type, deltas, voucher_type, voucher_no, posting_date=None, remarks=None, reverses=None, last=False):
	"""Write one ledger entry. Callers lock the account first. `last`: the account's last entry when the caller has just read it
	(None for an account with no entries yet)."""
	conn = frappe.db.get_value("Connection", connection, ["consumer", "consumer_name", "society"], as_dict=True)
	last = last_entry(connection) if last is False else last
	before = balances_of(last)  # balances(), from the entry just read
	after = engine.apply(before, deltas)
	posting_date = getdate(posting_date or nowdate())
	periods.assert_open(conn.society, posting_date, entry_type)  # a closed month is locked (FS-ACC-10)
	entry = frappe.get_doc(entry_fields(connection, conn, last, before, after, deltas, entry_type, voucher_type, voucher_no, posting_date,
	                                    remarks, reverses))
	entry.flags.from_account = True
	entry.insert(ignore_permissions=True)
	if reverses:
		frappe.db.set_value("Customer Ledger Entry", reverses, "reversed_by", entry.name, update_modified=False)
	return entry


def deltas_of(entry_name) -> dict:
	e = frappe.db.get_value("Customer Ledger Entry", entry_name, [f"d_{b}" for b in engine.ALL], as_dict=True)
	return {b: engine.D(e[f"d_{b}"]) for b in engine.ALL}


def reverse_entry(entry_name, voucher_type, voucher_no, posting_date=None, remarks=None):
	e = frappe.db.get_value("Customer Ledger Entry", entry_name, ["connection", "reversed_by"], as_dict=True)
	if e.reversed_by:
		frappe.throw(_("Ledger entry {0} is already reversed by {1}.").format(entry_name, e.reversed_by))
	lock(e.connection)
	deltas = engine.reverse(balances(e.connection), deltas_of(entry_name))
	return post(e.connection, "Reversal", deltas, voucher_type, voucher_no, posting_date, remarks, reverses=entry_name)


# ---------------------------------------------------------------- bills
def balances_of(last) -> dict:
	"""The balances an entry leaves (an empty account when there's none)."""
	return {b: engine.D(last[f]) for b, f in FIELDS.items()} if last else engine.empty()


def post_bill(bill, last=False):
	"""A posted bill: the unpaid current bill moves to arrears, the new bill becomes current, and any
	credit pays it (FS-REC-10). `last`: the account's last entry when the caller has just read it (a bill run does)."""
	lock(bill.connection)
	last = last_entry(bill.connection) if last is False else last
	deltas = engine.bill(balances_of(last), bill.current_amount, posting_order(bill.society))
	entry = post(bill.connection, "Bill", deltas, "Utility Bill", bill.name, bill.issue_date,
	             _("Bill for {0}").format(getdate(bill.billing_month).strftime("%B %Y")), last=last)
	frappe.db.set_value("Utility Bill", bill.name, {"ledger_entry": entry.name, "surcharge_status": "Not due yet"},
	                    update_modified=False)
	return entry


def check_bill_reversible(bill):
	"""A posted bill can be undone only while nothing else is in force on its account after it (its own
	surcharge aside; an entry already reversed, and its reversal, cancel out): reverse those first, so no
	payment is silently re-applied (FS-REC-12)."""
	if not bill.ledger_entry:
		return
	seq = frappe.db.get_value("Customer Ledger Entry", bill.ledger_entry, "seq")
	later = frappe.db.get_value("Customer Ledger Entry", {"connection": bill.connection, "seq": [">", seq],
	                                                      "voucher_no": ["!=", bill.name],
	                                                      "reversed_by": ["is", "not set"], "reverses": ["is", "not set"]},
	                            ["name", "entry_type", "voucher_no"], order_by="seq", as_dict=True)
	if later:
		frappe.throw(_("Bill {0} can't be cancelled: {1} {2} was posted to account {3} after it. Reverse it first.").format(
			bill.name, _(later.entry_type), later.voucher_no, bill.connection))


def reverse_bill(bill):
	"""Undo a posted bill (and its surcharge) when its run is cancelled."""
	if not bill.ledger_entry:
		return
	lock(bill.connection)
	check_bill_reversible(bill)
	if bill.surcharge_entry and not frappe.db.get_value("Customer Ledger Entry", bill.surcharge_entry, "reversed_by"):
		reverse_entry(bill.surcharge_entry, "Utility Bill", bill.name, remarks=_("Bill cancelled"))
	reverse_entry(bill.ledger_entry, "Utility Bill", bill.name, remarks=_("Bill cancelled"))


def arrears_for_new_bill(connection, bal=None) -> float:
	"""Everything owed before a new bill: arrears, unpaid current bill, surcharge and fees, less credit, less what former holders still
	owe (their final bills and balances are theirs: user decision 2026-11-16, the incoming holder starts clean)."""
	from utility_billing.utility_billing import holders  # noqa: PLC0415

	owed = flt(engine.total(bal)) if bal is not None else balance(connection)
	return round(owed - holders.former_outstanding(connection), 2)


# ---------------------------------------------------------------- surcharge (FS-REC-03, FS-PAY-08)
def surcharge_deadline(bill):
	due = bill.get("extended_due_date") or bill.due_date  # a due date extension moves the deadline (FS-WF-03)
	return add_days(due, cint(frappe.db.get_value("Society", bill.society, "grace_days")))


NOTHING_PAID = "Nothing paid by the due date"


def paid_in_time(bill, deadline) -> bool:
	"""Whether the bill escapes the surcharge, counting payments by the date the consumer paid (FS-PAY-08).
	Default rule: the account was fully paid by the deadline. Societies that waive the surcharge on any
	payment ("Nothing paid by the due date") only need a payment made between issue and deadline.
	A final bill is paid in time when its former holder's balance is; the current holder's bills leave former holders' balances out."""
	from utility_billing.utility_billing import holders  # noqa: PLC0415

	if cint(bill.get("final_bill")):
		fob = frappe.db.get_value("Former Occupant Balance", {"final_bill": bill.name}, ["name", "amount", "surcharge"], as_dict=True)
		if fob:
			return flt(fob.amount) - holders.paid_by(fob.name, deadline) <= 0.005
	if balance_as_of(bill.connection, deadline) - holders.former_outstanding(bill.connection, deadline) <= 0:
		return True
	if frappe.db.get_value("Society", bill.society, "surcharge_rule") == NOTHING_PAID:
		return bool(frappe.db.exists("Customer Ledger Entry", {
			"connection": bill.connection, "entry_type": "Payment", "reversed_by": ["is", "not set"],
			"posting_date": ["between", [getdate(bill.issue_date), getdate(deadline)]],
		}))
	return False


def apply_surcharges(as_of=None, society=None):
	"""Daily: add each posted bill's late payment surcharge the day after its due date (+ grace days),
	unless it was paid in time (see paid_in_time)."""
	as_of = getdate(as_of or nowdate())
	filters = {"docstatus": 1, "surcharge_status": "Not due yet", "due_date": ["<", as_of]}
	if society:
		filters["society"] = society
	posted = 0
	for name in frappe.get_all("Utility Bill", filters=filters, pluck="name", order_by="due_date, name"):
		bill = frappe.get_doc("Utility Bill", name)
		deadline = surcharge_deadline(bill)
		if deadline >= as_of:
			continue
		if frappe.db.exists("Bill Dispute", {"bill": bill.name, "status": "Open"}):
			continue  # the surcharge is held while the bill is in dispute (FS-WF-10)
		if flags.effect(bill.connection, flags.NO_SURCHARGE, bill.due_date, deadline):  # staff, exempt, court case (FS-MST-14)
			bill.db_set("surcharge_status", "Not charged (flagged)", update_modified=False)
			continue
		if flt(bill.surcharge_amount) <= 0 or paid_in_time(bill, deadline):
			bill.db_set("surcharge_status", "Not charged (paid in time)", update_modified=False)
			continue
		lock(bill.connection)
		deltas = engine.charge(balances(bill.connection), "surcharge", bill.surcharge_amount, posting_order(bill.society))
		# dated the day after the deadline, or in the first open month when that one is already closed
		entry = post(bill.connection, "Surcharge", deltas, "Utility Bill", bill.name, periods.next_open_date(bill.society, add_days(deadline, 1)),
		             _("Late payment surcharge on the {0} bill").format(getdate(bill.billing_month).strftime("%B %Y")))
		bill.db_set({"surcharge_status": "Posted", "surcharge_entry": entry.name}, update_modified=False)
		if cint(bill.get("final_bill")):  # the surcharge on a final bill is the former holder's (increment 22)
			from utility_billing.utility_billing import holders  # noqa: PLC0415

			holders.surcharge_added(bill, bill.surcharge_amount)
		posted += 1
	return posted


def recheck_surcharge(connection, payment_date):
	"""A payment dated on or before a bill's due date may reach the system after the surcharge was
	added (bank or channel delay). If the bill now counts as paid in time, reverse the surcharge."""
	for name in frappe.get_all("Utility Bill", filters={"connection": connection, "docstatus": 1, "surcharge_status": "Posted"},
	                           pluck="name"):
		bill = frappe.get_doc("Utility Bill", name)
		deadline = surcharge_deadline(bill)
		if getdate(payment_date) > deadline:
			continue
		if paid_in_time(bill, deadline):
			reverse_entry(bill.surcharge_entry, "Utility Bill", bill.name, remarks=_("Paid by the due date (FS-PAY-08)"))
			bill.db_set("surcharge_status", "Reversed (paid in time)", update_modified=False)


@frappe.whitelist(methods=["POST"])
def run_surcharges(society: str | None = None):
	"""Apply due surcharges now (the scheduler does this daily)."""
	if not rights.can("payments.reverse"):
		raise frappe.PermissionError(_("Only a Billing Manager can run surcharges."))
	return apply_surcharges(society=society)


# ---------------------------------------------------------------- payments, fees, notes
def post_payment(receipt):
	lock(receipt.connection)
	before = balances(receipt.connection)
	society = frappe.get_cached_doc("Society", receipt.society)
	owed = engine.total(before)
	amount = engine.D(receipt.amount)
	if not receipt.get("shared"):  # a billing account's payment is checked once for all its services (billaccounts.check_payment)
		if amount > max(owed, engine.D(0)) and not cint(society.allow_overpayment):
			frappe.throw(_("The account owes Rs {0}; this society doesn't accept overpayments.").format(flt(max(owed, 0))))
		if amount < owed and not cint(society.allow_part_payment):
			frappe.throw(_("The account owes Rs {0}; this society doesn't accept part payments.").format(flt(owed)))
	deltas = engine.payment(before, amount, engine.order_of(society.payment_posting_order))
	entry = post(receipt.connection, "Payment", deltas, "Payment Receipt", receipt.name, receipt.payment_date,
	             _("{0} payment").format(_(receipt.mode)))
	recheck_surcharge(receipt.connection, receipt.payment_date)
	return entry, deltas


def post_fee(fee):
	lock(fee.connection)
	deltas = engine.charge(balances(fee.connection), "fees", fee.amount, posting_order(fee.society))
	fee_name = frappe.db.get_value("Fee Type", fee.fee_type, "fee_name")
	return post(fee.connection, "Fee", deltas, "Fee Charge", fee.name, fee.posting_date, fee_name)


def post_note(note):
	lock(note.connection)
	bal = balances(note.connection)
	bucket = BUCKET_OF[note.bucket]
	if note.note_type == "Credit Note":
		try:
			deltas = engine.credit_note(bal, bucket, note.amount)
		except ValueError:
			frappe.throw(_("A credit note can't exceed the {0} balance of Rs {1}.").format(_(note.bucket), flt(bal[bucket])))
	else:
		deltas = engine.charge(bal, bucket, note.amount, posting_order(note.society))
	return post(note.connection, note.note_type, deltas, "Account Adjustment", note.name, nowdate(), note.remarks)


def post_refund(refund):
	"""A refund of credit: the credit balance goes down, money goes out (FS-PAY-16)."""
	lock(refund.connection)
	try:
		deltas = engine.refund(balances(refund.connection), refund.amount)
	except ValueError as e:
		frappe.throw(_("Refund refused: {0}").format(e))
	return post(refund.connection, "Refund", deltas, "Refund", refund.name, nowdate(),
	            _("Refund to {0} by {1}").format(refund.payee_name, _(refund.refund_mode)))


def post_deposit_offset(sd):
	"""A security deposit applied to what the account owes, like a payment from the deposit (FS-MST-10)."""
	lock(sd.connection)
	before = balances(sd.connection)
	deltas = engine.payment(before, engine.D(sd.amount), posting_order(sd.society))
	return post(sd.connection, "Deposit offset", deltas, "Security Deposit", sd.name, sd.posting_date,
	            _("Security deposit applied to the account"))


def post_write_off(wo):
	"""Write off arrears, surcharge and fees the society can't recover (FS-REC-09, FS-WF-14)."""
	lock(wo.connection)
	try:
		deltas = engine.write_off(balances(wo.connection), wo.amount)
	except ValueError as e:
		frappe.throw(_("Write-off refused: {0}").format(e))
	return post(wo.connection, "Write-off", deltas, "Write Off", wo.name, nowdate(),
	            _("Written off by board resolution {0}").format(wo.resolution_no))


def check_reason(reason_code, used_for):
	if not reason_code:
		frappe.throw(_("Choose a reason."))
	applies, enabled = frappe.db.get_value("Reason Code", reason_code, ["applies_to", "enabled"]) or (None, 0)
	if not enabled or applies != used_for:
		frappe.throw(_("Reason {0} isn't an enabled reason for {1}.").format(reason_code, _(used_for)))


@frappe.whitelist(methods=["POST"])
def reverse_document(doctype: str, name: str, reason_code: str, remarks: str | None = None):
	"""Reverse a posted payment (for example a dishonoured cheque) or fee, with a reason, restoring
	what it paid (FS-PAY-12, FS-REC-12). Billing Manager only; never the person who took it."""
	if doctype not in ("Payment Receipt", "Fee Charge"):
		frappe.throw(_("Only payments and fees are reversed here."))
	if not rights.can("payments.reverse"):
		raise frappe.PermissionError(_("Only a Billing Manager can reverse a {0}.").format(_(doctype)))
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 1:
		frappe.throw(_("Only a posted {0} can be reversed.").format(_(doctype)))
	if doc.owner == frappe.session.user and frappe.session.user != "Administrator":
		frappe.throw(_("You can't reverse a {0} you entered yourself.").format(_(doctype)))
	check_reason(reason_code, "Payment Reversal" if doctype == "Payment Receipt" else "Fee Reversal")
	if not (remarks or "").strip():
		frappe.throw(_("Explain the reversal."))
	doc.flags.reversal = {"reason_code": reason_code, "remarks": remarks.strip()}
	doc.flags.ignore_permissions = True
	doc.cancel()
	if doctype == "Payment Receipt" and reason_code == "CHQ-BOUNCE":
		from utility_billing.utility_billing import corrections

		corrections.after_dishonour(doc)  # the dishonour fee and the cash-only flag, as the society set them (FS-WF-11)
	return doc.name


# ---------------------------------------------------------------- enquiry and statement
def connection_of(reference_no):
	ref = (reference_no or "").strip()
	if ref and frappe.db.exists("Connection", ref):
		return ref
	found = frappe.db.get_value("Connection", {"reference_no": ref}, "name")
	if found:
		return found
	from utility_billing.utility_billing import legacy  # noqa: PLC0415

	found = legacy.connection_of(ref)  # an old account number from the previous system, while still accepted (increment 29)
	if found:
		return found
	rows = accounts_of_consumer(ref)[1]  # a consumer ID, when the consumer has one open account (increment 31)
	return rows[0].name if len(rows) == 1 else None


def accounts_of_consumer(consumer_id, permitted=False):
	"""(consumer, open accounts) for a consumer ID: ours, or the previous system's customer ID while its society still accepts old
	numbers. User's decision, 2026-11-20: payments are found by reference number and consumer ID. `permitted`: only the accounts the
	current user may read (for a list shown on screen)."""
	ref = (consumer_id or "").strip()
	if not ref:
		return None, []
	old = False
	consumer = ref if frappe.db.exists("Consumer", ref) else None
	if not consumer:
		names = frappe.get_all("Consumer", filters={"legacy_customer_id": ref}, pluck="name", limit=2)
		if len(names) != 1:  # unknown, or the same old ID on two consumers
			return None, []
		consumer, old = names[0], True
	rows = (frappe.get_list if permitted else frappe.get_all)(
		"Connection", filters={"consumer": consumer, "status": ["in", ["Active", "Disconnected"]]},
		fields=["name", "society", "utility", "property", "status"], order_by="name")
	if old:
		from utility_billing.utility_billing import legacy  # noqa: PLC0415

		rows = [r for r in rows if legacy.accepted(r.society)]
	return consumer, rows


@frappe.whitelist()
def enquiry(reference_no: str):
	"""Bill enquiry for counters and channels (FS-PAY-05): consumer, latest bill, amounts, balances."""
	connection = connection_of(reference_no)
	if not connection:
		from utility_billing.utility_billing import billaccounts  # noqa: PLC0415

		ba = billaccounts.account_of(reference_no)  # a billing account number: all its services at once (increment 22)
		if ba:
			frappe.get_doc("Billing Account", ba).check_permission("read")
			return billaccounts.enquiry(ba)
		consumer, rows = accounts_of_consumer(reference_no)
		if len(rows) > 1:
			frappe.throw(_("Consumer {0} has {1} accounts. Use the reference number of the account to pay.").format(consumer, len(rows)))
		frappe.throw(_("No account with reference number or consumer ID {0}.").format(reference_no))
	frappe.get_doc("Connection", connection).check_permission("read")
	return enquiry_of(connection)


def enquiry_of(connection):
	"""The enquiry without the desk permission check: the billing API checks its client's scopes and societies instead."""
	conn = frappe.db.get_value("Connection", connection, ["consumer_name", "society", "status"], as_dict=True)
	bill = frappe.db.get_value(
		"Utility Bill", {"connection": connection, "docstatus": 1},
		["name", "billing_month", "due_date", "extended_due_date", "payable_within_due", "payable_after_due", "surcharge_status", "issue_date", "installment_due"],
		order_by="billing_month desc, creation desc", as_dict=True,
	)
	bal = balances(connection)
	owed = flt(engine.total(bal))
	from utility_billing.utility_billing import holders  # noqa: PLC0415

	former = holders.former_outstanding(connection)  # what former holders still owe: collected from them, not the current holder
	return {
		"connection": connection, "consumer_name": conn.consumer_name, "society": conn.society, "status": conn.status,
		"bill": bill.name if bill else None,
		"bill_month": getdate(bill.billing_month).strftime("%B %Y") if bill else None,
		"due_date": str(bill.extended_due_date or bill.due_date) if bill else None,
		"payable_within_due": flt(bill.payable_within_due) if bill else 0,
		"payable_after_due": flt(bill.payable_after_due) if bill else 0,
		"balance": owed, "balances": {b: flt(v) for b, v in bal.items()}, "former_balance": former,
		# What to collect today: the whole balance (which already includes any posted surcharge), less former holders' balances.
		"amount_due": _amount_due(connection, bill, round(owed - former, 2)),
	}


def _amount_due(connection, bill, owed) -> float:
	"""What to collect today: the whole balance, or under an installment plan the latest bill's payable amount less what has been
	paid since it was issued (the rest of the arrears isn't due yet)."""
	if bill and flt(bill.installment_due):
		paid = flt(frappe.db.sql("""select -sum(amount) from `tabCustomer Ledger Entry` where connection = %s and entry_type = 'Payment'
		                            and ifnull(reversed_by, '') = '' and posting_date >= %s""", (connection, bill.issue_date))[0][0])
		return max(min(flt(owed), flt(bill.payable_within_due) - paid), 0)
	return max(owed, 0)


def statement(connection, from_date=None, to_date=None):
	"""Account statement for a date range (FS-REC-14): opening balance, entries, closing balance.
	Without an end date it runs to today or the latest entry, whichever is later (a bill can carry a
	future issue date), so the closing balance matches the account's balance now."""
	if not to_date:
		latest = frappe.db.get_value("Customer Ledger Entry", {"connection": connection}, "posting_date",
		                             order_by="posting_date desc")
		to_date = max(getdate(nowdate()), getdate(latest)) if latest else nowdate()
	to_date = getdate(to_date)
	from_date = getdate(from_date) if from_date else None
	opening = balance_as_of(connection, add_days(from_date, -1)) if from_date else 0
	filters = {"connection": connection, "posting_date": ["<=", to_date]}
	if from_date:
		filters["posting_date"] = ["between", [from_date, to_date]]
	rows = frappe.get_all(
		"Customer Ledger Entry", filters=filters,
		fields=["name", "posting_date", "entry_type", "voucher_type", "voucher_no", "remarks", "amount", "reverses"],
		order_by="posting_date, seq",
	)
	running = flt(opening)
	for r in rows:
		running += flt(r.amount)
		r.running = running
	return {"connection": connection, "from_date": from_date, "to_date": to_date, "opening": flt(opening),
	        "closing": running, "rows": rows, "balances": {b: flt(v) for b, v in balances(connection).items()}}


@frappe.whitelist()
def account_summary(connection: str):
	"""Balances shown on the Connection form."""
	frappe.get_doc("Connection", connection).check_permission("read")
	last = last_entry(connection)
	return {"balances": {b: flt(v) for b, v in balances(connection).items()}, "balance": balance(connection),
	        "entries": frappe.db.count("Customer Ledger Entry", {"connection": connection}),
	        "last_entry": last.name if last else None}
