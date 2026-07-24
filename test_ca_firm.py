import unittest
import json
import database
import ca_engine
from main import app
from fastapi.testclient import TestClient

class TestCAFirmPracticeSuite(unittest.TestCase):

    def setUp(self):
        database.clear_db()
        self.client = TestClient(app)
        
        # Seed default staff members
        database.add_staff_member("CA Ramesh Kumar", "ramesh@ca-firm.in", "PARTNER")
        database.add_staff_member("Priya Sharma", "priya@ca-firm.in", "SENIOR_CA")
        database.add_staff_member("Ankit Verma", "ankit@ca-firm.in", "ARTICLED_CLERK")
        
        # Seed default taxpayers
        self.t1_id = database.add_taxpayer("Apex Tech Solutions", "AAAPA1234A", "27AAAPA1234A1Z1", "PVT_LTD", "COMPLIANT", "Priya Sharma")
        self.t2_id = database.add_taxpayer("Vikram Malhotra", "BMPM5678B", None, "INDIVIDUAL", "COMPLIANT", "Ankit Verma")

    def tearDown(self):
        database.clear_db()

    def test_firm_compliance_matrix(self):
        matrix = ca_engine.generate_firm_compliance_matrix()
        self.assertEqual(matrix["total_taxpayers"], 2)
        self.assertEqual(matrix["staff_count"], 3)
        self.assertEqual(matrix["compliant_count"], 2)

    def test_bulk_ais_reconciliation(self):
        batch = [
            {
                "taxpayer_id": self.t1_id,
                "ais_entries": [
                    {"source_name": "Infosys Tech", "amount": 1000000.0, "tds_deducted": 10000.0, "section": "194J"}
                ]
            },
            {
                "taxpayer_id": self.t2_id,
                "ais_entries": [
                    {"source_name": "Unmatched Payer Corp", "amount": 500000.0, "tds_deducted": 5000.0, "section": "194C"}
                ]
            }
        ]
        
        res = ca_engine.bulk_ais_reconcile(batch)
        self.assertEqual(res["processed_count"], 2)
        
        taxpayers = database.get_taxpayers()
        self.assertTrue(any(t["status"] in ["COMPLIANT", "AIS_DISCREPANCY"] for t in taxpayers))

    def test_batch_advance_tax_calculator(self):
        schedule = ca_engine.batch_advance_tax_calculator()
        self.assertEqual(len(schedule), 2)
        for s in schedule:
            self.assertIn("installments", s)
            self.assertIn("June_15_Q1", s["installments"])
            self.assertGreaterEqual(s["net_annual_tax"], 0.0)

    def test_ca_audit_package_and_notices(self):
        # Log a Section 143(1) notice
        nid = database.add_notice(self.t1_id, "143(1)", "2026-07-24", "RECEIVED", "Initial brief draft")
        database.update_taxpayer_status(self.t1_id, "NOTICE_PENDING")
        
        package = ca_engine.generate_ca_audit_package(self.t1_id)
        self.assertEqual(package["taxpayer_id"], self.t1_id)
        self.assertEqual(package["compliance_status"], "NOTICE_PENDING")
        self.assertEqual(len(package["notices"]), 1)
        self.assertIn("CLASSIFICATION DEFENSE BRIEF", package["defense_brief"])

    def test_api_ca_endpoints(self):
        # Test GET /api/ca/dashboard
        r = self.client.get("/api/ca/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("total_taxpayers", r.json())
        
        # Test POST /api/ca/taxpayers
        r2 = self.client.post("/api/ca/taxpayers", json={
            "name": "Dr. Sunita Rao",
            "pan": "CSUPR9999Z",
            "entity_type": "INDIVIDUAL",
            "assigned_staff": "Priya Sharma"
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "created")
        
        # Test GET /api/ca/advance-tax-summary
        r3 = self.client.get("/api/ca/advance-tax-summary")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(len(r3.json()), 3) # 2 original + 1 newly created

if __name__ == "__main__":
    unittest.main()
