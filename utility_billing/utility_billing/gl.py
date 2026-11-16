# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Summary journals, export and receivables reconciliation (FS-GL-01-04, FS-REC-13, 20; FS-RPT-39).

The customer account (subledger) is the authority for every customer transaction (FS-REC-20). The
general ledger receives summary journals:
- one per bill run, made when the run is approved: receivables debited, each charge type's income
  account credited;
- one per society per day for everything else posted that day: payments (cash or bank by payment
  mode), surcharges, fees, credit and debit notes, and reversals.
Every Customer Ledger Entry goes into exactly one journal and is linked to it, so the receivables in
the journals always equal the customer accounts total, apart from entries not yet journaled.
"""

import csv
import io
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import add_days, flt, formatdate, getdate, now_datetime, nowdate
from utility_billing.utility_billing import rights

EXPORTERS = {"System Manager", "Accounts Officer", "Billing Manager", "General Manager"}


class MissingAccount(frappe.ValidationError):
	pass


# ---------------------------------------------------------------- account mapping (FS-GL-01)
def settings(society):
	s = frappe.get_cached_doc("Society", society)
	return frappe._dict(
		receivables=s.receivables_account, surcharge=s.surcharge_income_account, other=s.other_income_account,
		adjustments=s.adjustments_account, write_off=s.write_off_account, payments={r.mode: r.account for r in s.payment_accounts},
		deposits=s.get("deposit_liability_account"), opening=s.get("opening_balance_account"),
	)


def need(account, what):
	if not account:
		raise MissingAccount(_("Set the account for {0}.").format(what))
	return account


def payment_account(soc, society, receipt):
	if receipt.mode == "Cash" and receipt.cash_counter:
		counter_cash = frappe.db.get_value("Cash Counter", receipt.cash_counter, "cash_account")
		if counter_cash:
			return counter_cash
	return need(soc.payments.get(receipt.mode), _("{0} payments (Society {1} → Accounting)").format(_(receipt.mode), society))


def bill_lines(bill_name, soc, society):
	"""Income credits of a bill, by account: each line's charge type income account; lines without a
	charge type (minimum bill adjustment) go to Other Billing Income."""
	out = OrderedDict()
	for l in frappe.get_all("Bill Line", filters={"parent": bill_name, "parenttype": "Utility Bill"},
	                        fields=["charge_type", "label", "amount", "tax"], order_by="idx"):
		if l.tax:
			acct = need(frappe.db.get_value("Tax", l.tax, "account_code"), _("tax {0} (Tax → Tax Payable Account)").format(l.label))
		elif l.charge_type:
			acct = need(frappe.db.get_value("Charge Type", l.charge_type, "account_code"),
			            _("charge type {0} (Charge Type → Income Account)").format(l.charge_type))
		else:
			acct = need(soc.other, _("{0} (Society {1} → Other Billing Income)").format(l.label, society))
		out[acct] = out.get(acct, 0) + flt(l.amount)
	# A tax included in the charges is carved out of them: its part moves from income to the tax account. The charges it was
	# worked out on are not tracked per account, so the carve-out comes off Other Billing Income.
	for t in frappe.get_all("Bill Tax", filters={"parent": bill_name, "parenttype": "Utility Bill", "inclusive": 1}, fields=["tax", "tax_name", "amount"]):
		if flt(t.amount):
			acct = need(frappe.db.get_value("Tax", t.tax, "account_code"), _("tax {0} (Tax → Tax Payable Account)").format(t.tax_name))
			other = need(soc.other, _("the included tax {0} (Society {1} → Other Billing Income)").format(t.tax_name, society))
			out[acct] = out.get(acct, 0) + flt(t.amount)
			out[other] = out.get(other, 0) - flt(t.amount)
	return out


def lines_for(entry_name):
	"""Balanced journal lines [(account, debit, credit)] for one Customer Ledger Entry."""
	e = frappe.get_doc("Customer Ledger Entry", entry_name)
	soc = settings(e.society)
	rec = need(soc.receivables, _("receivables (Society {0} → Accounting)").format(e.society))
	amount = flt(e.amount)
	if e.entry_type == "Transfer":  # between two accounts of one society: receivables to receivables, no journal (increment 23)
		return []
	if e.entry_type == "Reversal":
		return [(a, cr, dr) for a, dr, cr in lines_for(e.reverses)]
	if e.entry_type == "Bill":
		income = bill_lines(e.voucher_no, soc, e.society)
		diff = round(amount - sum(income.values()), 2)
		if diff:
			acct = need(soc.other, _("the bill rounding difference (Society {0} → Other Billing Income)").format(e.society))
			income[acct] = income.get(acct, 0) + diff
		return [(rec, amount, 0)] + [(a, 0, v) if v >= 0 else (a, -v, 0) for a, v in income.items() if v]
	if e.entry_type == "Opening":  # balances brought over at go-live: receivables against the opening balances account
		opening = need(soc.opening, _("opening balances (Society {0} → Go-live → Opening Balances Account)").format(e.society))
		return [(rec, amount, 0), (opening, 0, amount)] if amount >= 0 else [(opening, -amount, 0), (rec, 0, -amount)]
	if e.entry_type == "Surcharge":
		return [(rec, amount, 0), (need(soc.surcharge, _("surcharge income (Society {0} → Accounting)").format(e.society)), 0, amount)]
	if e.entry_type == "Payment":
		receipt = frappe.get_doc("Payment Receipt", e.voucher_no)
		return [(payment_account(soc, e.society, receipt), -amount, 0), (rec, 0, -amount)]
	if e.entry_type == "Fee":
		fee_type = frappe.db.get_value("Fee Charge", e.voucher_no, "fee_type")
		acct = frappe.db.get_value("Fee Type", fee_type, "income_account") or soc.other
		return [(rec, amount, 0), (need(acct, _("fee {0} (Fee Type → Income Account)").format(fee_type)), 0, amount)]
	if e.entry_type == "Write-off":
		expense = need(soc.write_off, _("written-off receivables (Society {0} → Write-off Expense Account)").format(e.society))
		return [(expense, -amount, 0), (rec, 0, -amount)]  # the amount of a write-off entry is negative: the receivable falls
	if e.entry_type == "Deposit offset":
		held = need(soc.deposits, _("security deposits held (Society {0} → Security Deposits Held Account)").format(e.society))
		return [(held, -amount, 0), (rec, 0, -amount)]  # the liability falls, the receivable falls
	if e.entry_type == "Refund":
		mode = frappe.db.get_value("Refund", e.voucher_no, "refund_mode")
		paid_from = need(soc.payments.get(mode), _("{0} payments (Society {1} → Accounting)").format(_(mode), e.society))
		return [(rec, amount, 0), (paid_from, 0, amount)]  # receivables debited (credit used up), cash or bank credited
	if e.entry_type in ("Credit Note", "Debit Note"):
		adj = need(soc.adjustments, _("credit and debit notes (Society {0} → Bill Adjustments and Waivers)").format(e.society))
		return [(rec, amount, 0), (adj, 0, amount)] if amount >= 0 else [(adj, -amount, 0), (rec, 0, -amount)]
	frappe.throw(_("No journal rule for {0} entries.").format(e.entry_type))


# ---------------------------------------------------------------- journals (FS-GL-02)
def build_journal(society, entry_names, journal_type, posting_date, narration, bill_run=None):
	"""One summary journal over the entries, netted per account. Raises MissingAccount (nothing saved)."""
	if not entry_names:
		return None
	totals = OrderedDict()
	for name in entry_names:
		for acct, dr, cr in lines_for(name):
			totals[acct] = totals.get(acct, 0) + flt(dr) - flt(cr)
	rec = settings(society).receivables
	lines = []
	for acct, net in totals.items():
		net = round(net, 2)
		if not net:
			continue
		lines.append({"account": acct, "account_name": frappe.db.get_value("Ledger Account", acct, "account_name"),
		              "debit": net if net > 0 else 0, "credit": -net if net < 0 else 0, "narration": narration[:140]})
	total_dr = round(sum(l["debit"] for l in lines), 2)
	total_cr = round(sum(l["credit"] for l in lines), 2)
	if total_dr != total_cr:
		frappe.throw(_("Journal doesn't balance: debit {0}, credit {1}.").format(total_dr, total_cr))
	j = frappe.get_doc({
		"doctype": "GL Journal", "journal_type": journal_type, "society": society, "posting_date": posting_date,
		"bill_run": bill_run, "narration": narration, "entry_count": len(entry_names), "lines": lines,
		"total_debit": total_dr, "total_credit": total_cr, "receivables_change": round(totals.get(rec, 0), 2),
		"journal_title": f"{society} {journal_type} {formatdate(posting_date)}" + (f" {bill_run}" if bill_run else ""),
	})
	j.flags.from_gl = True
	j.insert(ignore_permissions=True)
	for name in entry_names:
		frappe.db.set_value("Customer Ledger Entry", name, "gl_journal", j.name, update_modified=False)
	return j


def deposit_journal(sd, reverse=False):
	"""A deposit received or paid back moves cash or bank against the deposits held (a liability); the customer's account is untouched
	(FS-MST-10). `reverse` makes the opposite journal when the deposit document is cancelled. Raises MissingAccount (nothing saved)."""
	soc = settings(sd.society)
	held = need(soc.deposits, _("security deposits held (Society {0} → Security Deposits Held Account)").format(sd.society))
	if sd.mode == "Opening balance":  # a deposit brought over at go-live: no money moved now
		money = need(soc.opening, _("opening balances (Society {0} → Go-live → Opening Balances Account)").format(sd.society))
	else:
		money = need(soc.payments.get(sd.mode), _("{0} payments (Society {1} → Accounting)").format(_(sd.mode or ""), sd.society))
	amount = flt(sd.amount)
	pairs = [(money, amount, 0), (held, 0, amount)] if sd.deposit_type == "Received" else [(held, amount, 0), (money, 0, amount)]
	if reverse:
		pairs = [(a, cr, dr) for a, dr, cr in pairs]
	narration = (_("Cancelled: ") if reverse else "") + _("Security deposit {0} {1} ({2})").format(_(sd.deposit_type).lower(), sd.connection, sd.name)
	lines = [{"account": a, "account_name": frappe.db.get_value("Ledger Account", a, "account_name"), "debit": dr, "credit": cr,
	          "narration": narration[:140]} for a, dr, cr in pairs]
	j = frappe.get_doc({
		"doctype": "GL Journal", "journal_type": "Security Deposit", "society": sd.society, "posting_date": frappe.utils.nowdate() if reverse else sd.posting_date,
		"narration": narration, "entry_count": 1, "lines": lines, "total_debit": amount, "total_credit": amount, "receivables_change": 0,
		"journal_title": f"{sd.society} Security Deposit {formatdate(sd.posting_date)} {sd.name}" + (" cancelled" if reverse else ""),
	})
	j.flags.from_gl = True
	j.insert(ignore_permissions=True)
	return j


def pending_deposits(society):
	return frappe.get_all("Security Deposit", filters={"society": society, "docstatus": 1, "deposit_type": ["in", ["Received", "Refunded"]],
	                                                   "gl_journal": ["is", "not set"]}, pluck="name")


def make_bill_run_journal(run_name):
	run = frappe.get_doc("Bill Run", run_name)
	if run.docstatus != 1:
		frappe.throw(_("Bill run {0} isn't posted.").format(run_name))
	bills = frappe.get_all("Utility Bill", filters={"bill_run": run_name, "docstatus": ["in", [1, 2]]}, pluck="name")
	entries = frappe.get_all("Customer Ledger Entry", filters={
		"entry_type": "Bill", "voucher_type": "Utility Bill", "voucher_no": ["in", bills or [""]],
		"gl_journal": ["is", "not set"]}, pluck="name", order_by="seq")
	j = build_journal(run.society, entries, "Bill Run", run.issue_date,
	                  _("Bills of {0} ({1})").format(run.run_title, run_name), bill_run=run_name)
	if j:
		run.db_set("gl_journal", j.name, update_modified=False)
	return j


def after_run_posted(run):
	"""Called when a bill run is approved. A missing account mapping doesn't stop billing: the entries
	wait (they show in the reconciliation) until the accounts are set and the journal is made."""
	frappe.db.savepoint("gl_bill_run")
	try:
		return make_bill_run_journal(run.name)
	except MissingAccount as e:
		frappe.db.rollback(save_point="gl_bill_run")
		frappe.msgprint(_("Bills are posted, but the bill run journal wasn't made: {0} Then use Make Journals.").format(e),
		                indicator="orange", alert=True)
		return None


def day_bounds(date):
	d = getdate(date)
	return f"{d} 00:00:00", f"{d} 23:59:59.999999"


def make_daily_journal(society, date):
	"""Everything posted to the society's customer accounts on `date` (by posting time), except bills,
	which go to their bill run's journal."""
	start, end = day_bounds(date)
	entries = frappe.get_all("Customer Ledger Entry", filters={
		"society": society, "entry_type": ["not in", ["Bill", "Transfer"]], "gl_journal": ["is", "not set"],
		"posted_on": ["between", [start, end]]}, pluck="name", order_by="posted_on, seq")
	return build_journal(society, entries, "Daily Transactions", getdate(date),
	                     _("Collections and account transactions of {0}").format(formatdate(date)))


