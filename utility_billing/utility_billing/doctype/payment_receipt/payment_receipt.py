# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""Payment Receipt (FS-PAY-01, 02, 06-08, 12, 13).

Counter: the cashier scans or types the reference number, takes full or part payment in cash, cheque,
pay order or card, and submits; the receipt prints. Receipts need the cashier's open day for today
and are numbered per counter (CB1-00001, ...), never reused.
Bank / Channel: an Accounts Officer records a payment made through a bank, 1Link or Kuickpay, with
the date the consumer paid and the channel's transaction ID, which can post only once.

Submitting posts the payment to the customer account in the society's posting order. A posted receipt
is never edited; a Billing Manager reverses it with a reason (account.reverse_document)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt, getdate, now_datetime, nowdate

from utility_billing.utility_billing import account, notify
from utility_billing.utility_billing.doctype.cashier_day.cashier_day import open_day_of, refresh_totals

CHANNEL_ROLES = {"System Manager", "Accounts Officer"}
INSTRUMENT_MODES = {"Cheque", "Pay Order"}


class PaymentReceipt(Document):
	def before_insert(self):
		if self.payment_channel == "Counter":
			self.set_counter_day()

	def autoname(self):
		if self.payment_channel == "Counter":
			self.name = make_autoname(f"{self.cash_counter}-.#####", doc=self)
		else:
			self.name = make_autoname("BNK-.YYYY.-.#####", doc=self)

	def set_counter_day(self):
		day = open_day_of()
		if not day:
			frappe.throw(_("Open your cashier day before taking payments."))
		if str(day.business_date) != nowdate():
			frappe.throw(_("Your open day {0} is for {1}. Close it and open today's day first.").format(
				day.name, day.business_date))
		self.cash_counter, self.cashier_day, self.payment_date = day.cash_counter, day.name, day.business_date

	def validate(self):
		self.reference_no = (self.reference_no or "").strip()
		self.connection = account.connection_of(self.reference_no)
		if not self.connection:
			frappe.throw(_("No account with reference number {0}.").format(self.reference_no))
		info = account.enquiry_of(self.connection) if self.flags.api_client else account.enquiry(self.connection)
		self.consumer_name, self.society = info["consumer_name"], info["society"]
		self.latest_bill, self.bill_due_date = info["bill"], info["due_date"]
		self.payable_within_due, self.payable_after_due = info["payable_within_due"], info["payable_after_due"]
		self.balance_before = info["balance"]
		self.amount = flt(self.amount, 2)
		if self.amount <= 0:
			frappe.throw(_("Enter the amount received."))
		if self.payment_channel == "Counter":
			if self.mode not in ("Cash", "Cheque", "Pay Order", "Card"):
				frappe.throw(_("At the counter, take cash, cheque, pay order or card."))
			if self.cash_counter and frappe.db.get_value("Cash Counter", self.cash_counter, "society") != self.society:
				frappe.throw(_("Account {0} belongs to society {1}; counter {2} doesn't.").format(
					self.connection, self.society, self.cash_counter))
		else:
			if not set(frappe.get_roles()) & CHANNEL_ROLES and not self.flags.api_client:  # the billing API checks its client's scopes
				raise frappe.PermissionError(_("Only an Accounts Officer records bank and channel payments."))
			if self.mode == "Cash":
				frappe.throw(_("Choose how the bank or channel payment was made."))
			self.channel_txn_id = (self.channel_txn_id or "").strip()
			if not self.channel_txn_id:
				frappe.throw(_("Enter the channel's transaction ID."))
			self.cash_counter = self.cashier_day = None
			if getdate(self.payment_date) > getdate(nowdate()):
				frappe.throw(_("The payment date can't be in the future."))
		if self.channel_txn_id:
			other = frappe.db.get_value("Payment Receipt", {"channel_txn_id": self.channel_txn_id, "name": ["!=", self.name]})
			if other:
				frappe.throw(_("Transaction ID {0} is already posted as receipt {1}.").format(self.channel_txn_id, other))
		if self.mode in INSTRUMENT_MODES and frappe.db.get_value("Consumer", frappe.db.get_value("Connection", self.connection, "consumer"), "cash_only"):
			frappe.throw(_("{0} pays by cash only after a dishonoured cheque. Take cash, card or a bank transfer.").format(self.consumer_name))
		if self.mode in INSTRUMENT_MODES and not (self.instrument_no or "").strip():
			frappe.throw(_("Enter the {0} number.").format(_(self.mode).lower()))

	def before_submit(self):
		from utility_billing.utility_billing import holders  # noqa: PLC0415

		holders.check_payment(self)  # a payment for a former occupant's balance (increment 22)
		if self.payment_channel == "Counter":
			status = frappe.db.get_value("Cashier Day", self.cashier_day, "status")
			if status != "Open":
				frappe.throw(_("Cashier day {0} is closed; no more receipts can be taken on it.").format(self.cashier_day))

	def on_submit(self):
		entry, deltas = account.post_payment(self)
		self.db_set({
			"alloc_current": -flt(deltas["current"]), "alloc_arrears": -flt(deltas["arrears"]),
			"alloc_surcharge": -flt(deltas["surcharge"]), "alloc_fees": -flt(deltas["fees"]),
			"alloc_credit": flt(deltas["credit"]), "balance_after": flt(entry.balance),
			"ledger_entry": entry.name, "status": "Posted",
		})
		self.update_day()
		if self.get("former_balance"):
			from utility_billing.utility_billing import holders  # noqa: PLC0415

			holders.refresh(self.former_balance)
		notify.payment_received(self)  # FS-PAY-09: the receipt notification

	def before_cancel(self):
		if not self.flags.reversal:
			frappe.throw(_("A posted receipt can't be cancelled. A Billing Manager reverses it with a reason."))

	def on_cancel(self):
		r = self.flags.reversal
		account.reverse_entry(self.ledger_entry, "Payment Receipt", self.name,
		                      remarks=_("Receipt {0} reversed: {1}").format(self.name, r["remarks"]))
		self.db_set({"status": "Reversed", "reversal_reason": r["reason_code"], "reversal_remarks": r["remarks"],
		             "reversed_by": frappe.session.user, "reversed_on": now_datetime()})
		self.update_day()
		if self.get("former_balance"):
			from utility_billing.utility_billing import holders  # noqa: PLC0415

			holders.refresh(self.former_balance)  # the reversed payment no longer counts against the former holder's balance
		notify.payment_received(self, reversed_=True)

	def update_day(self):
		if not self.cashier_day:
			return
		day = frappe.get_doc("Cashier Day", self.cashier_day)
		if day.status != "Open":
			return
		refresh_totals(day)
		frappe.db.set_value("Cashier Day", day.name, {
			f: day.get(f) for f in ("receipt_count", "cash_total", "cheque_total", "card_total", "total_collected",
			                        "reversed_count", "reversed_total", "expected_cash")
		}, update_modified=False)
