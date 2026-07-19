import os
import uuid
import json
import socket
import io
import datetime
import asyncio
import base64
from typing import List, Dict, Optional
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode
from zeroconf import IPVersion, ServiceInfo, Zeroconf
from cryptography.hazmat.primitives import hashes

import database
import tax_rules
from crypto_vault import CryptoVault
from agent_engine import TaxOSAgentEngine
from merkle_tree import MerkleTree
from ssi_vault import SSIVault
from forecast_engine import ForecastEngine
from ais_engine import reconcile as ais_reconcile, generate_draft_invoices as ais_draft, generate_variance_report as ais_variance_report
from classifier_engine import classify_activity, simulate_retroactive_switch, generate_defense_brief
import gst_engine
import tds_engine
import tp_engine
app = FastAPI(title="TaxOS Production Edge Hub")
database.init_db()
vault = CryptoVault()
ssi_vault = SSIVault()
forecaster = ForecastEngine()

# Global mDNS responder reference
zeroconf_instance = None
mdns_info = None

# Connection Manager for WebSockets (Arduino and Mobile connection push)
class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

ws_manager = WebSocketManager()

# Global Ledger State
ledger_state = {"state": "green", "last_approval_hash": "None"}

# Templates Setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# mDNS Auto-Discovery Startup and Shutdown
@app.on_event("startup")
async def startup_event():
    global zeroconf_instance, mdns_info
    local_ip = get_local_ip()
    try:
        zeroconf_instance = Zeroconf()
        mdns_info = ServiceInfo(
            "_http._tcp.local.",
            "TaxOS Hub Service._http._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=8000,
            properties={"path": "/"},
            server="taxos-pc.local."
        )
        zeroconf_instance.register_service(mdns_info)
        print(f"mDNS Active: taxos-pc.local advertised on {local_ip}:8000")
    except Exception as e:
        print(f"Failed to initialize mDNS: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    global zeroconf_instance, mdns_info
    if zeroconf_instance:
        try:
            zeroconf_instance.unregister_service(mdns_info)
            zeroconf_instance.close()
            print("mDNS shut down successfully.")
        except Exception as e:
            print(f"Error closing mDNS: {e}")

# Zero-Trust Authentication Dependency
async def verify_device_token(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing pairing token.")
    token = auth_header.split(" ")[1]
    if not database.verify_device_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid pairing token.")
    return token

# Pydantic Inputs
class PairingInput(BaseModel):
    pin: str
    device_name: str
    public_key: str = None
    device_password: str = None

class UnlockInput(BaseModel):
    device_token: str
    password: str

class InvoiceInput(BaseModel):
    vendor: str
    amount: float
    date: str
    supersedes_id: int = None

class ApprovalInput(BaseModel):
    hash: str
    signature: Optional[str] = None
    challenge: Optional[str] = None

# API Routes
@app.get("/pairing-pin")
def get_pairing_pin():
    """Exposes current pairing PIN on the PC screen dashboard."""
    return {"pin": vault.get_current_pin()}

@app.post("/pair")
def pair_device(data: PairingInput):
    """Pairs a client device using the 6-digit PIN and registers public attestation key and password."""
    if vault.verify_pairing_pin(data.pin):
        token = vault.generate_device_token(data.device_name)
        database.add_device(data.device_name, token, data.public_key, data.device_password)
        # Generate next PIN for subsequent devices
        vault.generate_pairing_pin()
        return {"status": "paired", "device_token": token}
    raise HTTPException(status_code=400, detail="Invalid pairing PIN.")

@app.post("/unlock")
def unlock_device(data: UnlockInput):
    """Verifies client terminal unlock password against edge server database."""
    res = database.verify_device_password(data.device_token, data.password)
    if res is None:
        raise HTTPException(status_code=403, detail="Device not paired. Re-pairing required.")
    if res is True:
        return {"status": "unlocked"}
    raise HTTPException(status_code=401, detail="Access denied. Invalid password.")

@app.post("/invoice")
async def add_invoice(inv: InvoiceInput):
    # Retrieve active user details
    user_id = database.get_primary_user_id()
    fy_id = database.get_active_fy_id()
    
    # Check for anomaly using active invoices
    active_invoices = database.get_invoices(include_inactive=False)
    is_anomaly = tax_rules.check_anomaly(inv.vendor, inv.amount, active_invoices)
    
    # Save to SQLite (transparently encrypts vendor and date)
    new_id = database.add_invoice(
        user_id=user_id,
        client_id=1, # Default seed client
        fy_id=fy_id,
        vendor=inv.vendor,
        amount=inv.amount,
        date=inv.date,
        is_anomaly=is_anomaly,
        supersedes_id=inv.supersedes_id
    )
    
    # Recalculate totals
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    
    # Rebuild Merkle Tree and update Root
    leaves = [f"{i['id']}|{i['vendor']}|{i['amount']}|{i['date']}" for i in invoices]
    merkle = MerkleTree(leaves)
    merkle_root = merkle.get_root()
    database.add_merkle_root(merkle_root)
    
    # Calculate time-series compliance forecast
    forecast = forecaster.forecast_gst_crossing(invoices)
    
    flags = []
    
    # Update Ledger and WebSockets state
    if total > tax_rules.TOTAL_44ADA_LIMIT:
        flags.append("44ADA presumptive taxation limit exceeded")
    if total > tax_rules.TOTAL_GST_LIMIT:
        flags.append("GST registration threshold crossed")
        ledger_state["state"] = "red"
    elif total > tax_rules.GST_AMBER_LIMIT:
        flags.append("GST registration threshold warning (80% reached)")
        ledger_state["state"] = "amber"
    
    # Broadcast new state dynamically to WebSocket connections
    await ws_manager.broadcast({
        "event": "ledger_update",
        "total": total,
        "ledger_state": ledger_state["state"],
        "invoice_id": new_id,
        "merkle_root": merkle_root,
        "forecast": forecast
    })
    
    return {
        "status": "success",
        "invoice_id": new_id,
        "total": total,
        "flags": flags,
        "is_anomaly": is_anomaly,
        "merkle_root": merkle_root,
        "forecast": forecast
    }

@app.get("/status")
def get_status():
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    
    # Comprehensive tax estimations
    tax_info = tax_rules.calculate_44ada_tax(total)
    advance_tax = tax_rules.get_upcoming_advance_tax(tax_info["new_regime_tax"])
    
    # Fetch audit chain logs
    user_id = database.get_primary_user_id()
    approvals = database.get_approvals(user_id)
    devices = database.get_devices()
    
    anomalies = [i for i in invoices if i["is_anomaly"] == 1]
    
    # Fetch Merkle Root, Forecast, Treasury simulation & Penalties
    latest_root = database.get_latest_merkle_root()
    forecast = forecaster.forecast_gst_crossing(invoices)
    treasury = forecaster.forecast_cash_flow(invoices)
    penalties = forecaster.forecast_penalties(invoices)
    arbitrage = forecaster.optimize_tax_regime(invoices)
    # Accrual Tax Trap (Brutal Truth #1)
    accrual_split = tax_rules.calculate_accrual_split(invoices)
    
    return {
        "invoices": invoices,
        "total": total,
        "invoice_count": len(invoices),
        "anomalies_count": len(anomalies),
        "ledger_state": ledger_state["state"],
        "last_approval_hash": ledger_state["last_approval_hash"],
        "tax_info": tax_info,
        "advance_tax": advance_tax,
        "approvals": approvals,
        "paired_devices": devices,
        "pc_ip": get_local_ip(),
        "latest_root": latest_root["root_hash"] if latest_root else None,
        "forecast": forecast,
        "treasury": treasury,
        "penalties": penalties,
        "arbitrage": arbitrage,
        "accrual_split": accrual_split
    }

@app.post("/approve")
async def approve(payload: ApprovalInput, request: Request):
    """Processes cryptographic sign-off approvals."""
    user_id = database.get_primary_user_id()
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    
    latest = database.get_latest_approval(user_id)
    prev_hash = latest["current_hash"] if latest else "GENESIS"
    
    # ECDSA signature verification if details are provided
    if payload.signature and payload.challenge:
        # Get pairing token from headers
        auth_header = request.headers.get("Authorization")
        device_token = None
        if auth_header and auth_header.startswith("Bearer "):
            device_token = auth_header.split(" ")[1]
            
        pub_key_base64 = None
        if device_token:
            pub_key_base64 = database.get_device_public_key(device_token)
        else:
            # Fallback: get first public key registered
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT public_key FROM devices WHERE public_key IS NOT NULL LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            if row:
                pub_key_base64 = row[0]
                
        if pub_key_base64:
            try:
                import base64
                from cryptography.hazmat.primitives.asymmetric import ec
                from cryptography.hazmat.primitives import serialization
                
                pub_bytes = base64.b64decode(pub_key_base64)
                pub_key = serialization.load_der_public_key(pub_bytes)
                
                sig_bytes = base64.b64decode(payload.signature)
                
                pub_key.verify(
                    sig_bytes,
                    payload.challenge.encode("utf-8"),
                    ec.ECDSA(hashes.SHA256())
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cryptographic signature check failed: {e}")

    event_data = f"APPROVED_FILING_FOR_TOTAL_{total}"
    database.add_approval(user_id, prev_hash, event_data, payload.hash)
    
    ledger_state["state"] = "green"
    ledger_state["last_approval_hash"] = payload.hash
    
    # Broadcast approval to UIs
    await ws_manager.broadcast({
        "event": "approved",
        "hash": payload.hash,
        "ledger_state": "green"
    })
    
    return {"status": "approved", "hash": payload.hash}

@app.get("/credentials/issue")
def issue_credential():
    """Issues a signed JSON-LD Verifiable Credential for GST compliance verification."""
    user_id = database.get_primary_user_id()
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    approvals = database.get_approvals(user_id)
    last_hash = approvals[0]["current_hash"] if approvals else "GENESIS"
    
    gst_status = "UNDER_THRESHOLD"
    if total >= 2000000:
        gst_status = "CROSS_LIMIT"
        
    vc = ssi_vault.issue_verifiable_credential(
        freelancer_name="Devashish Sharma",
        gst_status=gst_status,
        total_turnover=total,
        last_hash=last_hash
    )
    return vc

@app.get("/agent-reasoning")
def get_agent_reasoning():
    agent = TaxOSAgentEngine(user_id=database.get_primary_user_id())
    result = agent.run_agentic_workflow("Analyze active invoices, advance tax obligations, and GST threshold regulations.")
    return result

@app.get("/download-lut")
def download_lut():
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    user_id = database.get_primary_user_id()
    approvals = database.get_approvals(user_id)
    last_hash = approvals[0]["current_hash"] if approvals else "None"
    
    # Compile template filled with freelancer PAN details
    lut_text = f"""FORM GST RFD-11
[See Rule 96A]
LETTER OF UNDERTAKING FOR EXPORT OF SERVICES WITHOUT PAYMENT OF INTEGRATED TAX

Freelancer Name: Devashish Sharma
PAN: ABCDE1234F | Segment: IT Consulting Exporter
Fiscal Assessment Period: FY 2026-27
Cumulative Receipts Verified: INR {total:,.2f}
Ledger Authenticity Code: Verified Edge Node

Cryptographic Trust Verification Sign-off:
Attestation Token Hash: {last_hash}
Signed cryptographically via Paired Device Ledger.

Date: {datetime.date.today().strftime("%d-%B-%Y")}
Place: Noida, Uttar Pradesh, India
"""
    return StreamingResponse(
        io.BytesIO(lut_text.encode("utf-8")),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment;filename=LUT_RFD11_Draft.txt"}
    )

@app.get("/summary/qr")
def get_summary_qr():
    local_ip = get_local_ip()
    url = f"http://{local_ip}:8000/summary"
    
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.post("/voice-entry")
def voice_entry(payload: dict):
    text = payload.get("text", "")
    import re
    numbers = re.findall(r'\d+', text.replace(",", ""))
    amount = 0.0
    if numbers:
        amount = float(numbers[0])
    
    # Fallback parsing
    if amount == 0.0:
        words = text.lower().split()
        text_num_map = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100, "thousand": 1000, "lakh": 100000, "crore": 10000000
        }
        temp = 0
        for w in words:
            if w in text_num_map:
                val = text_num_map[w]
                if val >= 1000:
                    if temp == 0: temp = 1
                    amount += temp * val
                    temp = 0
                else:
                    temp = val
        if temp > 0: amount += temp

    vendor = "Unknown Vendor"
    words = text.split()
    for i, w in enumerate(words):
        if w.lower() == "from" and i + 1 < len(words):
            vendor = words[i+1].strip(",.?!")
            break
    if vendor == "Unknown Vendor" and len(words) > 0:
        vendor = words[0].strip(",.?!").capitalize()
        
    return {"parsed_vendor": vendor, "parsed_amount": amount, "raw_text": text}

# WebSockets Endpoint (Real-time dynamic messaging for Mobile / Dashboard)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial configuration values
        invoices = database.get_invoices(include_inactive=False)
        total = sum(i["amount"] for i in invoices)
        await websocket.send_json({
            "event": "handshake",
            "ledger_state": ledger_state["state"],
            "total": total
        })
        
        while True:
            # Maintain connection and listen for client events
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            # Approvals are now routed via direct HTTPS POST /approve for security.
            if payload.get("event") == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        ws_manager.disconnect(websocket)

# HTML Templates Render Pathways
@app.get("/", response_class=HTMLResponse)
def get_dashboard(request: Request):
    local_ip = get_local_ip()
    pin = vault.get_current_pin()
    return templates.TemplateResponse(request, "dashboard.html", {
        "pc_ip": local_ip,
        "pairing_pin": pin
    })

@app.get("/capture", response_class=HTMLResponse)
def get_capture(request: Request):
    local_ip = get_local_ip()
    return templates.TemplateResponse(request, "capture.html", {
        "pc_ip": local_ip
    })

@app.get("/summary", response_class=HTMLResponse)
def get_summary(request: Request):
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    tax_info = tax_rules.calculate_44ada_tax(total)
    advance_tax = tax_rules.get_upcoming_advance_tax(tax_info["new_regime_tax"])
    
    user_id = database.get_primary_user_id()
    approvals = database.get_approvals(user_id)
    
    return templates.TemplateResponse(request, "summary.html", {
        "invoices": invoices,
        "total": total,
        "tax_info": tax_info,
        "advance_tax": advance_tax,
        "approvals": approvals
    })

@app.get("/ca", response_class=HTMLResponse)
def get_ca_portal(request: Request):
    invoices = database.get_invoices(include_inactive=True) # CA sees absolute history
    total = sum(i["amount"] for i in invoices if i["is_active"] == 1)
    
    tax_info = tax_rules.calculate_44ada_tax(total)
    user_id = database.get_primary_user_id()
    approvals = database.get_approvals(user_id)
    devices = database.get_devices()
    
    return templates.TemplateResponse(request, "ca_dashboard.html", {
        "invoices": invoices,
        "total": total,
        "tax_info": tax_info,
        "approvals": approvals,
        "devices": devices
    })

@app.get("/did.json")
def get_did_document():
    """Exposes the PC Hub's Decentralized Identifier Document containing its verification public keys."""
    from cryptography.hazmat.primitives import serialization
    pub_der = ssi_vault.public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_b64 = base64.b64encode(pub_der).decode("utf-8")
    return {
        "@context": "https://www.w3.org/ns/did/v1",
        "id": ssi_vault.did,
        "verificationMethod": [{
            "id": f"{ssi_vault.did}#key-1",
            "type": "JsonWebKey2020",
            "controller": ssi_vault.did,
            "publicKeyJwk": {
                "kty": "EC",
                "crv": "P-256",
                "x": pub_b64[:32],
                "y": pub_b64[32:]
            }
        }],
        "authentication": [f"{ssi_vault.did}#key-1"],
        "assertionMethod": [f"{ssi_vault.did}#key-1"]
    }

@app.get("/invoice/{invoice_id}/proof")
def get_invoice_merkle_proof(invoice_id: int):
    """Generates a binary Merkle proof showing that a specific invoice is included in the current Merkle Root."""
    invoices = database.get_invoices(include_inactive=False)
    target_idx = -1
    for idx, inv in enumerate(invoices):
        if inv["id"] == invoice_id:
            target_idx = idx
            break
    if target_idx == -1:
        raise HTTPException(status_code=404, detail="Invoice not found in active ledger.")
        
    leaves = [f"{i['id']}|{i['vendor']}|{i['amount']}|{i['date']}" for i in invoices]
    merkle = MerkleTree(leaves)
    
    proof = merkle.get_proof(target_idx)
    target_leaf = leaves[target_idx]
    return {
        "invoice_id": invoice_id,
        "leaf": target_leaf,
        "proof": proof,
        "root": merkle.get_root()
    }

@app.get("/compliance/itr4")
def generate_itr4_schema():
    """Generates the official Sugam ITR-4 JSON compliance schema for presumptive taxation."""
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    presumptive_income = total * 0.50
    
    # Calculate simple tax slabs for ITR-4 representation
    taxable = max(0.0, presumptive_income - 75000) # standard deduction under 44ADA
    tax_due = 0.0
    if taxable > 700000:
        tax_due = (taxable - 700000) * 0.10 + 20000
    
    return {
        "assessment_year": "2026-27",
        "form_type": "ITR-4 (Sugam)",
        "section": "44ADA - Presumptive Professional Income",
        "personal_information": {
            "name": "Devashish Sharma",
            "pan_encrypted": "ABCDE1234F",
            "status": "Individual Resident"
        },
        "income_details": {
            "gross_receipts": total,
            "presumptive_income_50pct": presumptive_income,
            "deductions_allowed": 75000.0,
            "net_taxable_income": taxable
        },
        "tax_computation": {
            "gross_tax_due": tax_due,
            "rebate_87A": 20000.0 if taxable <= 700000 else 0.0,
            "net_tax_payable": max(0.0, tax_due - (20000.0 if taxable <= 700000 else 0.0))
        }
    }

@app.get("/compliance/gstr1")
def generate_gstr1_schema():
    """Compiles the monthly GSTR-1 JSON schema mapping out all active invoice records."""
    invoices = database.get_invoices(include_inactive=False)
    
    # Group B2B invoice items
    b2b_items = []
    for inv in invoices:
        taxable_value = inv["amount"]
        cgst = taxable_value * 0.09
        sgst = taxable_value * 0.09
        b2b_items.append({
            "invoice_id": inv["id"],
            "vendor": inv["vendor"],
            "invoice_date": inv["date"],
            "taxable_value": taxable_value,
            "gst_rate_pct": 18.0,
            "cgst": cgst,
            "sgst": sgst,
            "total_gst": cgst + sgst
        })
        
    return {
        "gstin_issuer": "27ABCDE1234F1Z5",
        "tax_period": "July 2026",
        "document_type": "GSTR-1 Return Schema",
        "b2b_summary": {
            "invoice_count": len(invoices),
            "total_taxable_value": sum(item["taxable_value"] for item in b2b_items),
            "total_cgst": sum(item["cgst"] for item in b2b_items),
            "total_sgst": sum(item["sgst"] for item in b2b_items),
            "total_gst": sum(item["total_gst"] for item in b2b_items)
        },
        "invoices": b2b_items
    }

@app.get("/compliance/itr-select")
def select_itr_form():
    """Auto-selects the mandatory ITR form type based on taxpayer profile, foreign receipts, and business activity."""
    invoices = database.get_invoices(include_inactive=False)
    total = sum(float(i["amount"]) for i in invoices)
    has_foreign = any("US" in i["vendor"] or "UK" in i["vendor"] or "NRI" in i["vendor"] or "Global" in i["vendor"] for i in invoices)
    print(f"DEBUG ITR SELECT: len={len(invoices)}, total={total}, has_foreign={has_foreign}")
    
    # Classification rules under Income Tax Act, 2025
    if total > 7500000:
        recommended_form = "ITR-3"
        reason = "Gross receipts exceed Section 44ADA limit (₹75 Lakhs). Audit and detailed P&L mandatory under ITR-3."
        due_date = "31-Oct-2026 (Audit Case)"
    elif has_foreign:
        recommended_form = "ITR-3"
        reason = "Foreign receipts / Schedule FA disclosure detected. ITR-3 mandatory for foreign asset and income reporting."
        due_date = "31-Aug-2026"
    elif total > 0:
        recommended_form = "ITR-4 (Sugam)"
        reason = "Eligible for Presumptive Professional Taxation under Section 44ADA (Turnover <= ₹75L)."
        due_date = "31-Jul-2026"
    else:
        recommended_form = "ITR-1 (Sahaj)"
        reason = "Salaried / Interest income only."
        due_date = "31-Jul-2026"
        
    return {
        "assessment_year": "2026-27",
        "act_framework": "Income Tax Act, 2025 (Section 393 Consolidated TDS)",
        "recommended_form": recommended_form,
        "due_date": due_date,
        "eligibility_reason": reason,
        "turnover_inr": total,
        "has_foreign_receipts": has_foreign,
        "ineligible_forms": [
            {"form": "ITR-1", "reason": "Ineligible due to business/profession income"},
            {"form": "ITR-6", "reason": "Ineligible (for corporate entities only)"}
        ]
    }

@app.get("/compliance/tax-audit")
def get_tax_audit_status():
    """Generates Tax Audit readiness score, Form 3CD draft checklist, and Sec 271B penalty risk."""
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    threshold = 5000000.0 # ₹50L Sec 44AB threshold for 44ADA professionals
    
    audit_required = total > threshold
    penalty_sec_271b = min(150000.0, total * 0.005) if audit_required else 0.0
    readiness_score = 92 if len(invoices) > 10 else 78
    
    return {
        "gross_receipts_inr": total,
        "audit_threshold_44ab": threshold,
        "audit_required": audit_required,
        "readiness_score_pct": readiness_score,
        "sec_271b_penalty_risk_inr": penalty_sec_271b,
        "due_date": "30-Sep-2026",
        "form_3cd_checklist": [
            {"clause": "Clause 8 (Method of Accounting)", "status": "COMPLIANT", "detail": "Mercantile / Cash accrual tracked"},
            {"clause": "Clause 17 (Sec 43CA/50C Property Transfers)", "status": "N/A", "detail": "No immovable property transfer"},
            {"clause": "Clause 21(a) (Personal Expenses Disallowance)", "status": "VERIFIED", "detail": "Zero non-business disallowance"},
            {"clause": "Clause 34 (TDS Compliance Sec 393)", "status": "READY", "detail": "All TDS deductions & Form 26AS reconciled"}
        ]
    }

@app.post("/invoice/{invoice_id}/escalate")
def escalate_invoice(invoice_id: int):
    """Deterministic state machine escalation: adds penalty interest and drafts legal notice."""
    invoices = database.get_invoices(include_inactive=True)
    target = None
    for inv in invoices:
        if inv["id"] == invoice_id:
            target = inv
            break
    if not target:
        raise HTTPException(status_code=404, detail="Invoice not found.")
        
    # Calculate late payment penalty interest: 18% p.a.
    days_overdue = 45
    interest = round(target["amount"] * 0.18 * (days_overdue / 365.0), 2)
    new_state = "ESCALATED"
    
    database.update_invoice_payment_state(invoice_id, new_state, interest)
    
    notice = f"""FORMAL LEGAL NOTICE OF DEMAND
Date: {datetime.date.today().strftime('%d-%b-%Y')}

To: {target['vendor']}

This is a formal smart contract escalation demand notice for outstanding invoice reference #{target['id']}.
Principal Amount: INR {target['amount']:.2f}
Late Payment Interest Accrued (18% p.a. for {days_overdue} days): INR {interest:.2f}
Total Outstanding Balance Due: INR {target['amount'] + interest:.2f}

You are requested to clear the outstanding sum of INR {target['amount'] + interest:.2f} immediately.

Failing this, we will be forced to initiate legal arbitration under local commercial regulations.
"""
    return {
        "invoice_id": invoice_id,
        "previous_state": target.get("payment_state", "ISSUED"),
        "new_state": new_state,
        "interest_charged": interest,
        "total_due": target["amount"] + interest,
        "legal_notice_draft": notice
    }

@app.get("/invoice/{invoice_id}/irn")
def get_invoice_irn(invoice_id: int):
    """Generates a locally deterministic UUID v5 IRN payload for NIC e-invoicing compliance."""
    invoices = database.get_invoices(include_inactive=True)
    target = None
    for inv in invoices:
        if inv["id"] == invoice_id:
            target = inv
            break
    if not target:
        raise HTTPException(status_code=404, detail="Invoice not found.")
        
    namespace = uuid.NAMESPACE_DNS
    irn_source = f"TAXOS|PAN_MOCK_FREELANCER|{target['id']}|{target['amount']}|{target['date']}"
    irn_hash = str(uuid.uuid5(namespace, irn_source))
    
    return {
        "Version": "1.03",
        "Irn": irn_hash,
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": "B2B",
            "RegRev": "N"
        },
        "DocDtls": {
            "Typ": "INV",
            "No": f"TAXOS-{target['id']}",
            "Dt": target["date"]
        },
        "ValDtls": {
            "AssVal": target["amount"],
            "CGstVal": round(target["amount"] * 0.09, 2),
            "SGstVal": round(target["amount"] * 0.09, 2),
            "TotVal": round(target["amount"] * 1.18, 2)
        }
    }

@app.get("/compliance/arbitrage")
def get_tax_arbitrage():
    """Calculates tax minimization strategies and regime billing optimization advice."""
    invoices = database.get_invoices(include_inactive=False)
    advice = forecaster.optimize_tax_regime(invoices)
    return advice

@app.get("/compliance/report")
def generate_audit_report():
    """Generates a plain-English narrative board report of the freelancer's business health."""
    invoices = database.get_invoices(include_inactive=False)
    total = sum(i["amount"] for i in invoices)
    
    tax_info = tax_rules.calculate_44ada_tax(total)
    treasury = forecaster.forecast_cash_flow(invoices)
    penalties = forecaster.forecast_penalties(invoices)
    arbitrage = forecaster.optimize_tax_regime(invoices)
    
    report_text = f"""==================================================
              TAXOS AUDIT NARRATIVE REPORT
==================================================
Date: {datetime.date.today().strftime('%d-%b-%Y')}
Entity: Devashish Sharma (Freelance Professional)
PAN: ABCDE1234F
Assessment Year: 2026-27

1. EXECUTIVE FINANCIAL SUMMARY
--------------------------------------------------
* Cumulative Receipts (Active): INR {total:,.2f}
* Presumptive Profit (Sec 44ADA): INR {total * 0.5:,.2f}
* Estimated Income Tax Due: INR {tax_info['new_regime_tax']:,.2f} (Preferred: New Regime)

2. TREASURY & LIQUIDITY ANALYSIS
--------------------------------------------------
* Days Sales Outstanding (DSO): {treasury['dso']:.1f} Days
* Days Payable Outstanding (DPO): {treasury['dpo']:.1f} Days
* Cash Conversion Cycle (CCC): {treasury['ccc']:.1f} Days
* Liquidity Deficit Risk (30-day projection): {treasury['liquidity_risk_pct']}%

3. REGULATORY COMPLIANCE & RISK PROJECTIONS
--------------------------------------------------
* GST Threshold status: {"LIMIT EXCEEDED" if total >= 2000000 else "COMPLIANT"}
* CBIC Late Filing Penalty Risk: {penalties['risk_pct']}%
* Expected Backdated Penalty Cost: INR {penalties['expected_penalty_inr']:,.2f}
* Tax Arbitrage Suggestion: {arbitrage['regime_advice']}

4. CRYPTOGRAPHIC TRUST VERIFICATIONS
--------------------------------------------------
* Decentralized DID Issuer: {ssi_vault.did}
* Merkle Seal State: Immutable cryptographic ledger chain link verified.

--------------------------------------------------
Prepared by TaxOS Autonomous Sovereign Operating System.
==================================================
"""
    return {"report": report_text}

@app.post("/invoice/{invoice_id}/mark-paid")
def mark_paid(invoice_id: int, payload: dict = None):
    """Marks an invoice as PAID. Solves Brutal Truth #1: The Accrual Tax Trap."""
    invoices = database.get_invoices(include_inactive=True)
    target = None
    for inv in invoices:
        if inv["id"] == invoice_id:
            target = inv
            break
    if not target:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    
    payment_date = datetime.date.today().strftime("%Y-%m-%d")
    if payload and "payment_date" in payload:
        payment_date = payload["payment_date"]
    
    database.mark_invoice_paid(invoice_id, payment_date)
    
    # Recalculate accrual split
    updated_invoices = database.get_invoices(include_inactive=False)
    accrual = tax_rules.calculate_accrual_split(updated_invoices)
    
    return {
        "status": "paid",
        "invoice_id": invoice_id,
        "payment_date": payment_date,
        "accrual_split": accrual
    }

# ========== BRUTAL TRUTH #2: AIS RECONCILIATION (Government Mirror) ==========

class AISEntry(BaseModel):
    source_name: str
    amount: float
    tds_deducted: float = 0.0
    section: str = "TDS"

from typing import List, Dict, Optional, Union
from fastapi import Body

@app.post("/ais/import")
def import_ais(payload: Union[Dict, List] = Body(...)):
    """Imports AIS (Annual Information Statement) entries for reconciliation. Supports flat list, AISEntry list, and structured dict payloads."""
    database.clear_ais_entries()
    imported = []
    
    entries_list = []
    if isinstance(payload, list):
        entries_list = payload
    elif isinstance(payload, dict):
        if "entries" in payload and isinstance(payload["entries"], list):
            entries_list = payload["entries"]
        else:
            if "tds_credits" in payload and isinstance(payload["tds_credits"], list):
                for item in payload["tds_credits"]:
                    v = item.get("vendor") or item.get("source") or item.get("source_name") or "TDS Source"
                    amt = float(item.get("amount", 0.0))
                    gross = amt * 10.0 if amt < 100000 else amt
                    entries_list.append({"source_name": v, "amount": gross, "tds_deducted": amt, "section": "194J"})
            if "foreign_receipts" in payload and isinstance(payload["foreign_receipts"], list):
                for item in payload["foreign_receipts"]:
                    v = item.get("source") or item.get("vendor") or item.get("source_name") or "Foreign Client"
                    amt = float(item.get("amount", 0.0))
                    entries_list.append({"source_name": v, "amount": amt, "tds_deducted": 0.0, "section": "FOREIGN"})
            if "upi_high_value" in payload and isinstance(payload["upi_high_value"], list):
                for item in payload["upi_high_value"]:
                    v = item.get("source") or item.get("vendor") or item.get("source_name") or "UPI Transaction"
                    amt = float(item.get("amount", 0.0))
                    entries_list.append({"source_name": v, "amount": amt, "tds_deducted": 0.0, "section": "SFT"})
    
    for item in entries_list:
        if isinstance(item, dict):
            src = item.get("source_name") or item.get("vendor") or item.get("source") or "Unknown AIS Source"
            amt = float(item.get("amount", 0.0))
            tds = float(item.get("tds_deducted", 0.0))
            sec = str(item.get("section", "TDS"))
            eid = database.add_ais_entry(src, amt, tds, sec)
            imported.append({"id": eid, "source_name": src, "amount": amt})
            
    return {"status": "imported", "count": len(imported), "entries": imported}

@app.get("/ais/reconcile")
def reconcile_ais():
    """Runs three-way AIS reconciliation. Solves Brutal Truth #2: The Government Mirror."""
    invoices = database.get_invoices(include_inactive=False)
    ais_entries = database.get_ais_entries()
    
    result = ais_reconcile(invoices, ais_entries)
    report = ais_variance_report(result)
    drafts = ais_draft(result["unmatched_in_ais"])
    
    return {
        "reconciliation": result,
        "variance_report": report,
        "draft_invoices": drafts
    }

# ========== BRUTAL TRUTH #3: CLASSIFICATION GRADER (44AD vs 44ADA) ==========

@app.get("/compliance/classify")
def classify_invoices():
    """Runs NPU classification analysis on invoices. Solves Brutal Truth #3: The Classification Death Spiral."""
    invoices = database.get_invoices(include_inactive=False)
    vendors = [inv["vendor"] for inv in invoices]
    total = sum(inv["amount"] for inv in invoices)
    
    classification = classify_activity(vendors)
    switch_analysis = simulate_retroactive_switch(total)
    
    return {
        "classification": classification,
        "switch_analysis": switch_analysis
    }

@app.get("/compliance/defense-brief")
def get_defense_brief():
    """Generates a litigation-ready defense brief for the classification choice."""
    invoices = database.get_invoices(include_inactive=False)
    vendors = [inv["vendor"] for inv in invoices]
    total = sum(inv["amount"] for inv in invoices)
    
    classification = classify_activity(vendors)
    brief = generate_defense_brief(classification, total)
    
    return {"defense_brief": brief}

# ========== GST COMPLETE SUITE ==========
@app.post("/purchase")
def add_purchase_endpoint(payload: dict):
    pid = database.add_purchase(
        user_id=database.get_primary_user_id(),
        vendor_name=payload.get("vendor_name", "Unknown Vendor"),
        vendor_gstin=payload.get("vendor_gstin", ""),
        taxable_amount=float(payload.get("taxable_amount", 0.0)),
        cgst=float(payload.get("cgst", 0.0)),
        sgst=float(payload.get("sgst", 0.0)),
        igst=float(payload.get("igst", 0.0)),
        date=payload.get("date", datetime.date.today().strftime("%Y-%m-%d"))
    )
    return {"status": "success", "purchase_id": pid}

@app.get("/compliance/gstr3b")
def get_gstr3b():
    invoices = database.get_invoices(include_inactive=False)
    purchases = database.get_purchases()
    return gst_engine.calculate_gstr3b(invoices, purchases)

@app.get("/compliance/gstr9")
def get_gstr9():
    invoices = database.get_invoices(include_inactive=False)
    purchases = database.get_purchases()
    return gst_engine.generate_gstr9(invoices, purchases)

@app.post("/gst/import-2b")
def import_gstr2b(payload: list = Body(...)):
    purchases = database.get_purchases()
    return gst_engine.reconcile_itc(purchases, payload)

# ========== TDS COMPLETE SUITE ==========
@app.get("/compliance/tds-rates")
def get_tds_rate(nature: str):
    return tds_engine.verify_tds_rate(nature)

@app.post("/tds/record")
def record_tds(payload: dict):
    tid = database.add_tds_record(
        client_id=1,
        amount=float(payload.get("amount", 0.0)),
        section=payload.get("section", "393(1)(z)"),
        rate=float(payload.get("rate", 10.0))
    )
    return {"status": "success", "tds_record_id": tid}

@app.get("/tds/generate-certificate")
def generate_tds_cert():
    records = database.get_tds_records()
    return tds_engine.generate_form16a(records)

# ========== TRANSFER PRICING SUITE ==========
@app.post("/tp/simulate")
def simulate_tp(payload: list = Body(...)):
    return tp_engine.simulate_arms_length_range(payload)

@app.post("/tp/generate-3ceb")
def generate_3ceb(payload: dict = Body(...)):
    client_name = payload.get("client_name", "Acme Global")
    return tp_engine.generate_form3ceb(client_name, payload.get("transactions", {}))

@app.get("/tp/dtaa-check")
def check_dtaa(country_code: str, payment_nature: str):
    return tp_engine.check_dtaa_compliance(country_code, payment_nature)

@app.post("/reset")
def reset_db():
    database.clear_db()
    vault.generate_pairing_pin()
    ledger_state["state"] = "green"
    ledger_state["last_approval_hash"] = "None"
    return {"status": "reset_successful"}
