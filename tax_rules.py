import datetime
from statistics import mean

TOTAL_44ADA_LIMIT = 7_500_000   # ₹75 Lakhs presumptive tax cash ceiling
TOTAL_GST_LIMIT = 2_000_000     # ₹20 Lakhs rolling GST threshold (normal states)
GST_AMBER_LIMIT = TOTAL_GST_LIMIT * 0.8  # ₹16 Lakhs (80% warning threshold)

def check_anomaly(vendor: str, amount: float, active_invoices: list) -> bool:
    """
    Keep a rolling average per vendor. If a new invoice is more than 2x
    that vendor's average (with at least 2 past active invoices), flag it.
    """
    # Only average active (non-superseded) invoices for the vendor
    past = [i["amount"] for i in active_invoices if i["vendor"].lower() == vendor.lower()]
    if len(past) >= 2:
        return amount > 2 * mean(past)
    return False

def calculate_new_regime_tax(taxable_income: float) -> float:
    """
    Calculates estimated income tax liability under the New Tax Regime (FY 2026-27 / FY 2025-26).
    Rebate under Section 87A makes tax zero if taxable income <= ₹7,00,000.
    """
    if taxable_income <= 700000:
        return 0.0
    
    tax = 0.0
    remaining = taxable_income
    
    # New Tax Regime Slabs (FY 2026-27):
    # 0 - 3L: 0%
    # 3L - 6L: 5%
    # 6L - 9L: 10%
    # 9L - 12L: 15%
    # 12L - 15L: 20%
    # Above 15L: 30%
    
    if remaining > 1500000:
        tax += (remaining - 1500000) * 0.30
        remaining = 1500000
    if remaining > 1200000:
        tax += (remaining - 1200000) * 0.20
        remaining = 1200000
    if remaining > 900000:
        tax += (remaining - 900000) * 0.15
        remaining = 900000
    if remaining > 600000:
        tax += (remaining - 600000) * 0.10
        remaining = 600000
    if remaining > 300000:
        tax += (remaining - 300000) * 0.05
        
    # Add 4% Health and Education Cess
    return tax * 1.04

def calculate_old_regime_tax(taxable_income: float, deductions: float = 150000.0) -> float:
    """
    Calculates estimated income tax liability under the Old Tax Regime.
    Applies standard ₹1.5L Chapter VI-A deductions (e.g. PPF, ELSS, Insurance) for comparison.
    """
    net_taxable = max(0.0, taxable_income - deductions)
    if net_taxable <= 500000:
        return 0.0  # 87A rebate applies to net income <= 5L
        
    tax = 0.0
    remaining = net_taxable
    
    # Old Tax Regime Slabs:
    # 0 - 2.5L: 0%
    # 2.5L - 5L: 5%
    # 5L - 10L: 20%
    # Above 10L: 30%
    
    if remaining > 1000000:
        tax += (remaining - 1000000) * 0.30
        remaining = 1000000
    if remaining > 500000:
        tax += (remaining - 500000) * 0.20
        remaining = 500000
    if remaining > 250000:
        tax += (remaining - 250000) * 0.05
        
    return tax * 1.04

def calculate_44ada_tax(total_receipts: float) -> dict:
    """
    Calculates 44ADA presumptive taxation metrics for active income.
    Compares New vs. Old Tax Regime dynamically.
    """
    presumptive_income = total_receipts * 0.5
    new_regime_tax = calculate_new_regime_tax(presumptive_income)
    old_regime_tax = calculate_old_regime_tax(presumptive_income)
    
    limit_exceeded = total_receipts > TOTAL_44ADA_LIMIT
    
    return {
        "presumptive_income": presumptive_income,
        "new_regime_tax": new_regime_tax,
        "old_regime_tax": old_regime_tax,
        "preferred_regime": "New Tax Regime" if new_regime_tax <= old_regime_tax else "Old Tax Regime",
        "tax_savings": abs(new_regime_tax - old_regime_tax),
        "limit_exceeded": limit_exceeded
    }

