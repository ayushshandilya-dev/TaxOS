"""
ais_engine.py — AIS Reconciliation Engine for TaxOS
====================================================

Solves **Brutal Truth #2: The Government Mirror**.

The Indian Income Tax Department's Annual Information Statement (AIS) already
knows what a freelancer earned — every TDS-deducting client reports payments.
Freelancers, however, file blind: they rely on their own books, which may be
incomplete.  The mismatch triggers scrutiny notices.

This module closes the loop by:

1. **Reconciling** invoice books against AIS entries via fuzzy name matching
   and amount-tolerance checks.
2. **Generating draft invoices** for AIS entries the freelancer never recorded.
3. **Producing a variance report** in plain English so the freelancer
   understands exactly what the government sees vs. what the books show.

All computation is offline — zero external API calls.  Only the Python
standard library is used (``difflib.SequenceMatcher`` for fuzzy matching).

Confidence & Risk Formulas
--------------------------
* **match_confidence** (per pair):
    ``0.4 * name_similarity  +  0.6 * amount_similarity``
    where *name_similarity* ∈ [0, 1] comes from ``SequenceMatcher.ratio()``
    and *amount_similarity* ∈ [0, 1] is ``1 - (|Δ| / max(ais, book, 1))``,
    clamped to [0, 1].

* **confidence_pct** (overall reconciliation quality):
    ``(matched_count / total_unique_entries) * 100``
    where *total_unique_entries* = matched + unmatched_ais + unmatched_books.

* **scrutiny_risk_pct**:
    A composite score in [0, 100] driven by three signals:
      - *variance_ratio* = ``|variance_inr| / max(total_ais, 1)``
      - *unmatched_ais_ratio* = ``unmatched_ais_count / max(total_entries, 1)``
      - *unmatched_books_ratio* = ``unmatched_books_count / max(total_entries, 1)``

    ``risk = clamp(40 * variance_ratio
                 + 40 * unmatched_ais_ratio
                 + 20 * unmatched_books_ratio, 0, 100)``

    Rationale: the IT Department cares most about income *it* sees but the
    taxpayer didn't declare (AIS unmatch & variance), and somewhat less about
    income the taxpayer declared but that has no AIS backing.
"""

from __future__ import annotations