def pending_runs(society):
	return frappe.get_all("Bill Run", filters={"society": society, "docstatus": 1, "gl_journal": ["is", "not set"]},
	                      pluck="name", order_by="issue_date")


def make_journals_for(society, up_to=None):
	"""Bill run journals still missing, then one daily journal per day with unjournaled entries up to `up_to`."""
	made = []
	for run in pending_runs(society):
		j = make_bill_run_journal(run)
		if j:
			made.append(j.name)
	for name in pending_deposits(society):  # deposits whose journal waited for an account mapping
		sd = frappe.get_doc("Security Deposit", name)
		try:
			j = deposit_journal(sd)
		except MissingAccount:
			continue  # still waits; the reconciliation is not affected (deposits don't touch receivables)
		sd.db_set("gl_journal", j.name, update_modified=False)
		made.append(j.name)
	up_to = getdate(up_to or nowdate())
	rows = frappe.db.sql(
		"""select distinct date(posted_on) from `tabCustomer Ledger Entry`
		where society = %s and entry_type not in ('Bill', 'Transfer') and ifnull(gl_journal, '') = '' and date(posted_on) <= %s
		order by 1""", (society, up_to))
	for (day,) in rows:
		j = make_daily_journal(society, day)
		if j:
			made.append(j.name)
	return made