def get_upcoming_advance_tax(estimated_tax: float, current_date=None) -> dict:
    """
    Determines the next advance tax payment deadline and cumulative amount due.
    """
    if current_date is None:
        current_date = datetime.date.today()
    elif isinstance(current_date, str):
        current_date = datetime.datetime.strptime(current_date.split("T")[0], "%Y-%m-%d").date()
        
    year = current_date.year
    
    if current_date.month <= 3:
        fy_start_year = year - 1
    else:
        fy_start_year = year
        
    deadlines = [
        {"date": datetime.date(fy_start_year, 6, 15), "percent": 0.15, "label": "Q1 (June 15)"},
        {"date": datetime.date(fy_start_year, 9, 15), "percent": 0.45, "label": "Q2 (Sept 15)"},
        {"date": datetime.date(fy_start_year, 12, 15), "percent": 0.75, "label": "Q3 (Dec 15)"},
        {"date": datetime.date(fy_start_year + 1, 3, 15), "percent": 1.00, "label": "Q4 (March 15)"}
    ]
    
    upcoming = None
    for dl in deadlines:
        if dl["date"] >= current_date:
            upcoming = dl
            break
            
    if upcoming is None:
        upcoming = {"date": datetime.date(fy_start_year + 1, 6, 15), "percent": 0.15, "label": "Q1 (June 15)"}
        
    return {
        "installment": upcoming["label"],
        "due_date": upcoming["date"].strftime("%d-%b-%Y"),
        "percent": int(upcoming["percent"] * 100),
        "cumulative_due": estimated_tax * upcoming["percent"]
    }

def calculate_accrual_split(invoices: list, cash_balance: float = 200000.0) -> dict:
    """
    THE ACCRUAL TAX TRAP SOLVER (Brutal Truth #1).
    
    Splits total 44ADA tax liability into:
    - Green Zone: Tax due on invoices already PAID (cash in hand)
    - Red Zone: Tax due on UNPAID invoices (accrual liability — money not yet received)
    
    If the Red Zone tax exceeds available cash balance, calculates:
    - How much the freelancer needs to BORROW to pay advance tax
    - Interest cost of that borrowing at 18% p.a.
    - Suggests an early payment discount to the largest unpaid client
    """
    paid = [i for i in invoices if i.get("payment_state") == "PAID"]
    unpaid = [i for i in invoices if i.get("payment_state") != "PAID"]
    
    paid_total = sum(i["amount"] for i in paid)
    unpaid_total = sum(i["amount"] for i in unpaid)
    total = paid_total + unpaid_total
    
    # Calculate 44ADA presumptive tax on each zone
    total_tax_info = calculate_44ada_tax(total)
    preferred_tax = min(total_tax_info["new_regime_tax"], total_tax_info["old_regime_tax"])
    
    # Proportional tax split based on revenue contribution
    paid_ratio = paid_total / total if total > 0 else 0.0
    unpaid_ratio = unpaid_total / total if total > 0 else 0.0
    
    green_zone_tax = round(preferred_tax * paid_ratio, 2)
    red_zone_tax = round(preferred_tax * unpaid_ratio, 2)
    
    # Borrowed tax exposure: how much of your advance tax bill is on money you haven't received
    borrowed_amount = max(0.0, red_zone_tax - max(0.0, cash_balance - green_zone_tax))
    interest_cost_annual = 0.18  # 18% p.a. typical personal loan rate
    # Assume borrowing for 90 days (one advance tax quarter)
    interest_cost = round(borrowed_amount * interest_cost_annual * (90 / 365), 2)
    
    # Early payment discount suggestion for the largest unpaid invoice
    early_payment_suggestion = None
    if unpaid and borrowed_amount > 0:
        largest_unpaid = max(unpaid, key=lambda x: x["amount"])
        # 2% discount is cheaper than 18% interest on borrowing
        discount_pct = 2.0
        discount_amount = round(largest_unpaid["amount"] * discount_pct / 100, 2)
        early_payment_suggestion = {
            "client": largest_unpaid["vendor"],
            "invoice_id": largest_unpaid["id"],
            "invoice_amount": largest_unpaid["amount"],
            "suggested_discount_pct": discount_pct,
            "discount_amount": discount_amount,
            "interest_saved": interest_cost,
            "net_benefit": round(interest_cost - discount_amount, 2),
            "recommendation": f"Offer {largest_unpaid['vendor']} a {discount_pct}% early payment discount (₹{discount_amount:,.0f}) to avoid borrowing ₹{borrowed_amount:,.0f} at 18% interest (cost: ₹{interest_cost:,.0f})."
        }
    
    return {
        "paid_total": paid_total,
        "unpaid_total": unpaid_total,
        "green_zone_tax": green_zone_tax,
        "red_zone_tax": red_zone_tax,
        "total_tax": round(preferred_tax, 2),
        "paid_invoice_count": len(paid),
        "unpaid_invoice_count": len(unpaid),
        "borrowed_amount": borrowed_amount,
        "interest_cost": interest_cost,
        "needs_borrowing": borrowed_amount > 0,
        "early_payment_suggestion": early_payment_suggestion
    }

