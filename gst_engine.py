"""
GST Complete Suite - Engine for GSTR-3B, GSTR-9, and ITC Auto-Matching.
"""
from difflib import SequenceMatcher

def calculate_gstr3b(invoices: list[dict], purchases: list[dict]) -> dict:
    """
    Calculates the net GST liability (Output Tax - ITC) for a given period.
    Assumes standard 18% GST for simplified testing purposes on all invoices.
    """
    total_sales = sum(float(i.get("amount", 0.0)) for i in invoices)
    
    # Calculate output tax (18% on B2B invoices)
    cgst_output = total_sales * 0.09
    sgst_output = total_sales * 0.09
    total_output_tax = cgst_output + sgst_output

    # Calculate Input Tax Credit (ITC) from purchases
    cgst_itc = sum(float(p.get("cgst", 0.0)) for p in purchases)
    sgst_itc = sum(float(p.get("sgst", 0.0)) for p in purchases)
    igst_itc = sum(float(p.get("igst", 0.0)) for p in purchases)
    total_itc = cgst_itc + sgst_itc + igst_itc

    net_gst_payable = max(0.0, total_output_tax - total_itc)
    
    return {
        "total_sales_inr": total_sales,
        "output_tax": {
            "cgst": cgst_output,
            "sgst": sgst_output,
            "total": total_output_tax
        },
        "input_tax_credit": {
            "cgst": cgst_itc,
            "sgst": sgst_itc,
            "igst": igst_itc,
            "total": total_itc
        },
        "net_gst_payable_inr": net_gst_payable,
        "status": "COMPLIANT" if total_sales > 0 else "NIL_RETURN"
    }

def generate_gstr9(invoices: list[dict], purchases: list[dict]) -> dict:
    """
    Compiles the GSTR-9 Annual Return data.
    """
    gstr3b_summary = calculate_gstr3b(invoices, purchases)
    
    return {
        "form_type": "GSTR-9 Annual Return",
        "financial_year": "2026-27",
        "part_ii_outward_supplies": gstr3b_summary["output_tax"],
        "part_iii_itc_availed": gstr3b_summary["input_tax_credit"],
        "part_iv_tax_paid": {
            "paid_through_cash": gstr3b_summary["net_gst_payable_inr"],
            "paid_through_itc": gstr3b_summary["input_tax_credit"]["total"]
        },
        "audit_requirement_9c": gstr3b_summary["total_sales_inr"] > 50000000.0  # ₹5 Crore limit for 9C
    }

def reconcile_itc(local_purchases: list[dict], gstr2b_entries: list[dict]) -> dict:
    """
    Matches local purchases with the government's GSTR-2B data to identify lost ITC.
    """
    matched = []
    unmatched_in_2b = [] # In 2B but not in books
    unmatched_in_books = [] # In books but not in 2B (Supplier defaulted!)
    
    # Track which 2B entries have been matched
    matched_2b_ids = set()
    
    total_itc_books = 0.0
    total_itc_2b = 0.0
    
    for purchase in local_purchases:
        p_amount = float(purchase.get("taxable_amount", 0.0))
        p_cgst = float(purchase.get("cgst", 0.0))
        p_sgst = float(purchase.get("sgst", 0.0))
        p_igst = float(purchase.get("igst", 0.0))
        p_gstin = purchase.get("vendor_gstin", "").upper()
        
        p_total_itc = p_cgst + p_sgst + p_igst
        total_itc_books += p_total_itc
        
        best_match = None
        best_score = 0.0
        
        for entry in gstr2b_entries:
            if entry.get("id") in matched_2b_ids:
                continue
                
            e_amount = float(entry.get("taxable_amount", 0.0))
            e_cgst = float(entry.get("cgst", 0.0))
            e_sgst = float(entry.get("sgst", 0.0))
            e_igst = float(entry.get("igst", 0.0))
            e_gstin = entry.get("vendor_gstin", "").upper()
            
            # Match criteria: GSTIN match + Amount within ₹100 tolerance
            if p_gstin and e_gstin and p_gstin == e_gstin:
                if abs(p_amount - e_amount) <= 100.0:
                    best_match = entry
                    best_score = 100.0
                    break
        
        if best_match:
            matched.append({"purchase": purchase, "gstr2b": best_match, "match_score": best_score})
            matched_2b_ids.add(best_match.get("id"))
            total_itc_2b += (float(best_match.get("cgst", 0.0)) + float(best_match.get("sgst", 0.0)) + float(best_match.get("igst", 0.0)))
        else:
            unmatched_in_books.append(purchase)
            
    for entry in gstr2b_entries:
        if entry.get("id") not in matched_2b_ids:
            unmatched_in_2b.append(entry)
            total_itc_2b += (float(entry.get("cgst", 0.0)) + float(entry.get("sgst", 0.0)) + float(entry.get("igst", 0.0)))
            
    itc_lost = sum(
        (float(p.get("cgst", 0.0)) + float(p.get("sgst", 0.0)) + float(p.get("igst", 0.0)))
        for p in unmatched_in_books
    )
    
    variance_report = f"ITC RECONCILIATION REPORT\n"
    variance_report += f"=========================\n"
    variance_report += f"Total Matches: {len(matched)}\n"
    variance_report += f"ITC Available in GSTR-2B: ₹{total_itc_2b:,.2f}\n"
    variance_report += f"ITC Claimed in Books: ₹{total_itc_books:,.2f}\n"
    
    if itc_lost > 0:
        variance_report += f"\n🚨 DANGER: ITC LOST DUE TO SUPPLIER DEFAULT: ₹{itc_lost:,.2f}\n"
        variance_report += f"{len(unmatched_in_books)} vendors have not filed their GSTR-1. You cannot claim this ITC.\n"
    else:
        variance_report += f"\n✅ ALL ITC SECURED. Suppliers are fully compliant.\n"
        
    return {
        "matched": matched,
        "unmatched_in_2b": unmatched_in_2b,
        "unmatched_in_books": unmatched_in_books,
        "total_itc_2b": total_itc_2b,
        "total_itc_books": total_itc_books,
        "itc_lost_inr": itc_lost,
        "variance_report": variance_report
    }
