"""
Transfer Pricing & Corporate Suite - Engine for DTAA, Form 3CEB, and Arm's Length Pricing.
"""
import datetime
import statistics

def simulate_arms_length_range(transactions: list[dict]) -> dict:
    """
    Calculates the 35th to 65th percentile range (Arm's Length Range) for a set of comparable transactions,
    as required by Indian Transfer Pricing regulations.
    """
    if not transactions:
        return {"error": "No comparable transactions provided."}
        
    prices = [float(tx.get("amount", 0.0)) for tx in transactions]
    prices.sort()
    
    n = len(prices)
    if n < 6:
        # If less than 6 comparables, arithmetic mean is used
        mean_price = statistics.mean(prices)
        return {
            "method": "Arithmetic Mean (Dataset < 6)",
            "arms_length_price": mean_price,
            "tolerance_allowed_pct": 3.0,
            "comparables_used": n
        }
    
    # 35th and 65th percentile calculation
    p35_idx = int(round(n * 0.35)) - 1
    p65_idx = int(round(n * 0.65)) - 1
    median_idx = int(round(n * 0.50)) - 1
    
    p35_idx = max(0, min(p35_idx, n - 1))
    p65_idx = max(0, min(p65_idx, n - 1))
    median_idx = max(0, min(median_idx, n - 1))
    
    return {
        "method": "Percentile Range (35th - 65th)",
        "range_start": prices[p35_idx],
        "median": prices[median_idx],
        "range_end": prices[p65_idx],
        "comparables_used": n
    }

def generate_form3ceb(client_name: str, tx_summary: dict) -> dict:
    """
    Drafts the Form 3CEB Accountant's Report for International Transactions.
    """
    return {
        "form": "Form 3CEB",
        "description": "Report from an accountant to be furnished under section 92E relating to international transaction(s)",
        "client_name": client_name,
        "financial_year": "2026-27",
        "transaction_summary": tx_summary,
        "declaration": "We have examined the accounts and records of the assessee relating to international transactions. The particulars given in the Annexure are true and correct.",
        "status": "DRAFT_READY_FOR_SIGNATURE"
    }

def check_dtaa_compliance(country_code: str, payment_nature: str) -> dict:
    """
    Verifies DTAA (Double Taxation Avoidance Agreement) compliance requirements like TRC and Form 10F.
    """
    requirements = [
        "Valid Tax Residency Certificate (TRC) from the foreign government.",
        "Form 10F (self-declaration) electronically filed.",
        "No Permanent Establishment (PE) declaration."
    ]
    
    # Form 41 is a new requirement often cited in ITA 2025 proposals for specific treaty benefits
    if country_code in ["SG", "MU", "AE", "NL"]:
        requirements.append("New Form 41 (Enhanced Disclosure for Treaty Shopping Prone Jurisdictions) must be filed.")
        
    return {
        "country_code": country_code,
        "payment_nature": payment_nature,
        "dtaa_requirements": requirements,
        "risk_level": "HIGH" if country_code in ["SG", "MU", "AE", "NL"] else "MEDIUM"
    }