@frappe.whitelist(methods=["POST"])
def make_journals(society: str, up_to: str | None = None):
	if not rights.can("gl.export"):
		raise frappe.PermissionError(_("Only Accounts Officers and managers make journals."))
	return make_journals_for(society, up_to)


def daily():
	"""Scheduler: journals up to yesterday and a reconciliation for every society (FS-GL-02, 04)."""
	yesterday = add_days(nowdate(), -1)
	for society in frappe.get_all("Society", pluck="name"):
		try:
			make_journals_for(society, yesterday)
		except MissingAccount:
			frappe.log_error(title=f"Journals not made for {society}: account missing")
		reconcile(society)


# ---------------------------------------------------------------- reconciliation (FS-GL-04, REC-13)
def reconcile(society, save=True):
	"""Customer accounts total vs receivables in the journals. The difference must be fully explained by
	ledger entries not yet in a journal; anything else is an error to investigate."""
	rec = settings(society).receivables
	subledger = flt(frappe.db.sql("select sum(amount) from `tabCustomer Ledger Entry` where society = %s", society)[0][0])
	posted = 0.0
	if rec:
		posted = flt(frappe.db.sql(
			"""select sum(l.debit - l.credit) from `tabGL Journal Line` l join `tabGL Journal` j on j.name = l.parent
			where j.society = %s and l.account = %s""", (society, rec))[0][0])
	pending = frappe.db.sql(
		"""select count(*), sum(amount) from `tabCustomer Ledger Entry` where society = %s and ifnull(gl_journal, '') = ''""",
		society)[0]
	pending_count, pending_amount = int(pending[0] or 0), flt(pending[1])
	difference = round(subledger - posted, 2)
	unexplained = round(difference - pending_amount, 2)
	status = "Difference" if unexplained else ("Timing differences" if pending_count else "Matched")
	result = frappe._dict(society=society, as_of=nowdate(), customer_accounts_total=subledger, receivables_posted=posted,
	                      difference=difference, not_journaled_count=pending_count, not_journaled_amount=pending_amount,
	                      unexplained=unexplained, status=status,
	                      notes=None if rec else _("No receivables account set on the society."))
	if save:
		doc = frappe.get_doc({"doctype": "Receivables Reconciliation", **result})
		doc.flags.from_gl = True
		doc.insert(ignore_permissions=True)
		result.name = doc.name
	return result


