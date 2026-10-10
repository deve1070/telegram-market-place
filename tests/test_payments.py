"""Unit tests for the verification decision logic and receipt parsers.
NOTE: the sample receipts below are SYNTHETIC (built from the field labels the open-source verifiers document),
not captured from live Telebirr/CBE. Re-check the parsers against real receipts before relying on auto-verify."""
from datetime import datetime
from decimal import Decimal

import pytest

from app.payments.base import Outcome, Receipt, extract_reference, PROVIDERS, FetchError
from app.payments.cbe import parse_cbe_text
from app.payments.matching import account_match, evaluate, name_match, parse_dt
from app.payments.telebirr import parse_telebirr_html

CREATED = datetime(2026, 10, 3, 9, 0, 0)  # naive UTC  (= 12:00 in Addis)
ACCT, HOLDER = "251911223344", "Abebe Kebede Tadesse"


def receipt(**kw):
    base = dict(provider="telebirr", reference="CE2513001XYT", amount=Decimal("1500"), success=True,
                receiver_name="Abebe Kebede Tadesse", receiver_account="2519****3344",
                paid_at=datetime(2026, 10, 3, 9, 30))
    base.update(kw)
    return Receipt(**base)


def ev(r, total="1500"):
    return evaluate(r, order_total=Decimal(total), order_created=CREATED, account_number=ACCT, holder_name=HOLDER)


def test_happy_path_verified():
    assert ev(receipt()).outcome == Outcome.VERIFIED


def test_overpay_ok_underpay_rejected():
    assert ev(receipt(amount=Decimal("1600"))).outcome == Outcome.VERIFIED
    r = ev(receipt(amount=Decimal("1400")))
    assert (r.outcome, r.reason) == (Outcome.REJECTED, "amount_low")


def test_wrong_account_rejected():
    r = ev(receipt(receiver_account="2519****9999"))
    assert (r.outcome, r.reason) == (Outcome.REJECTED, "wrong_receiver")


def test_masked_account_falls_back_to_name():
    assert ev(receipt(receiver_account="25****")).outcome == Outcome.VERIFIED
    assert ev(receipt(receiver_account=None, receiver_name="Abebe K*** T***")).outcome == Outcome.VERIFIED
    r = ev(receipt(receiver_account=None, receiver_name="Hanna Girma Alemu"))
    assert (r.outcome, r.reason) == (Outcome.REJECTED, "wrong_receiver")


def test_uncertain_receiver_goes_to_seller():
    r = ev(receipt(receiver_account=None, receiver_name=None))
    assert (r.outcome, r.reason) == (Outcome.UNAVAILABLE, "uncertain_receiver")


def test_old_receipt_rejected_but_swapped_date_tolerated():
    old = ev(receipt(paid_at=datetime(2026, 9, 1, 9, 0)))
    assert (old.outcome, old.reason) == (Outcome.REJECTED, "too_old")
    # parser unsure whether 05/10 is 5 Oct or 10 May: the alternative passes -> not rejected
    ok = ev(receipt(paid_at=datetime(2026, 5, 10, 9, 0), paid_at_alt=datetime(2026, 10, 3, 9, 30)))
    assert ok.outcome == Outcome.VERIFIED


def test_failed_transaction_rejected():
    assert ev(receipt(success=False)).reason == "not_successful"


def test_missing_time_is_uncertain_not_rejected():
    r = ev(receipt(paid_at=None))
    assert (r.outcome, r.reason) == (Outcome.UNAVAILABLE, "uncertain_time")


def test_helpers():
    assert account_match("1****5678", "1000123455678") is True
    assert account_match("0911223344", "251911223344") is True  # same phone, different prefix
    assert account_match("1****5679", "1000123455678") is False
    assert account_match("12", "1000123455678") is None
    assert name_match("Abebe K*** Tadesse", HOLDER) == "match"
    assert name_match("Hanna Girma", HOLDER) == "mismatch"
    assert name_match("Abebe", HOLDER) is None  # only one token: uncertain
    utc, alt = parse_dt("03-10-2026 12:30:00")  # Addis time -> UTC
    assert utc == datetime(2026, 10, 3, 9, 30) and alt == datetime(2026, 3, 10, 9, 30)
    utc, _ = parse_dt("10/3/2026, 12:30:00 PM")
    assert utc == datetime(2026, 10, 3, 9, 30)


def test_reference_extraction_and_formats():
    assert extract_reference("Transaction number is ce2513001xyt thanks", "telebirr") == "CE2513001XYT"
    assert extract_reference("0911223344", "telebirr") is None  # a phone number is not a reference
    assert extract_reference("ref FT253183LQF0 ok", "cbe") == "FT253183LQF0"
    assert extract_reference("hello", "cbe") is None
    assert PROVIDERS["cbe"].ref_re.match("FT253183LQF089873510")


TELEBIRR_HTML = """<html><body><table>
<tr><td>Payer Name</td><td>Hanna Girma</td></tr><tr><td>Credited Party name</td><td>Abebe Kebede Tadesse</td></tr>
<tr><td>Credited party account no</td><td>2519****3344</td></tr><tr><td>transaction status</td><td>Completed</td></tr>
<tr><td>Receipt No.</td><td>CE2513001XYT</td></tr><tr><td>Payment date</td><td>03-10-2026 12:30:00</td></tr>
<tr><td>Settled Amount</td><td>1,500.00 Birr</td></tr></table></body></html>"""


def test_telebirr_parser_end_to_end():
    r = parse_telebirr_html(TELEBIRR_HTML, "CE2513001XYT")
    assert r.amount == Decimal("1500.00") and r.success and r.receiver_name == "Abebe Kebede Tadesse"
    assert ev(r).outcome == Outcome.VERIFIED
    with pytest.raises(FetchError):
        parse_telebirr_html("<html>nothing here</html>", "CE2513001XYT")
    with pytest.raises(FetchError) as e:
        parse_telebirr_html(TELEBIRR_HTML, "ZZ9999999999")
    assert e.value.reason == "not_found"


CBE_TEXT = """Customer Transaction Details
Payer: HANNA GIRMA ALEMU
Account: 1****4321
Receiver: ABEBE KEBEDE TADESSE
Account: 1****3344
Reason: payment
Transferred Amount 1,500.00 ETB
Payment Date & Time 10/3/2026, 12:30:00 PM"""


def test_cbe_parser_end_to_end():
    r = parse_cbe_text(CBE_TEXT, "FT253183LQF0")
    assert r.amount == Decimal("1500.00") and r.receiver_account == "1****3344"
    assert ev(r).outcome == Outcome.VERIFIED
    with pytest.raises(FetchError):
        parse_cbe_text("garbage", "FT253183LQF0")
