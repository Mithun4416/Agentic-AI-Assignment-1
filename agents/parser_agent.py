"""Parser Agent: turn an uploaded CSV/PDF statement into the canonical transaction schema.

Deterministic first: header detection, a synonym table for column mapping, and
regex-based normalisation. The LLM is called only when the synonym table cannot
map a required column, and then it only sees the header row and three sample rows
(already masked).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

CANONICAL_COLUMNS = [
    "txn_id", "date", "description", "merchant", "amount", "type", "balance",
    "category", "category_source",
]

# --------------------------------------------------------------------------- #
# Column synonyms (normalised header text -> canonical field)
# --------------------------------------------------------------------------- #
SYNONYMS: dict[str, list[str]] = {
    "date": ["date", "txn date", "transaction date", "tran date", "posting date", "value date",
             "value dt", "trans date", "date of transaction"],
    "description": ["narration", "particulars", "description", "transaction remarks", "remarks",
                    "details", "transaction details", "transaction description", "narrative"],
    "debit": ["withdrawal amt", "withdrawal", "withdrawals", "debit", "debit amount", "dr",
              "withdrawal amount", "debits", "paid out", "money out"],
    "credit": ["deposit amt", "deposit", "deposits", "credit", "credit amount", "cr",
               "deposit amount", "credits", "paid in", "money in"],
    "amount": ["amount", "txn amount", "transaction amount", "amt"],
    "type_indicator": ["dr/cr", "cr/dr", "type", "txn type", "transaction type", "debit/credit"],
    "balance": ["closing balance", "balance", "available balance", "running balance"],
}
# value date is a weaker match than the main date column
_DATE_PRIORITY = ["date", "txn date", "transaction date", "tran date", "posting date", "trans date",
                  "date of transaction", "value date", "value dt"]

CITY_TOKENS = {"BANGALORE", "BENGALURU", "MUMBAI", "DELHI", "PUNE", "CHENNAI", "HYDERABAD",
               "KOLKATA", "NOIDA", "GURGAON", "IN", "IND", "INDIA"}


class ParseError(ValueError):
    """Raised with a user-facing message when a statement cannot be parsed."""


class ColumnMapping(BaseModel):
    """LLM output: which source column holds each canonical field (exact names or null)."""
    date: Optional[str] = Field(None, description="Column holding the transaction date")
    description: Optional[str] = Field(None, description="Column holding the narration/remarks")
    debit: Optional[str] = Field(None, description="Column with withdrawal/debit amounts, if separate")
    credit: Optional[str] = Field(None, description="Column with deposit/credit amounts, if separate")
    amount: Optional[str] = Field(None, description="Single signed amount column, if no separate debit/credit")
    type_indicator: Optional[str] = Field(None, description="Column saying DR/CR or Debit/Credit, if any")
    balance: Optional[str] = Field(None, description="Running balance column, if any")


@dataclass
class ParseReport:
    rows_read: int = 0
    rows_valid: int = 0
    rows_dropped: int = 0
    drop_reasons: Counter = field(default_factory=Counter)
    header_row: int = 0
    column_mapping: dict = field(default_factory=dict)
    mapping_source: str = "synonyms"
    warnings: list[str] = field(default_factory=list)
    prompt_trace: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "rows_valid": self.rows_valid,
            "rows_dropped": self.rows_dropped,
            "drop_reasons": dict(self.drop_reasons),
            "header_row": self.header_row,
            "column_mapping": self.column_mapping,
            "mapping_source": self.mapping_source,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Masking and merchant cleaning
# --------------------------------------------------------------------------- #
_UPI_ID = re.compile(r"[\w.\-]+@[a-zA-Z][\w]*")
_CARD = re.compile(r"\b\d{4}[Xx*]{4,}\d{4}\b")
_LONG_DIGITS = re.compile(r"\b\d{9,18}\b")
_ACCT_HINT = re.compile(r"(?i)\b(a/c|acct|account)\s*(no\.?|number)?\s*[:\-]?\s*[\dXx*]{6,}")


def mask_identifiers(text: str) -> str:
    """Replace UPI IDs, card numbers, account numbers and reference numbers with placeholders."""
    if not isinstance(text, str):
        return text
    text = _UPI_ID.sub("[UPI-ID]", text)
    text = _CARD.sub("[CARD]", text)
    text = _ACCT_HINT.sub("[ACCOUNT]", text)
    text = _LONG_DIGITS.sub("[REF]", text)
    return text


def _clean_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9&.' ]+", " ", name).upper()
    tokens = [t for t in name.split() if t]
    while len(tokens) > 1 and tokens[-1] in CITY_TOKENS:
        tokens.pop()
    return " ".join(tokens).strip(" .")


def extract_merchant(narration: str) -> str:
    """Pull a clean merchant/payee name out of a raw bank narration."""
    s = (narration or "").strip()
    up = s.upper()

    if up.startswith("UPI/"):
        parts = [p.strip() for p in s.split("/")]
        # UPI/DR/<ref>/<NAME>/... or UPI/<ref>/<NAME>/...
        for p in parts[1:]:
            if p.upper() in {"DR", "CR", "P2A", "P2M"} or p.isdigit() or not p:
                continue
            return _clean_name(p)
    if up.startswith("UPI-"):
        parts = [p.strip() for p in s.split("-")]
        if len(parts) > 1:
            return _clean_name(parts[1])
    if up.startswith("ATM") or "ATM WDL" in up or "CASH WDL" in up:
        return "ATM"
    m = re.match(r"(?i)POS\s+\S+\s+(.+)", s)
    if m:
        return _clean_name(m.group(1))
    m = re.match(r"(?i)N?ACH\s*D?\s*-\s*([^-]+)", s)
    if m:
        return _clean_name(m.group(1))
    if re.match(r"(?i)(NEFT|IMPS|RTGS)", s):
        parts = [p.strip() for p in re.split(r"[-/]", s) if p.strip()]
        for p in parts[1:]:
            if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", p.upper()) or p.isdigit() or p.upper() in {"CR", "DR"}:
                continue
            return _clean_name(p)
    words = re.findall(r"[A-Za-z][A-Za-z&.']+", s)
    return _clean_name(" ".join(words[:3])) or "UNKNOWN"


# --------------------------------------------------------------------------- #
# Number and date normalisation
# --------------------------------------------------------------------------- #
def parse_amount(value) -> tuple[Optional[float], Optional[str]]:
    """Parse '1,23,456.78', '₹500 Dr', '(250.00)', '-99'. Returns (signed value, 'debit'|'credit'|None)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, None
    s = str(value).strip()
    if not s or s in {"-", "--", "nan", "NaN", "None"}:
        return None, None
    hint = None
    m = re.search(r"(?i)\s*\b(dr|cr)\.?\s*$", s)
    if m:
        hint = "debit" if m.group(1).lower() == "dr" else "credit"
        s = s[: m.start()]
    negative = (s.startswith("(") and s.endswith(")")) or s.lstrip("₹Rs. ").startswith("-")
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits.count(".") > 1:
        return None, None
    try:
        val = float(digits)
    except ValueError:
        return None, None
    return (-val if negative else val), hint


