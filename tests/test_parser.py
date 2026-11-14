import re

import pandas as pd
import pytest

from agents.parser_agent import (CANONICAL_COLUMNS, ParseError, extract_merchant, mask_identifiers,
                                 parse_amount, parse_statement)


def test_sample_parses_to_canonical_schema(parsed, truth_rows):
    df, report = parsed
    assert list(df.columns) == CANONICAL_COLUMNS
    assert len(df) == len(truth_rows)
    assert report.rows_dropped == 0
    assert report.header_row == 4  # four banner lines above the table (blank lines ignored)
    assert report.mapping_source == "synonyms"
    assert set(df["type"]) == {"debit", "credit"}
    assert df["date"].min() == pd.Timestamp("2026-08-01")
    assert df["date"].max() == pd.Timestamp("2026-10-31") or df["date"].max() <= pd.Timestamp("2026-10-31")
    assert df["txn_id"].is_unique


def test_amounts_and_types_match_truth(parsed, truth_rows):
    df, _ = parsed
    for (_, r), t in zip(df.iterrows(), truth_rows):
        expected = t.debit if t.debit else t.credit
        assert r["amount"] == pytest.approx(expected)
        assert r["type"] == ("debit" if t.debit else "credit")
        assert r["date"].date() == t.day


def test_merchants_extracted(parsed, truth_rows):
    df, _ = parsed
    hits = sum(m == t.merchant for m, t in zip(df["merchant"], truth_rows))
    assert hits / len(df) >= 0.95


def test_identifiers_masked(parsed):
    df, _ = parsed
    text = " ".join(df["description"])
    assert "@" not in text
    assert not re.search(r"\d{9,}", text)
    assert "[UPI-ID]" in text and "[REF]" in text


def test_mask_identifiers_examples():
    s = "UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Order A/c No 50100123456789 card 4591XXXXXXXX3321"
    out = mask_identifiers(s)
    assert "412345678901" not in out and "swiggy@ybl" not in out and "50100123456789" not in out
    assert "3321" not in out


@pytest.mark.parametrize("raw,expected", [
    ("1,23,456.78", (123456.78, None)),
    ("₹500 Dr", (500.0, "debit")),
    ("2,000.00 CR", (2000.0, "credit")),
    ("(250.00)", (-250.0, None)),
    ("-99", (-99.0, None)),
    ("", (None, None)),
    ("-", (None, None)),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("narration,merchant", [
    ("UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Order", "SWIGGY"),
    ("UPI-ZOMATO-zomato@hdfcbank-HDFC0000001-123456789012-Dinner", "ZOMATO"),
    ("POS 4591XXXXXXXX3321 CROMA ELECTRONICS BANGALORE", "CROMA ELECTRONICS"),
    ("ATM WDL-MG ROAD BANGALORE-S1AN1234", "ATM"),
    ("NEFT CR-HDFC0001234-ACME TECHNOLOGIES PVT LTD-SALARY AUG 2026", "ACME TECHNOLOGIES PVT LTD"),
    ("ACH D- ZERODHA BROKING LTD-SIP123456", "ZERODHA BROKING LTD"),
])
def test_extract_merchant(narration, merchant):
    assert extract_merchant(narration) == merchant


def test_signed_amount_with_drcr_column():
    csv = (
        "Txn Date,Transaction Remarks,Amount,Dr/Cr,Balance\n"
        "01-08-2026,NEFT CR-HDFC0001234-ACME LTD-SALARY,\"85,000.00\",CR,\"1,85,000.00\"\n"
        "03-08-2026,UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Order,450.00,DR,\"1,84,550.00\"\n"
        "04-08-2026,UPI/DR/412345678902/UBER/AXIS/uber@axisbank/Ride,220.50,DR,\"1,84,329.50\"\n"
        "05-08-2026,UPI/DR/412345678903/BLINKIT/ICIC/blinkit@icici/Groceries,\"1,020.00\",DR,\"1,83,309.50\"\n"
        "06-08-2026,UPI/CR/412345678904/ROHAN DAS/HDFC/rohan@okhdfcbank/Split,500,CR,\"1,83,809.50\"\n"
    ).encode()
    df, report = parse_statement(csv, "x.csv")
    assert list(df["type"]) == ["credit", "debit", "debit", "debit", "credit"]
    assert df["amount"].tolist() == [85000.0, 450.0, 220.5, 1020.0, 500.0]
    assert df.loc[0, "date"] == pd.Timestamp("2026-08-01")
    assert report.column_mapping["type_indicator"] == "Dr/Cr"


def test_signed_amount_negative_means_debit():
    csv = (
        "Some Bank\nAccount 1234\n\n"
        "Date;Particulars;Amount (INR)\n"
        "02/09/2026;SWIGGY ORDER;-300\n03/09/2026;SALARY;50000\n04/09/2026;UBER RIDE;-150\n"
        "05/09/2026;ZOMATO;-275.5\n06/09/2026;DMART;-990\n"
    ).encode()
    df, report = parse_statement(csv, "y.csv")
    assert report.header_row == 2
    assert df["type"].tolist() == ["debit", "credit", "debit", "debit", "debit"]
    assert df.loc[0, "date"] == pd.Timestamp("2026-09-02")


def test_too_few_rows_raises():
    csv = b"Date,Narration,Debit,Credit\n01/08/2026,SWIGGY,100,\n"
    with pytest.raises(ParseError):
        parse_statement(csv, "z.csv")


def test_unknown_columns_without_llm_raises():
    csv = b"When,What,HowMuch\n01/08/2026,SWIGGY,100\n" * 6
    with pytest.raises(ParseError, match="columns"):
        parse_statement(csv, "z.csv")


def test_bad_rows_are_dropped_and_reported():
    rows = "Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
    rows += "".join(f"0{i}/08/2026,SWIGGY {i},100,\n" for i in range(1, 8))
    rows += "not a date,BROKEN,50,\n,EMPTY,,\n"
    df, report = parse_statement(rows.encode(), "d.csv")
    assert len(df) == 7
    assert report.rows_dropped == 2
