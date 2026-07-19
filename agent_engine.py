import json
import os
import requests
import database
import tax_rules

# Vector RAG Mock: In a real production setup, we embed and retrieve from Chromadb/Qdrant.
# Here we implement a local rule-and-knowledge index search (GST rules / 44ADA circulars).
OFFLINE_KNOWLEDGE_BASE = {
    "gst_threshold": "Under Section 22 of the CGST Act 2017, suppliers of services must register for GST if aggregate turnover exceeds ₹20 Lakhs. Exporters can file a Letter of Undertaking (LUT) in Form GST RFD-11 to export tax-free.",
    "sec_44ada": "Section 44ADA provides presumptive taxation for specified professionals. 50% of gross receipts is treated as profits. Effective FY 2023-24 onwards, the cash ceiling is ₹75 Lakhs, provided cash receipts do not exceed 5% of total receipts.",
    "advance_tax": "Under Section 208, every person whose estimated tax liability is ₹10,000 or more must pay advance tax. Failure to pay attracts Section 234B/234C interest penalty calculations."
}

class TaxOSAgentEngine:
    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.local_llm_url = "http://localhost:1234/v1/chat/completions"

    def search_invoice_history(self) -> str:
        invoices = database.get_invoices()
        return f"Found {len(invoices)} active invoices. Recent invoices: {json.dumps(invoices[:5])}"

    def calculate_advance_tax(self, total: float) -> str:
        tax_info = tax_rules.calculate_44ada_tax(total)
        est_tax = tax_info["new_regime_tax"]
        adv = tax_rules.get_upcoming_advance_tax(est_tax)
        return f"Estimated tax: ₹{est_tax:,.2f}. Next installment due: {adv['installment']} on {adv['due_date']}, required: ₹{adv['cumulative_due']:,.2f}."

    def fetch_gst_notifications(self, query: str) -> str:
        # Search the offline knowledge base
        results = []
        for key, text in OFFLINE_KNOWLEDGE_BASE.items():
            if key in query.lower() or any(word in text.lower() for word in query.lower().split()):
                results.append(f"[{key.upper()} Rule]: {text}")
        return "\n".join(results) if results else "No specific matching edge rules found."

    def run_agentic_workflow(self, query: str) -> dict:
        """
        Executes a LangGraph-style agent planning trace.
        Tries to call local LLM if running on edge, otherwise executes a local planning loop.
        """
        invoices = database.get_invoices()
        total = sum(i["amount"] for i in invoices)
        
        # 1. Planning phase (Local logging)
        agent_steps = [
            {"step": "Plan", "detail": "Analyze incoming tax query, determine required tools."}
        ]
        
        # Decide which tools to invoke based on query intent
        inv_hist_result = ""
        adv_tax_result = ""
        knowledge_result = ""
        
        if "invoice" in query.lower() or "receipt" in query.lower() or "threshold" in query.lower() or "gst" in query.lower() or "44ada" in query.lower():
            agent_steps.append({"step": "Tool Call", "detail": "search_invoice_history()"})
            inv_hist_result = self.search_invoice_history()
            agent_steps.append({"step": "Tool Response", "detail": inv_hist_result})
            
        if "tax" in query.lower() or "liability" in query.lower() or "advance" in query.lower() or "regime" in query.lower():
            agent_steps.append({"step": "Tool Call", "detail": f"calculate_advance_tax(total={total})"})
            adv_tax_result = self.calculate_advance_tax(total)
            agent_steps.append({"step": "Tool Response", "detail": adv_tax_result})
            
        if "rule" in query.lower() or "law" in query.lower() or "limit" in query.lower() or "gst" in query.lower() or "44ada" in query.lower():
            agent_steps.append({"step": "Tool Call", "detail": f"fetch_gst_notifications('{query}')"})
            knowledge_result = self.fetch_gst_notifications(query)
            agent_steps.append({"step": "Tool Response", "detail": knowledge_result})
            
        # 2. Final response generation
        agent_steps.append({"step": "Synthesize", "detail": "Ground results into structured, 2-sentence user advisory."})
        
        # Try calling the local NPU-accelerated LLM
        prompt = (
            "You are an on-device tax agent in TaxOS, an edge-only compliance coordinator.\n"
            "Formulate a precise, professional 2-sentence recommendation for the freelancer.\n"
            "Ground your answer STRICTLY in the following tool execution context:\n"
            f"- Invoice status: {inv_hist_result}\n"
            f"- Tax estimates: {adv_tax_result}\n"
            f"- Knowledge lookup: {knowledge_result}\n"
            f"- Current total receipts: ₹{total:,.2f}\n"
            "Format your answer as simple text. Do not hallucinate external facts."
        )
        
        try:
            res = requests.post(
                self.local_llm_url,
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0.2
                },
                timeout=1.5
            )
            if res.status_code == 200:
                explanation = res.json()["choices"][0]["message"]["content"].strip()
                return {
                    "source": "local_npu_llm",
                    "steps": agent_steps,
                    "explanation": explanation
                }
        except Exception:
            pass
            
        # Local deterministic synthesis
        pct_gst = (total / tax_rules.TOTAL_GST_LIMIT) * 100.0
        pct_44ada = (total / tax_rules.TOTAL_44ADA_LIMIT) * 100.0
        
        if total < tax_rules.GST_AMBER_LIMIT:
            explanation = (
                f"Your active ledger total stands at ₹{total:,.2f} ({pct_gst:.1f}% of GST limit). "
                "Current analysis indicates fully compliant status; we recommend continuing to log invoices and verifying hardware tokens regularly."
            )
        elif total < tax_rules.TOTAL_GST_LIMIT:
            explanation = (
                f"Ledger receipts have reached ₹{total:,.2f} ({pct_gst:.1f}% of GST limit). "
                "Warning: You are approaching the mandatory GST registration threshold. Please prepare your LUT template and ensure your Arduino trust token is ready for sign-off."
            )
        elif total < tax_rules.TOTAL_44ADA_LIMIT:
            explanation = (
                f"GST threshold of ₹20L has been crossed (current total: ₹{total:,.2f}). "
                "You must register for GST or submit the Letter of Undertaking (LUT) to enable tax-free exports, while remaining within the ₹75L Section 44ADA limit."
            )
        else:
            explanation = (
                f"Your gross receipts of ₹{total:,.2f} exceed the Section 44ADA presumptive tax limit (₹75 Lakhs). "
                "You are legally required to maintain formal books of accounts under Section 44AA and schedule a tax audit under Section 44AB."
            )
            
        return {
            "source": "deterministic_agent",
            "steps": agent_steps,
            "explanation": explanation
        }