def _norm_header(h) -> str:
    h = str(h or "").lower()
    h = re.sub(r"\((inr|rs\.?|₹)\)", "", h)
    h = h.replace(".", " ").replace("_", " ")
    return re.sub(r"\s+", " ", h).strip()


def _match_field(header: str) -> Optional[str]:
    n = _norm_header(header)
    for fld, names in SYNONYMS.items():
        if n in names:
            return fld
    return None


# --------------------------------------------------------------------------- #
# Reading raw rows
# --------------------------------------------------------------------------- #
def _rows_from_csv(data: bytes) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    # Banner lines above the table confuse csv.Sniffer, so pick the delimiter whose
    # parse yields the best-scoring header row.
    best_rows, best_score = None, -1
    for delim in (",", ";", "\t", "|"):
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
        idx = _detect_header([r for r in rows if any(c.strip() for c in r)])
        score = len({_match_field(c) for c in rows_nonempty(rows)[idx]} - {None}) if idx >= 0 else 0
        if score > best_score:
            best_rows, best_score = rows, score
    return best_rows


def rows_nonempty(rows: list[list[str]]) -> list[list[str]]:
    return [r for r in rows if any(str(c).strip() for c in r)]


def _rows_from_pdf(data: bytes, password: Optional[str]) -> list[list[str]]:
    import pdfplumber

    rows: list[list[str]] = []
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or None) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for r in table:
                        rows.append([("" if c is None else str(c).replace("\n", " ")) for c in r])
    except Exception as exc:  # wrong password, encrypted, corrupt
        msg = str(exc) or exc.__class__.__name__
        if "password" in msg.lower() or "encrypt" in msg.lower() or exc.__class__.__name__ == "PDFPasswordIncorrect":
            raise ParseError("This PDF is password-protected. Enter the correct password in the sidebar.") from exc
        raise ParseError(f"Could not read the PDF: {msg}") from exc
    if not rows:
        raise ParseError("No tables found in the PDF. Scanned/image PDFs are not supported (they need OCR).")
    return rows