import datetime
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AMOUNT_TOLERANCE_PCT: float = 0.05      # 5 %
_AMOUNT_TOLERANCE_ABS: float = 1_000.0   # ₹1 000
_NAME_MATCH_THRESHOLD: float = 0.45      # minimum SequenceMatcher ratio
_CONFIDENCE_NAME_WEIGHT: float = 0.4
_CONFIDENCE_AMT_WEIGHT: float = 0.6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lower-case, strip whitespace for comparison."""
    return text.strip().lower()


def _name_similarity(ais_source: str, invoice_vendor: str) -> float:
    """Return a similarity score ∈ [0, 1] between two entity names.

    Two checks are performed and the *maximum* is returned:

    1. **Substring containment** — if one normalised string is wholly
       contained in the other the score is 0.95 (not 1.0, to leave room
       for an exact match via SequenceMatcher).
    2. **SequenceMatcher ratio** — Levenshtein-style similarity from
       ``difflib``.

    Parameters
    ----------
    ais_source : str
        The ``source_name`` field from the AIS entry.
    invoice_vendor : str
        The ``vendor`` field from the freelancer's invoice.

    Returns
    -------
    float
        Similarity score between 0 and 1.
    """
    a = _normalise(ais_source)
    b = _normalise(invoice_vendor)

    if not a or not b:
        return 0.0

    # Exact match fast-path
    if a == b:
        return 1.0

    # Substring containment
    substring_score = 0.95 if (a in b or b in a) else 0.0

    # SequenceMatcher (Ratcliff/Obershelp)
    seq_score = SequenceMatcher(None, a, b).ratio()

    return max(substring_score, seq_score)


def _amounts_within_tolerance(ais_amount: float, book_amount: float) -> bool:
    """Check whether two amounts are 'close enough' to be the same payment.

    A match is accepted when **either** condition is true:

    * The absolute difference is ≤ ``_AMOUNT_TOLERANCE_ABS`` (₹1 000).
    * The absolute difference is ≤ ``_AMOUNT_TOLERANCE_PCT`` (5 %) of the
      larger of the two amounts.

    Parameters
    ----------
    ais_amount : float
        Amount reported in the AIS entry.
    book_amount : float
        Amount from the freelancer's invoice.

    Returns
    -------
    bool
    """
    diff = abs(ais_amount - book_amount)
    if diff <= _AMOUNT_TOLERANCE_ABS:
        return True
    max_amt = max(abs(ais_amount), abs(book_amount), 1.0)
    return diff <= _AMOUNT_TOLERANCE_PCT * max_amt


def _amount_similarity(ais_amount: float, book_amount: float) -> float:
    """Return a similarity score ∈ [0, 1] for two monetary amounts.

    ``1.0`` means identical; approaches ``0.0`` as divergence grows.

    Formula::

        1 - |Δ| / max(|ais|, |book|, 1)

    clamped to ``[0, 1]``.
    """
    diff = abs(ais_amount - book_amount)
    base = max(abs(ais_amount), abs(book_amount), 1.0)
    return max(0.0, 1.0 - diff / base)


def _match_confidence(name_sim: float, amt_sim: float) -> float:
    """Weighted confidence score for a single matched pair.

    ``0.4 * name_similarity + 0.6 * amount_similarity``

    The amount dimension is weighted higher because two different vendors
    can share a name prefix, but an amount match within tolerance is a
    strong corroborating signal.
    """
    return round(
        _CONFIDENCE_NAME_WEIGHT * name_sim + _CONFIDENCE_AMT_WEIGHT * amt_sim,
        4,
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the interval [lo, hi]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile(invoices: list[dict], ais_entries: list[dict]) -> dict:
    """Reconcile freelancer invoices against AIS entries.

    Performs fuzzy matching between ``ais_entry['source_name']`` and
    ``invoice['vendor']`` combined with an amount-tolerance check to
    pair each AIS record with the most likely invoice.

    Matching algorithm (greedy, best-first)
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    1. Compute the cross-product of all ``(ais_entry, invoice)`` pairs.
    2. For each pair, calculate ``name_similarity`` and check
       ``_amounts_within_tolerance``.  Pairs where the name similarity
       is below ``_NAME_MATCH_THRESHOLD`` **or** the amounts are outside
       tolerance are discarded.
    3. Remaining candidates are sorted by descending ``match_confidence``.
    4. Pairs are accepted greedily: once an AIS entry or invoice is
       consumed by a match, it is unavailable for further pairing.

    Parameters
    ----------
    invoices : list[dict]
        Each dict must contain at minimum::

            {
                "id": <any>,
                "vendor": str,
                "amount": float,
                "date": str,           # ISO-8601 or any string
                "payment_state": str,
            }

    ais_entries : list[dict]
        Each dict must contain at minimum::

            {
                "source_name": str,
                "amount": float,
                "tds_deducted": float,
                "section": str,        # e.g. "194J", "194C"
            }

    Returns
    -------
    dict
        Keys:

        * ``matched`` – list of ``{ais_entry, invoice, match_confidence}``
        * ``unmatched_in_ais`` – AIS entries with **no** matching invoice.
          *The government sees income the freelancer didn't record.*
        * ``unmatched_in_books`` – invoices with **no** matching AIS entry.
          *The freelancer invoiced but the government doesn't see it
          (TDS may not have been deducted).*
        * ``total_ais`` – sum of AIS amounts (₹).
        * ``total_books`` – sum of invoice amounts (₹).
        * ``variance_inr`` – ``total_ais - total_books`` (₹).
        * ``confidence_pct`` – overall reconciliation confidence (0–100).
        * ``scrutiny_risk_pct`` – estimated scrutiny risk (0–100).
    """

    # ------------------------------------------------------------------
    # 1.  Build scored candidate pairs
    # ------------------------------------------------------------------
    candidates: list[tuple[float, float, float, int, int]] = []
    # Each element: (confidence, name_sim, amt_sim, ais_idx, inv_idx)

    for ai, ais in enumerate(ais_entries):
        for ii, inv in enumerate(invoices):
            name_sim = _name_similarity(ais["source_name"], inv["vendor"])
            if name_sim < _NAME_MATCH_THRESHOLD:
                continue
            if not _amounts_within_tolerance(ais["amount"], inv["amount"]):
                continue
            amt_sim = _amount_similarity(ais["amount"], inv["amount"])
            conf = _match_confidence(name_sim, amt_sim)
            candidates.append((conf, name_sim, amt_sim, ai, ii))

    # ------------------------------------------------------------------
    # 2.  Greedy best-first assignment
    # ------------------------------------------------------------------
    candidates.sort(key=lambda c: c[0], reverse=True)

    matched_ais_indices: set[int] = set()
    matched_inv_indices: set[int] = set()
    matched: list[dict[str, Any]] = []

    for conf, _ns, _as, ai, ii in candidates:
        if ai in matched_ais_indices or ii in matched_inv_indices:
            continue
        matched.append(
            {
                "ais_entry": ais_entries[ai],
                "invoice": invoices[ii],
                "match_confidence": conf,
            }
        )
        matched_ais_indices.add(ai)
        matched_inv_indices.add(ii)

    # ------------------------------------------------------------------
    # 3.  Partition unmatched
    # ------------------------------------------------------------------
    unmatched_in_ais = [
        ais_entries[i]
        for i in range(len(ais_entries))
        if i not in matched_ais_indices
    ]
    unmatched_in_books = [
        invoices[i]
        for i in range(len(invoices))
        if i not in matched_inv_indices
    ]

    # ------------------------------------------------------------------
    # 4.  Aggregate totals & risk metrics
    # ------------------------------------------------------------------
    total_ais = sum(e["amount"] for e in ais_entries)
    total_books = sum(inv["amount"] for inv in invoices)
    variance_inr = round(total_ais - total_books, 2)

    total_entries = len(matched) + len(unmatched_in_ais) + len(unmatched_in_books)
    confidence_pct = round(
        (len(matched) / max(total_entries, 1)) * 100, 2
    )

    # Scrutiny risk composite
    variance_ratio = abs(variance_inr) / max(total_ais, 1.0)
    unmatched_ais_ratio = len(unmatched_in_ais) / max(total_entries, 1)
    unmatched_books_ratio = len(unmatched_in_books) / max(total_entries, 1)

    scrutiny_risk_pct = round(
        _clamp(
            40.0 * variance_ratio
            + 40.0 * unmatched_ais_ratio
            + 20.0 * unmatched_books_ratio,
            0.0,
            100.0,
        ),
        2,
    )

    return {
        "matched": matched,
        "unmatched_in_ais": unmatched_in_ais,
        "unmatched_in_books": unmatched_in_books,
        "total_ais": round(total_ais, 2),
        "total_books": round(total_books, 2),
        "variance_inr": variance_inr,
        "confidence_pct": confidence_pct,
        "scrutiny_risk_pct": scrutiny_risk_pct,
    }


def generate_draft_invoices(unmatched_ais: list[dict]) -> list[dict]:
    """Create draft invoice payloads for AIS entries that lack a book match.

    These drafts serve as *suggestions* — the freelancer reviews and
    confirms them before they are committed to the invoice ledger.

    Parameters
    ----------
    unmatched_ais : list[dict]
        Typically the ``unmatched_in_ais`` list returned by
        :func:`reconcile`.  Each dict must have ``source_name`` and
        ``amount`` keys.

    Returns
    -------
    list[dict]
        One draft per unmatched entry::

            {
                "vendor": <source_name>,
                "amount": <amount>,
                "date": "<today ISO-8601>",
                "payment_state": "DRAFT",
                "source": "AIS_RECONCILIATION",
            }
    """
    today_iso = datetime.date.today().isoformat()
    drafts: list[dict] = []

    for entry in unmatched_ais:
        drafts.append(
            {
                "vendor": entry["source_name"],
                "amount": entry["amount"],
                "date": today_iso,
                "payment_state": "DRAFT",
                "source": "AIS_RECONCILIATION",
            }
        )

    return drafts


def generate_variance_report(reconciliation_result: dict) -> str:
    """Produce a plain-English narrative explaining the reconciliation.

    The report is designed for a non-technical freelancer audience and
    covers:

    * How many entries matched and the overall confidence.
    * Government's view of income vs. the freelancer's books.
    * Each unmatched AIS entry (with a suggestion to create a draft).
    * Each unmatched book entry (possible TDS non-deduction).
    * A final scrutiny-risk assessment.

    Parameters
    ----------
    reconciliation_result : dict
        The dict returned by :func:`reconcile`.

    Returns
    -------
    str
        Multi-line plain-English report.
    """
    r = reconciliation_result
    matched = r["matched"]
    unmatched_ais = r["unmatched_in_ais"]
    unmatched_books = r["unmatched_in_books"]
    total_ais = r["total_ais"]
    total_books = r["total_books"]
    variance = r["variance_inr"]
    confidence = r["confidence_pct"]
    risk = r["scrutiny_risk_pct"]

    lines: list[str] = []

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    lines.append("=" * 68)
    lines.append("  TaxOS -- AIS RECONCILIATION REPORT")
    lines.append("=" * 68)
    lines.append("")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_entries = len(matched) + len(unmatched_ais) + len(unmatched_books)
    lines.append(f"Total AIS entries examined       : {len(matched) + len(unmatched_ais)}")
    lines.append(f"Total invoices in books          : {len(matched) + len(unmatched_books)}")
    lines.append(f"Successfully matched             : {len(matched)}")
    lines.append(f"Unmatched in AIS (govt sees)     : {len(unmatched_ais)}")
    lines.append(f"Unmatched in books (you invoiced): {len(unmatched_books)}")
    lines.append("")

    # ------------------------------------------------------------------
    # Financials
    # ------------------------------------------------------------------
    lines.append("-" * 68)
    lines.append("  FINANCIAL SUMMARY")
    lines.append("-" * 68)
    lines.append(f"Government (AIS) total income    : INR {total_ais:>14,.2f}")
    lines.append(f"Your books total income          : INR {total_books:>14,.2f}")
    lines.append(f"Variance (AIS - Books)           : INR {variance:>14,.2f}")
    lines.append("")

    if variance > 0:
        lines.append(
            "[!] The government thinks you earned MORE than your books show."
        )
        lines.append(
            "   This is the most common trigger for a scrutiny notice."
        )
    elif variance < 0:
        lines.append(
            "[i] Your books show MORE income than the AIS.  This is unusual"
        )
        lines.append(
            "   but generally safe -- you are reporting more, not less."
        )
    else:
        lines.append(
            "[OK] Perfect match -- your books and the government's records agree."
        )
    lines.append("")

    # ------------------------------------------------------------------
    # Matched entries
    # ------------------------------------------------------------------
    if matched:
        lines.append("-" * 68)
        lines.append("  MATCHED ENTRIES")
        lines.append("-" * 68)
        for i, m in enumerate(matched, 1):
            ais = m["ais_entry"]
            inv = m["invoice"]
            conf = m["match_confidence"]
            lines.append(
                f"  {i}. {ais['source_name']!r} <-> Invoice #{inv['id']}"
                f"  |  AIS INR {ais['amount']:,.2f}"
                f"  vs  Book INR {inv['amount']:,.2f}"
                f"  |  Confidence: {conf:.1%}"
            )
        lines.append("")

    # ------------------------------------------------------------------
    # Unmatched in AIS — CRITICAL
    # ------------------------------------------------------------------
    if unmatched_ais:
        lines.append("-" * 68)
        lines.append("  [!] UNMATCHED IN AIS -- GOVERNMENT SEES INCOME YOU DIDN'T INVOICE")
        lines.append("-" * 68)
        for i, entry in enumerate(unmatched_ais, 1):
            lines.append(
                f"  {i}. Source : {entry['source_name']}"
            )
            lines.append(
                f"     Amount : INR {entry['amount']:,.2f}"
                f"  |  TDS: INR {entry['tds_deducted']:,.2f}"
                f"  (Section {entry['section']})"
            )
            lines.append(
                "     -> ACTION: Create a DRAFT invoice for this entry and"
                " verify with the payer."
            )
            lines.append("")
    else:
        lines.append("  [OK] No unmatched AIS entries -- your books cover"
                      " everything the government sees.")
        lines.append("")

    # ------------------------------------------------------------------
    # Unmatched in books
    # ------------------------------------------------------------------
    if unmatched_books:
        lines.append("-" * 68)
        lines.append("  [i] UNMATCHED IN BOOKS -- YOU INVOICED BUT GOVERNMENT DOESN'T SEE")
        lines.append("-" * 68)
        for i, inv in enumerate(unmatched_books, 1):
            lines.append(
                f"  {i}. Invoice #{inv['id']}  |  Vendor: {inv['vendor']}"
                f"  |  INR {inv['amount']:,.2f}"
            )
            lines.append(
                "     -> NOTE: TDS may not have been deducted by this payer,"
                " or the AIS hasn't updated yet."
            )
            lines.append("")
    else:
        lines.append("  [OK] All your invoices have corresponding AIS entries.")
        lines.append("")

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------
    lines.append("=" * 68)
    lines.append("  RISK ASSESSMENT")
    lines.append("=" * 68)
    lines.append(f"  Reconciliation confidence : {confidence:.1f}%")
    lines.append(f"  Estimated scrutiny risk   : {risk:.1f}%")
    lines.append("")

    if risk <= 15:
        lines.append("  [OK] LOW RISK -- Your filing is well-supported by AIS data.")
    elif risk <= 40:
        lines.append(
            "  [!] MODERATE RISK -- Some discrepancies exist.  Review the"
            " unmatched entries above and reconcile before filing."
        )
    elif risk <= 70:
        lines.append(
            "  [!] HIGH RISK -- Significant gaps between your books and the"
            " government's records.  Immediate reconciliation recommended."
        )
    else:
        lines.append(
            "  [!!] VERY HIGH RISK -- Major variance detected.  Consult a CA"
            " and reconcile all entries before filing your ITR."
        )

    lines.append("")
    lines.append("=" * 68)
    lines.append(
        "  Report generated by TaxOS AIS Engine on "
        f"{datetime.date.today().isoformat()}"
    )
    lines.append("=" * 68)

    return "\n".join(lines)