@frappe.whitelist(methods=["POST"])
def reconcile_now(society: str):
	if not rights.can("gl.view"):
		raise frappe.PermissionError(_("You can't run the reconciliation."))
	return reconcile(society)


# ---------------------------------------------------------------- export (FS-GL-03)
DATE_FORMATS = {"dd-mm-yyyy": "%d-%m-%Y", "mm/dd/yyyy": "%m/%d/%Y", "yyyy-mm-dd": "%Y-%m-%d", "dd/mm/yyyy": "%d/%m/%Y"}


def export_rows(journals, layout):
	fmt = DATE_FORMATS.get(layout.date_format, "%d-%m-%Y")
	excel = layout.file_format == "Excel"

	def amount(v, blank_zero=True):
		# Accounting imports expect two decimals (20000.00); Excel gets real numbers.
		v = round(flt(v), 2)
		if blank_zero and not v:
			return ""
		return v if excel else f"{v:.2f}"

	rows = [[c.header for c in layout.columns]] if layout.include_header else []
	for j in journals:
		for line in j.lines:
			values = {
				"Journal No": j.name, "Date": getdate(j.posting_date).strftime(fmt), "Account Code": line.account,
				"Account Name": line.account_name, "Debit": amount(line.debit), "Credit": amount(line.credit),
				"Amount (debit +, credit -)": amount(flt(line.debit) - flt(line.credit), False), "Narration": j.narration or "",
				"Line Narration": line.narration or "", "Society": j.society, "Line Count": len(j.lines),
			}
			rows.append([(c.fixed_text or "") if c.source == "Fixed Text" else values.get(c.source, "") for c in layout.columns])
	return rows


