"""
TDS/TCS Complete Suite - Engine for ITA 2025 Section 393 Compliance.
"""
import datetime

def verify_tds_rate(nature_of_payment: str) -> dict:
    """
    Returns the correct TDS rate under the consolidated Income Tax Act 2025 (Section 393).
    """
    nature = nature_of_payment.lower()
    
    # ITA 2025 Section 393 consolidated rates mapped
    if "salary" in nature:
        return {"section": "392", "rate_pct": 0.0, "notes": "Slab rates apply"}
    elif "professional" in nature or "technical" in nature or "consulting" in nature:
        return {"section": "393(1)(a)", "rate_pct": 10.0, "notes": "Fee for professional or technical services"}
    elif "contractor" in nature or "sub-contractor" in nature:
        return {"section": "393(1)(b)", "rate_pct": 2.0, "notes": "Payments to contractors (1% for individuals)"}
    elif "rent" in nature:
        return {"section": "393(1)(c)", "rate_pct": 10.0, "notes": "Rent for land/building (2% for machinery)"}
    elif "commission" in nature or "brokerage" in nature:
        return {"section": "393(1)(d)", "rate_pct": 5.0, "notes": "Commission or brokerage"}
    elif "interest" in nature:
        return {"section": "393(1)(e)", "rate_pct": 10.0, "notes": "Interest other than securities"}
    else:
        return {"section": "393(1)(z)", "rate_pct": 10.0, "notes": "Default rate for unclassified payments"}

def generate_form16a(tds_records: list[dict]) -> dict:
    """
    Generates a consolidated Form 16A (TDS Certificate) schema for a client.
    """
    if not tds_records:
        return {"error": "No TDS records found for this client."}
        
    total_paid = sum(float(r.get("amount", 0.0)) for r in tds_records)
    
    # Calculate TDS amount based on rate (assuming rate is a percentage)
    total_tds_deducted = sum(float(r.get("amount", 0.0)) * (float(r.get("rate", 0.0)) / 100.0) for r in tds_records)
    
    return {
        "form_type": "FORM NO. 16A",
        "certificate_under": "Section 400 of the Income Tax Act, 2025",
        "financial_year": "2026-27",
        "date_of_issue": datetime.date.today().strftime("%d-%b-%Y"),
        "summary": {
            "total_amount_paid": total_paid,
            "total_tds_deducted": total_tds_deducted,
            "total_tds_deposited": total_tds_deducted
        },
        "transactions": tds_records,
        "verification": "Digitally Signed by TaxOS Edge Coordinator"
    }
