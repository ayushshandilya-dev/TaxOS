import database
import tax_rules
import ais_engine
import classifier_engine
from typing import List, Dict, Any

def generate_firm_compliance_matrix() -> Dict[str, Any]:
    """
    Generates practice-wide metrics for CA firms managing multiple taxpayer clients.
    """
    taxpayers = database.get_taxpayers()
    staff = database.get_staff_members()
    notices = database.get_notices()
    
    total_clients = len(taxpayers)
    compliant_count = sum(1 for t in taxpayers if t["status"] == "COMPLIANT")
    ais_discrepancy_count = sum(1 for t in taxpayers if t["status"] == "AIS_DISCREPANCY")
    notice_pending_count = sum(1 for t in taxpayers if t["status"] == "NOTICE_PENDING")
    advance_tax_due_count = sum(1 for t in taxpayers if t["status"] == "ADVANCE_TAX_DUE")
    
    # Entity breakdown
    entities = {}
    for t in taxpayers:
        etype = t["entity_type"]
        entities[etype] = entities.get(etype, 0) + 1
        
    return {
        "total_taxpayers": total_clients,
        "compliant_count": compliant_count,
        "ais_discrepancy_count": ais_discrepancy_count,
        "notice_pending_count": notice_pending_count,
        "advance_tax_due_count": advance_tax_due_count,
        "staff_count": len(staff),
        "total_notices_logged": len(notices),
        "entity_breakdown": entities,
        "taxpayers": taxpayers
    }

def bulk_ais_reconcile(ais_data_batch: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Processes AIS records for multiple taxpayers simultaneously.
    Each item in ais_data_batch should be:
    {
        "taxpayer_id": 1,
        "ais_entries": [
            {"source_name": "Google Adsense", "amount": 500000.0, "tds_deducted": 5000.0, "section": "194J"},
            ...
        ]
    }
    """
    results = []
    total_flagged = 0
    
    for client_data in ais_data_batch:
        taxpayer_id = client_data.get("taxpayer_id")
        taxpayer = database.get_taxpayer_by_id(taxpayer_id)
        if not taxpayer:
            continue
            
        entries = client_data.get("ais_entries", [])
        
        # Temporary AIS load & reconcile
        database.clear_ais_entries()
        for e in entries:
            database.add_ais_entry(
                source_name=e.get("source_name", "Unknown Source"),
                amount=float(e.get("amount", 0.0)),
                tds_deducted=float(e.get("tds_deducted", 0.0)),
                section=e.get("section", "TDS")
            )
            
        recon_report = ais_engine.reconcile()
        variances = recon_report.get("variances", [])
        has_anomaly = len(variances) > 0 or recon_report.get("has_anomaly", False)
        
        status = "AIS_DISCREPANCY" if has_anomaly else "COMPLIANT"
        database.update_taxpayer_status(taxpayer_id, status)
        
        if has_anomaly:
            total_flagged += 1
            
        results.append({
            "taxpayer_id": taxpayer_id,
            "taxpayer_name": taxpayer["name"],
            "pan": taxpayer["pan"],
            "has_anomaly": has_anomaly,
            "net_variance": recon_report.get("net_variance", 0.0),
            "variances": variances,
            "status_updated": status
        })
        
    return {
        "processed_count": len(results),
        "discrepancy_count": total_flagged,
        "results": results
    }

def batch_advance_tax_calculator(fy_label: str = "FY 2026-27") -> List[Dict[str, Any]]:
    """
    Calculates quarterly advance tax schedule across all active clients in the CA practice.
    Statutory deadlines:
    - June 15: 15%
    - Sept 15: 45%
    - Dec 15: 75%
    - March 15: 100%
    """
    taxpayers = database.get_taxpayers()
    invoices = database.get_invoices(include_inactive=False)
    
    # Calculate estimated revenue per client
    client_revenues = {}
    for inv in invoices:
        cid = inv["client_id"]
        client_revenues[cid] = client_revenues.get(cid, 0.0) + inv["amount"]
        
    schedule_report = []
    for t in taxpayers:
        # Default revenue mapping for simulation
        est_gross = client_revenues.get(t["id"], 1800000.0)
        
        # 44ADA Presumptive profit = 50% of gross
        tax_res = tax_rules.calculate_44ada_tax(est_gross)
        net_tax = tax_res["new_regime_tax"]
        
        q1_due = net_tax * 0.15
        q2_due = net_tax * 0.45
        q3_due = net_tax * 0.75
        q4_due = net_tax * 1.00
        
        schedule_report.append({
            "taxpayer_id": t["id"],
            "taxpayer_name": t["name"],
            "pan": t["pan"],
            "entity_type": t["entity_type"],
            "est_gross_revenue": est_gross,
            "net_annual_tax": net_tax,
            "preferred_regime": tax_res["preferred_regime"],
            "installments": {
                "June_15_Q1": round(q1_due, 2),
                "Sept_15_Q2": round(q2_due, 2),
                "Dec_15_Q3": round(q3_due, 2),
                "March_15_Q4": round(q4_due, 2)
            }
        })
        
    return schedule_report

def generate_ca_audit_package(taxpayer_id: int) -> Dict[str, Any]:
    """
    Compiles a comprehensive CA pre-audit & defense package for a client.
    """
    taxpayer = database.get_taxpayer_by_id(taxpayer_id)
    if not taxpayer:
        return {"error": "Taxpayer not found"}
        
    notices = database.get_notices(taxpayer_id)
    defense_brief = classifier_engine.generate_defense_brief()
    
    latest_merkle = database.get_latest_merkle_root()
    merkle_proof = latest_merkle.get("root_hash") if latest_merkle else "0xGENESIS_SECURE_MERKLE_ROOT"
    
    return {
        "taxpayer_id": taxpayer_id,
        "taxpayer_name": taxpayer["name"],
        "pan": taxpayer["pan"],
        "gstin": taxpayer.get("gstin"),
        "entity_type": taxpayer["entity_type"],
        "compliance_status": taxpayer["status"],
        "assigned_staff": taxpayer["assigned_staff"],
        "notices": notices,
        "defense_brief": defense_brief,
        "merkle_audit_proof": merkle_proof,
        "audit_readiness_score": 98.5 if taxpayer["status"] == "COMPLIANT" else 72.0
    }