def _detect_header(rows: list[list[str]], scan: int = 40) -> int:
    best_idx, best_score = -1, 0
    for i, row in enumerate(rows[:scan]):
        fields = {_match_field(c) for c in row} - {None}
        score = len(fields)
        if "date" in fields and score >= 2 and score > best_score:
            best_idx, best_score = i, score
            if score >= 3:
                break
    return best_idx


def _frame_from_rows(rows: list[list[str]]) -> tuple[pd.DataFrame, int]:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise ParseError("The file is empty.")
    idx = _detect_header(rows)
    header_idx = idx if idx >= 0 else 0
    header = [str(c).strip() or f"col_{j}" for j, c in enumerate(rows[header_idx])]
    body = []
    for r in rows[header_idx + 1:]:
        if [str(c).strip() for c in r] == header:  # repeated header on a new PDF page
            continue
        r = list(r) + [""] * (len(header) - len(r))
        body.append(r[: len(header)])
    df = pd.DataFrame(body, columns=header)
    return df, header_idx


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #
def _synonym_mapping(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    norm = {c: _norm_header(c) for c in columns}
    # date: prefer the transaction date over value date
    for pref in _DATE_PRIORITY:
        hit = [c for c, n in norm.items() if n == pref]
        if hit:
            mapping["date"] = hit[0]
            break
    for c in columns:
        fld = _match_field(c)
        if fld and fld != "date" and fld not in mapping:
            mapping[fld] = c
    return mapping


def _mapping_complete(m: dict) -> bool:
    return "date" in m and "description" in m and ("debit" in m or "credit" in m or "amount" in m)


def _llm_mapping(df: pd.DataFrame, llm, report: ParseReport) -> dict[str, str]:
    from prompts.prompts import PARSER_PROMPT, prompt_to_trace

    sample = df.head(3).map(lambda v: mask_identifiers(str(v))).to_csv(index=False)
    variables = {"columns": ", ".join(map(str, df.columns)), "sample_rows": sample}
    report.prompt_trace = prompt_to_trace("parser_column_mapping", PARSER_PROMPT, variables)
    chain = PARSER_PROMPT | llm.with_structured_output(ColumnMapping)
    result: ColumnMapping = chain.invoke(variables)
    cols = set(map(str, df.columns))
    return {k: v for k, v in result.model_dump().items() if v and v in cols}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def _txn_id(d, desc, amount, row) -> str:
    raw = f"{d}|{desc}|{amount:.2f}|{row}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def parse_statement(data: bytes, filename: str = "statement.csv", llm=None,
                    password: Optional[str] = None, min_rows: int = 5) -> tuple[pd.DataFrame, ParseReport]:
    """Parse raw file bytes into the canonical schema. Raises ParseError with a clear message."""
    report = ParseReport()
    is_pdf = filename.lower().endswith(".pdf") or data[:4] == b"%PDF"
    rows = _rows_from_pdf(data, password) if is_pdf else _rows_from_csv(data)
    raw, report.header_row = _frame_from_rows(rows)

    mapping = _synonym_mapping(list(raw.columns))
    if not _mapping_complete(mapping):
        if llm is None:
            raise ParseError(
                "Could not recognise the statement's columns "
                f"(found: {', '.join(map(str, raw.columns))}). Configure an LLM to map unusual layouts."
            )
        try:
            mapping = _llm_mapping(raw, llm, report)
            report.mapping_source = "llm"
        except Exception as exc:
            raise ParseError(f"Column mapping with the LLM failed: {exc}") from exc
        if not _mapping_complete(mapping):
            raise ParseError(f"Could not map the required columns. Mapping found: {mapping}")
    report.column_mapping = mapping
    report.rows_read = len(raw)

    records = []
    for i, r in raw.iterrows():
        date = pd.to_datetime(str(r[mapping["date"]]).strip(), dayfirst=True, errors="coerce")
        desc_raw = str(r[mapping["description"]]).strip()
        if pd.isna(date):
            report.drop_reasons["missing or invalid date"] += 1
            continue
        if not desc_raw or desc_raw.lower() == "nan":
            report.drop_reasons["missing description"] += 1
            continue

        amount, ttype = None, None
        if "debit" in mapping or "credit" in mapping:
            d_val, _ = parse_amount(r[mapping["debit"]]) if "debit" in mapping else (None, None)
            c_val, _ = parse_amount(r[mapping["credit"]]) if "credit" in mapping else (None, None)
            if d_val:
                amount, ttype = abs(d_val), "debit"
            elif c_val:
                amount, ttype = abs(c_val), "credit"
        if amount is None and "amount" in mapping:
            val, hint = parse_amount(r[mapping["amount"]])
            if val:
                indicator = str(r[mapping["type_indicator"]]).strip().lower() if "type_indicator" in mapping else ""
                if indicator.startswith(("dr", "debit", "d")) and indicator:
                    ttype = "debit"
                elif indicator.startswith(("cr", "credit", "c")) and indicator:
                    ttype = "credit"
                else:
                    ttype = hint or ("debit" if val < 0 else "credit")
                amount = abs(val)
        if not amount:
            report.drop_reasons["missing amount"] += 1
            continue

        balance = None
        if "balance" in mapping:
            b, hint = parse_amount(r[mapping["balance"]])
            balance = (-abs(b) if hint == "debit" else b) if b is not None else None

        merchant = extract_merchant(desc_raw)
        records.append({
            "txn_id": _txn_id(date.date(), desc_raw, amount, i),
            "date": date.normalize(),
            "description": mask_identifiers(desc_raw),
            "merchant": mask_identifiers(merchant),
            "amount": round(float(amount), 2),
            "type": ttype,
            "balance": balance,
            "category": None,
            "category_source": None,
        })

    report.rows_valid = len(records)
    report.rows_dropped = report.rows_read - report.rows_valid
    if report.rows_valid < min_rows:
        raise ParseError(
            f"Only {report.rows_valid} valid transactions found (need at least {min_rows}). "
            f"Dropped rows: {dict(report.drop_reasons) or 'none'}."
        )
    df = pd.DataFrame.from_records(records, columns=CANONICAL_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    if df["txn_id"].duplicated().any():
        report.warnings.append("Duplicate transaction IDs found; they were made unique.")
        df["txn_id"] = df["txn_id"] + "_" + df.groupby("txn_id").cumcount().astype(str)
    return df, report
