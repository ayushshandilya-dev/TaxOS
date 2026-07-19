import unittest
import json
import database
import tax_rules
from crypto_vault import CryptoVault
from main import app
from fastapi.testclient import TestClient

class TestTaxOSProduction(unittest.TestCase):

    def setUp(self):
        database.clear_db()
        from main import ledger_state
        ledger_state["state"] = "green"
        ledger_state["last_approval_hash"] = "None"
        self.vault = CryptoVault()
        self.client = TestClient(app)

    def tearDown(self):
        database.clear_db()

    def test_cryptography_vault(self):
        plain = "Confidential Tax Record $50,000"
        encrypted = self.vault.encrypt(plain)
        self.assertNotEqual(plain, encrypted)
        
        decrypted = self.vault.decrypt(encrypted)
        self.assertEqual(plain, decrypted)
        
        # Test pairing PIN logic
        pin = self.vault.generate_pairing_pin()
        self.assertEqual(len(pin), 6)
        self.assertTrue(pin.isdigit())
        
        # Test validation
        current_pin = self.vault.get_current_pin()
        self.assertTrue(self.vault.verify_pairing_pin(current_pin))
        # PIN should be cleared/changed after verification
        self.assertNotEqual(current_pin, self.vault.get_current_pin())

    def test_database_multi_tenancy_and_forensics(self):
        # Default user and client should be seeded
        user_id = database.get_primary_user_id()
        self.assertEqual(user_id, 1)
        
        fy_id = database.get_active_fy_id()
        self.assertEqual(fy_id, 2) # FY 2026-27 is seeded second and marked active
        
        # Add a new client
        # Invoices should show immutable forensic chain
        inv_id1 = database.add_invoice(user_id, 1, fy_id, "Google Ireland", 200000.0, "2026-07-18")
        
        # Correct the invoice (e.g. wrong amount recorded, correct it to 220000.0)
        inv_id2 = database.add_invoice(user_id, 1, fy_id, "Google Ireland", 220000.0, "2026-07-18", supersedes_id=inv_id1)
        
        # Check active invoices (only corrected invoice should be returned)
        active_invoices = database.get_invoices(include_inactive=False)
        self.assertEqual(len(active_invoices), 1)
        self.assertEqual(active_invoices[0]["id"], inv_id2)
        self.assertEqual(active_invoices[0]["amount"], 220000.0)
        
        # Check absolute history (CA view returns both rows)
        all_invoices = database.get_invoices(include_inactive=True)
        self.assertEqual(len(all_invoices), 2)
        # Check linkage
        self.assertEqual(all_invoices[0]["supersedes_invoice_id"], inv_id1) # Sorted by id desc
        self.assertEqual(all_invoices[0]["is_active"], 1)
        self.assertEqual(all_invoices[1]["is_active"], 0)

    def test_regime_comparison_tax_rules(self):
        # Test Case 1: Presumptive taxable profit below ₹7 Lakhs (Gross <= ₹14 Lakhs)
        # ₹12 Lakh receipts = ₹6 Lakh presumptive profit -> Nil tax in new regime (due to 87A rebate)
        res1 = tax_rules.calculate_44ada_tax(1200000.0)
        self.assertEqual(res1["new_regime_tax"], 0.0)
        
        # Test Case 2: Comparative calculation for ₹30 Lakh receipts
        # Presumptive profits = ₹15 Lakhs
        # New Regime Slabs:
        # 0-3L: 0%
        # 3-6L: 5% (15k)
        # 6-9L: 10% (30k)
        # 9-12L: 15% (45k)
        # 12-15L: 20% (60k)
        # Total = 150k + 4% cess = 156,000
        # Old Regime Slabs (Presumptive profits = 15L - 1.5L 80C deductions = 13.5L net taxable):
        # 0-2.5L: 0%
        # 2.5L-5L: 5% (12.5k)
        # 5L-10L: 20% (100k)
        # >10L: 30% (3.5L * 30% = 105k)
        # Total = 217.5k + 4% cess = 226,200
        res2 = tax_rules.calculate_44ada_tax(3000000.0)
        self.assertAlmostEqual(res2["new_regime_tax"], 156000.0)
        self.assertAlmostEqual(res2["old_regime_tax"], 226200.0)
        self.assertEqual(res2["preferred_regime"], "New Tax Regime")
        self.assertAlmostEqual(res2["tax_savings"], 70200.0)

    def test_api_pairing_and_endpoints(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives import hashes

        # Generate local mock client ECC key pair
        private_key = ec.generate_private_key(ec.SECP256R1())
        pub_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        pub_b64 = base64.b64encode(pub_bytes).decode("utf-8")

        # Fetch current pairing PIN
        pin = self.vault.get_current_pin()
        
        # Test Pairing - register public key and password
        pair_res = self.client.post("/pair", json={
            "pin": pin,
            "device_name": "Test Secure Phone Client",
            "public_key": pub_b64,
            "device_password": "testpassword"
        })
        self.assertEqual(pair_res.status_code, 200)
        data = pair_res.json()
        token = data["device_token"]
        self.assertTrue(database.verify_device_token(token))
        self.assertEqual(database.get_device_public_key(token), pub_b64)
        
        # Test Unlock - valid password
        unlock_res = self.client.post("/unlock", json={
            "device_token": token,
            "password": "testpassword"
        })
        self.assertEqual(unlock_res.status_code, 200)
        
        # Test Unlock - invalid password
        bad_unlock_res = self.client.post("/unlock", json={
            "device_token": token,
            "password": "wrongpassword"
        })
        self.assertEqual(bad_unlock_res.status_code, 401)

        # Test Invoice post
        payload = {"vendor": "Microsoft Corporation", "amount": 600000.0, "date": "2026-07-18"}
        inv_res = self.client.post("/invoice", json=payload)
        self.assertEqual(inv_res.status_code, 200)
        inv_data = inv_res.json()
        self.assertIn("merkle_root", inv_data)
        self.assertIn("forecast", inv_data)
        
        # Test Status endpoint contains forecasting and simulation analytics
        status_res = self.client.get("/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertIn("treasury", status_data)
        self.assertIn("penalties", status_data)
        self.assertIn("arbitrage", status_data)
        self.assertEqual(status_data["penalties"]["risk_pct"], 4.0) # threshold for ₹600k total
        
        # Check reasoning engine contains trace steps
        agent_res = self.client.get("/agent-reasoning")
        self.assertEqual(agent_res.status_code, 200)
        agent_data = agent_res.json()
        self.assertTrue(len(agent_data["steps"]) > 0)

        # Test Cryptographic Sign-Off challenge verification
        challenge = "GENESIS_HASH_PLACEHOLDER|600000.0|1718181818"
        signature_bytes = private_key.sign(
            challenge.encode("utf-8"),
            ec.ECDSA(hashes.SHA256())
        )
        signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")
        
        new_hash = "mock_sealed_ledger_hash_value"
        
        # Approve with valid signature
        app_res = self.client.post(
            "/approve", 
            json={
                "hash": new_hash,
                "signature": signature_b64,
                "challenge": challenge
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(app_res.status_code, 200)

        # Approve with invalid signature (should return 400)
        bad_app_res = self.client.post(
            "/approve", 
            json={
                "hash": new_hash,
                "signature": base64.b64encode(b"invalid_signature_bytes").decode("utf-8"),
                "challenge": challenge
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(bad_app_res.status_code, 400)

        # Test Verifiable Credential Generation & Verification
        vc_res = self.client.get("/credentials/issue")
        self.assertEqual(vc_res.status_code, 200)
        vc_data = vc_res.json()
        self.assertEqual(vc_data["credentialSubject"]["freelancerName"], "Devashish Sharma")
        
        # Verify VC using local SSIVault
        from main import ssi_vault
        self.assertTrue(ssi_vault.verify_credential(vc_data))

        # Test ITR-4 Schema Generation
        itr_res = self.client.get("/compliance/itr4")
        self.assertEqual(itr_res.status_code, 200)
        itr_data = itr_res.json()
        self.assertEqual(itr_data["form_type"], "ITR-4 (Sugam)")
        self.assertIn("tax_computation", itr_data)

        # Test GSTR-1 Schema Generation
        gstr_res = self.client.get("/compliance/gstr1")
        self.assertEqual(gstr_res.status_code, 200)
        gstr_data = gstr_res.json()
        self.assertEqual(gstr_data["gstin_issuer"], "27ABCDE1234F1Z5")
        self.assertTrue(len(gstr_data["invoices"]) > 0)

        # Test Smart Contract Invoice Escalation
        invoice_id = gstr_data["invoices"][0]["invoice_id"]
        esc_res = self.client.post(f"/invoice/{invoice_id}/escalate")
        self.assertEqual(esc_res.status_code, 200)
        esc_data = esc_res.json()
        self.assertEqual(esc_data["new_state"], "ESCALATED")
        self.assertIn("FORMAL LEGAL NOTICE", esc_data["legal_notice_draft"])

        # Test Offline E-Invoicing IRN Generator
        irn_res = self.client.get(f"/invoice/{invoice_id}/irn")
        self.assertEqual(irn_res.status_code, 200)
        irn_data = irn_res.json()
        self.assertEqual(irn_data["Version"], "1.03")
        self.assertIn("Irn", irn_data)

        # Test Tax Arbitrage Recommendations
        arb_res = self.client.get("/compliance/arbitrage")
        self.assertEqual(arb_res.status_code, 200)
        arb_data = arb_res.json()
        self.assertIn("regime_advice", arb_data)

        # Test Narrative Board Report
        rep_res = self.client.get("/compliance/report")
        self.assertEqual(rep_res.status_code, 200)
        rep_data = rep_res.json()
        self.assertIn("TAXOS AUDIT NARRATIVE REPORT", rep_data["report"])

        # Test ITR Form Auto-Selector (Income Tax Act, 2025)
        itr_select_res = self.client.get("/compliance/itr-select")
        self.assertEqual(itr_select_res.status_code, 200)
        self.assertIn("recommended_form", itr_select_res.json())

        # Test Tax Audit Readiness Score & Sec 271B Exposure
        audit_res = self.client.get("/compliance/tax-audit")
        self.assertEqual(audit_res.status_code, 200)
        self.assertIn("readiness_score_pct", audit_res.json())

    def test_brutal_truth_1_accrual_trap(self):
        """Tests the Accrual Tax Trap solver — Green Zone / Red Zone split."""
        # Create an invoice first (unpaid by default)
        inv_post = self.client.post("/invoice", json={"vendor": "Acme Corp", "amount": 2000000.0, "date": "2026-07-18"})
        self.assertEqual(inv_post.status_code, 200)

        # Status should contain accrual_split
        status_res = self.client.get("/status")
        data = status_res.json()
        self.assertIn("accrual_split", data)
        split = data["accrual_split"]
        
        # All invoices are unpaid initially
        self.assertEqual(split["paid_invoice_count"], 0)
        self.assertGreater(split["unpaid_invoice_count"], 0)
        self.assertEqual(split["green_zone_tax"], 0.0)
        self.assertGreater(split["red_zone_tax"], 0.0)
        
        # Mark an invoice as paid
        inv_id = data["invoices"][0]["id"]
        paid_res = self.client.post(f"/invoice/{inv_id}/mark-paid", json={"payment_date": "2026-07-18"})
        self.assertEqual(paid_res.status_code, 200)
        paid_data = paid_res.json()
        self.assertEqual(paid_data["status"], "paid")
        
        # Now accrual split should show some green zone
        new_split = paid_data["accrual_split"]
        self.assertGreater(new_split["green_zone_tax"], 0.0)
        self.assertGreater(new_split["paid_invoice_count"], 0)

    def test_brutal_truth_2_ais_reconciliation(self):
        """Tests the AIS Government Mirror reconciliation engine."""
        # Import AIS entries
        ais_data = [
            {"source_name": "Microsoft Corporation", "amount": 600000.0, "tds_deducted": 60000.0, "section": "194J"},
            {"source_name": "Unknown Corp", "amount": 250000.0, "tds_deducted": 25000.0, "section": "194J"}
        ]
        import_res = self.client.post("/ais/import", json=ais_data)
        self.assertEqual(import_res.status_code, 200)
        self.assertEqual(import_res.json()["count"], 2)
        
        # Run reconciliation
        recon_res = self.client.get("/ais/reconcile")
        self.assertEqual(recon_res.status_code, 200)
        recon_data = recon_res.json()
        
        # Should have reconciliation result with matched/unmatched
        self.assertIn("reconciliation", recon_data)
        self.assertIn("variance_report", recon_data)
        self.assertIn("draft_invoices", recon_data)
        
        # Unknown Corp should be unmatched in AIS (govt sees income we didn't invoice)
        unmatched_ais = recon_data["reconciliation"]["unmatched_in_ais"]
        self.assertTrue(any(e["source_name"] == "Unknown Corp" for e in unmatched_ais))
        
        # Draft invoices should be generated for unmatched AIS
        self.assertTrue(len(recon_data["draft_invoices"]) > 0)

    def test_brutal_truth_3_classification_grader(self):
        """Tests the Classification Death Spiral solver — 44AD vs 44ADA grader."""
        # Classify based on current invoices (Microsoft = software/IT)
        classify_res = self.client.get("/compliance/classify")
        self.assertEqual(classify_res.status_code, 200)
        classify_data = classify_res.json()
        
        self.assertIn("classification", classify_data)
        self.assertIn("switch_analysis", classify_data)
        self.assertIn("confidence_pct", classify_data["classification"])
        self.assertIn("misclassification_cost_inr", classify_data["switch_analysis"])
        
        # Defense brief should be generated
        brief_res = self.client.get("/compliance/defense-brief")
        self.assertEqual(brief_res.status_code, 200)
        brief_data = brief_res.json()
        self.assertIn("CLASSIFICATION DEFENSE BRIEF", brief_data["defense_brief"])

    def test_websocket_pipeline(self):
        # Test websocket handshake
        with self.client.websocket_connect("/ws") as websocket:
            data = websocket.receive_json()
            self.assertEqual(data["event"], "handshake")
            self.assertEqual(data["ledger_state"], "green")
            
            # Send mock approval via HTTP POST
            test_hash = "signature_value_for_verification"
            app_res = self.client.post("/approve", json={"hash": test_hash})
            self.assertEqual(app_res.status_code, 200)
            
            # Check WebSocket broadcast response
            broadcast = websocket.receive_json()
            self.assertEqual(broadcast["event"], "approved")
            self.assertEqual(broadcast["hash"], test_hash)
            self.assertEqual(broadcast["ledger_state"], "green")
            
            # Verify database has signature logged
            user_id = database.get_primary_user_id()
            approvals = database.get_approvals(user_id)
            self.assertEqual(approvals[0]["current_hash"], test_hash)

    def test_phase2_complete_suite(self):
        """Tests the Phase 2 engines: GST, TDS, and Transfer Pricing."""
        # 1. GST Suite
        # Add a purchase
        pur_res = self.client.post("/purchase", json={
            "vendor_name": "Cloud Services Inc",
            "vendor_gstin": "27ABCDE1234F1Z5",
            "taxable_amount": 100000.0,
            "cgst": 9000.0,
            "sgst": 9000.0,
            "igst": 0.0,
            "date": "2026-07-18"
        })
        self.assertEqual(pur_res.status_code, 200)
        
        # Test GSTR-3B Calculation
        gstr3b_res = self.client.get("/compliance/gstr3b")
        self.assertEqual(gstr3b_res.status_code, 200)
        self.assertIn("net_gst_payable_inr", gstr3b_res.json())
        
        # Test ITC Matcher (Import 2B)
        gstr2b_data = [
            {"vendor_gstin": "27ABCDE1234F1Z5", "taxable_amount": 100000.0, "cgst": 9000.0, "sgst": 9000.0, "igst": 0.0, "invoice_date": "2026-07-18"}
        ]
        import2b_res = self.client.post("/gst/import-2b", json=gstr2b_data)
        self.assertEqual(import2b_res.status_code, 200)
        itc_res = import2b_res.json()
        self.assertEqual(len(itc_res["matched"]), 1)
        self.assertEqual(itc_res["itc_lost_inr"], 0.0)
        
        # 2. TDS Suite
        tds_rate_res = self.client.get("/compliance/tds-rates?nature=Professional Services")
        self.assertEqual(tds_rate_res.status_code, 200)
        self.assertEqual(tds_rate_res.json()["rate_pct"], 10.0)
        
        record_tds_res = self.client.post("/tds/record", json={"amount": 50000.0, "section": "393(1)(a)", "rate": 10.0})
        self.assertEqual(record_tds_res.status_code, 200)
        
        cert_res = self.client.get("/tds/generate-certificate")
        self.assertEqual(cert_res.status_code, 200)
        self.assertIn("summary", cert_res.json())
        self.assertEqual(cert_res.json()["summary"]["total_tds_deducted"], 5000.0)
        
        # 3. Transfer Pricing Suite
        tp_sim_res = self.client.post("/tp/simulate", json=[
            {"amount": 100}, {"amount": 110}, {"amount": 105}, {"amount": 120}, {"amount": 115}, {"amount": 108}, {"amount": 112}
        ])
        self.assertEqual(tp_sim_res.status_code, 200)
        self.assertIn("median", tp_sim_res.json())
        
        dtaa_res = self.client.get("/tp/dtaa-check?country_code=SG&payment_nature=Royalty")
        self.assertEqual(dtaa_res.status_code, 200)
        self.assertEqual(dtaa_res.json()["risk_level"], "HIGH")
        self.assertTrue(any("Form 41" in req for req in dtaa_res.json()["dtaa_requirements"]))

if __name__ == "__main__":
    unittest.main()