@frappe.whitelist(methods=["POST"])
def export_journals(society: str, from_date: str, to_date: str, layout: str, journal_type: str | None = None):
	"""Journals of a period as CSV or Excel in the chosen layout, for Peachtree/Sage, QuickBooks or any
	other accounting software (FS-GL-03). Each export is recorded on the journals."""
	if not rights.can("gl.export"):
		raise frappe.PermissionError(_("Only Accounts Officers and managers export journals."))
	lay = frappe.get_doc("Journal Export Layout", layout)
	filters = {"society": society, "posting_date": ["between", [getdate(from_date), getdate(to_date)]]}
	if journal_type:
		filters["journal_type"] = journal_type
	names = frappe.get_all("GL Journal", filters=filters, pluck="name", order_by="posting_date, name")
	if not names:
		frappe.throw(_("No journals in this period."))
	journals = [frappe.get_doc("GL Journal", n) for n in names]
	rows = export_rows(journals, lay)
	base = f"journals-{society}-{getdate(from_date)}-{getdate(to_date)}"
	if lay.file_format == "Excel":
		from frappe.utils.xlsxutils import make_xlsx

		content, filename = make_xlsx(rows, "Journals").getvalue(), f"{base}.xlsx"
	else:
		buf = io.StringIO()
		csv.writer(buf, lineterminator="\r\n").writerows(rows)
		content, filename = buf.getvalue().encode("utf-8"), f"{base}.csv"
	for j in journals:
		frappe.db.set_value("GL Journal", j.name, {"export_count": (j.export_count or 0) + 1,
		                                           "last_exported_on": now_datetime(),
		                                           "last_exported_by": frappe.session.user}, update_modified=False)
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
