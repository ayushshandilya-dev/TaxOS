import unittest
import database
import tax_rules
from main import app
from fastapi.testclient import TestClient

class TestTaxOS(unittest.TestCase):
    
    def setUp(self):
        # Initialize and clear DB before each test
        database.clear_db()
        self.client = TestClient(app)

    def tearDown(self):
        # Clear database to leave clean slate
        database.clear_db()

    def test_database_invoice_operations(self):
        # Verify initial state is empty
        invoices = database.get_invoices()
        self.assertEqual(len(invoices), 0)
        
        # Add invoice
        inv_id = database.add_invoice("Google LLC", 150000.0, "2026-07-15", False)
        self.assertIsNotNone(inv_id)
        
        # Verify retrieval
        invoices = database.get_invoices()
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0]["vendor"], "Google LLC")
        self.assertEqual(invoices[0]["amount"], 150000.0)
        self.assertEqual(invoices[0]["is_anomaly"], 0)

    def test_anomaly_detection_rules(self):
        # Setup: add 2 past invoices for a vendor
        database.add_invoice("Apple App Store", 10000.0, "2026-06-01", False)
        database.add_invoice("Apple App Store", 12000.0, "2026-07-01", False)
        
        # Retrieve past invoices for the rule check
        past = database.get_invoices()
        
        # Test case 1: normal amount (no anomaly)
        is_anom_normal = tax_rules.check_anomaly("Apple App Store", 15000.0, past)
        self.assertFalse(is_anom_normal)
        
        # Test case 2: >2x vendor average (average = 11000, 2x = 22000)
        # 25000 is an anomaly
        is_anom_unusual = tax_rules.check_anomaly("Apple App Store", 25000.0, past)
        self.assertTrue(is_anom_unusual)

    def test_44ada_tax_rules(self):
        # Test Case 1: Gross receipts below zero tax rebate limit (Presumptive income <= 7 Lakhs, i.e., gross <= 14 Lakhs)
        # ₹10 Lakh receipts = ₹5 Lakh presumptive income -> Tax should be ₹0
        res = tax_rules.calculate_44ada_tax(1000000.0)
        self.assertEqual(res["presumptive_income"], 500000.0)
        self.assertEqual(res["estimated_tax"], 0.0)
        self.assertFalse(res["limit_exceeded"])
        
        # Test Case 2: Gross receipts crossing rebate limit
        # ₹24 Lakh receipts = ₹12 Lakh presumptive income -> Tax calculation:
        # Slabs:
        # 0-3L: 0
        # 3-6L: 5% (15,000)
        # 6-9L: 10% (30,000)
        # 9-12L: 15% (45,000)
        # Total Tax = 90,000 + 4% cess = 93,600
        res = tax_rules.calculate_44ada_tax(2400000.0)
        self.assertEqual(res["presumptive_income"], 1200000.0)
        self.assertAlmostEqual(res["estimated_tax"], 93600.0)
        self.assertFalse(res["limit_exceeded"])
        
        # Test Case 3: Exceeding 44ADA Cash limit of ₹75 Lakhs
        res = tax_rules.calculate_44ada_tax(8000000.0)
        self.assertTrue(res["limit_exceeded"])

    def test_advance_tax_deadlines(self):
        # Estimated tax = ₹1,00,000
        # Let's mock a date in July 2026. The next deadline should be Q2 (Sept 15) with 45% cumulative due
        import datetime
        mock_date = datetime.date(2026, 7, 18)
        adv = tax_rules.get_upcoming_advance_tax(100000.0, current_date=mock_date)
        
        self.assertEqual(adv["installment"], "Q2 (Sept 15)")
        self.assertEqual(adv["due_date"], "15-Sep-2026")
        self.assertEqual(adv["percent"], 45)
        self.assertEqual(adv["cumulative_due"], 45000.0)

    def test_server_invoice_endpoints(self):
        # Post first invoice
        payload = {"vendor": "Google", "amount": 500000.0, "date": "2026-07-18"}
        response = self.client.post("/invoice", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total"], 500000.0)
        self.assertEqual(data["invoice_count"], 1)
        
        # Verify status endpoint updates
        status_res = self.client.get("/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertEqual(status_data["total"], 500000.0)
        self.assertEqual(status_data["arduino_state"], "green")
        
        # Post enough to cross GST Amber (80% of 20L = 16L)
        self.client.post("/invoice", json={"vendor": "Google", "amount": 1200000.0, "date": "2026-07-18"})
        status_res = self.client.get("/status")
        self.assertEqual(status_res.json()["arduino_state"], "amber")
        
        # Post enough to cross GST Limit (20L)
        self.client.post("/invoice", json={"vendor": "Google", "amount": 400000.0, "date": "2026-07-18"})
        status_res = self.client.get("/status")
        self.assertEqual(status_res.json()["arduino_state"], "red")

    def test_ledger_approvals_chain(self):
        # Perform approval post
        test_hash = "f1e2d3c4b5a697887766554433221100abcdef1234567890abcdef1234567890"
        payload = {"hash": test_hash}
        response = self.client.post("/approve", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "approved")
        self.assertEqual(data["hash"], test_hash)
        
        # Verify DB chain
        approvals = database.get_approvals()
        # Initial block is GENESIS (1) and then our new block (2)
        self.assertEqual(len(approvals), 2)
        # approvals are ordered desc, so index 0 is our new block
        self.assertEqual(approvals[0]["current_hash"], test_hash)
        self.assertEqual(approvals[0]["previous_hash"], "GENESIS_HASH_PLACEHOLDER")

    def test_agent_reasoning_and_voice(self):
        # Test agent reasoning endpoint returns source and explanation
        response = self.client.get("/agent-reasoning")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("source", data)
        self.assertIn("explanation", data)
        
        # Test voice entry mock parsing
        payload = {"text": "invoice from Upwork of seventy-five thousand rupees"}
        response = self.client.post("/voice-entry", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["parsed_vendor"], "Upwork")
        self.assertEqual(data["parsed_amount"], 75000.0)

if __name__ == "__main__":
    unittest.main()
