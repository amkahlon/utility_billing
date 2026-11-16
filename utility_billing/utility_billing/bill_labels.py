# Copyright (c) 2026, SoftVerx. All rights reserved. See license.txt.
"""The bill's labels in a second language (FS-LOC, gap 1.3), printed beside the English when the society chooses Urdu or Arabic.
Drafted by the developer: **have a native speaker review them before a client goes live**; a society's own notices and payment
instructions in the second language are entered on the Society."""

LABELS = {
	"Urdu": {
		"Consumer copy": "صارف کی کاپی", "Bill No": "بل نمبر", "Consumer": "صارف", "Name": "نام", "Property": "پراپرٹی", "Category": "زمرہ",
		"Meter No": "میٹر نمبر", "Mobile": "موبائل", "Reference No": "ریفرنس نمبر", "Bill month": "بل کا مہینہ", "Issue date": "تاریخ اجراء",
		"Due date": "آخری تاریخ", "Previous reading": "پچھلی ریڈنگ", "Present reading": "موجودہ ریڈنگ", "Multiplier": "ملٹی پلائر",
		"Units": "یونٹس", "Reading": "ریڈنگ", "Charges": "چارجز", "Rate": "ریٹ", "Amount (Rs)": "رقم (روپے)", "Current bill": "موجودہ بل",
		"Arrears": "بقایا جات", "Payable within due date": "آخری تاریخ تک قابل ادائیگی", "Payable after due date": "آخری تاریخ کے بعد قابل ادائیگی", "Late payment surcharge": "تاخیری ادائیگی سرچارج", "Meter photo": "میٹر کی تصویر",
		"Month": "مہینہ", "Bill": "بل", "Paid on": "ادائیگی کی تاریخ", "Paid": "ادا شدہ", "Notices": "اطلاعات", "How to pay": "ادائیگی کا طریقہ",
		"Office copy": "دفتری کاپی", "Within due date": "آخری تاریخ تک", "After due date": "آخری تاریخ کے بعد",
		"Bank / cashier stamp": "بینک / کیشیئر کی مہر",
	},
	"Arabic": {
		"Consumer copy": "نسخة المشترك", "Bill No": "رقم الفاتورة", "Consumer": "المشترك", "Name": "الاسم", "Property": "العقار", "Category": "الفئة",
		"Meter No": "رقم العداد", "Mobile": "الجوال", "Reference No": "الرقم المرجعي", "Bill month": "شهر الفاتورة", "Issue date": "تاريخ الإصدار",
		"Due date": "تاريخ الاستحقاق", "Previous reading": "القراءة السابقة", "Present reading": "القراءة الحالية", "Multiplier": "المضاعف",
		"Units": "الوحدات", "Reading": "القراءة", "Charges": "الرسوم", "Rate": "السعر", "Amount (Rs)": "المبلغ", "Current bill": "الفاتورة الحالية",
		"Arrears": "المتأخرات", "Payable within due date": "المستحق قبل تاريخ الاستحقاق", "Payable after due date": "المستحق بعد تاريخ الاستحقاق", "Late payment surcharge": "رسوم التأخر في السداد", "Meter photo": "صورة العداد",
		"Month": "الشهر", "Bill": "الفاتورة", "Paid on": "تاريخ الدفع", "Paid": "المدفوع", "Notices": "إشعارات", "How to pay": "طريقة الدفع",
		"Office copy": "نسخة المكتب", "Within due date": "قبل تاريخ الاستحقاق", "After due date": "بعد تاريخ الاستحقاق",
		"Bank / cashier stamp": "ختم البنك / أمين الصندوق",
	},
}
LANG_CODE = {"Urdu": "ur", "Arabic": "ar"}


def second(label: str, language: str | None) -> str | None:
	return LABELS.get(language or "", {}).get(label)
