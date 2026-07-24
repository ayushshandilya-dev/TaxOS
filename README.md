# TaxOS 

Hey there! Welcome to **TaxOS**. We built this project to solve a massive problem for Indian freelancers, small businesses, and startups: dealing with the new Income Tax Act of 2025 without losing your mind (or your privacy).

TaxOS is basically an operating system for your taxes. It runs completely locally on your computer—meaning your financial data never touches the cloud.

## What it does

Here's what we built into the platform:

* **Catches Mismatches Early (AIS Reconciliation):** The government already knows what you earn. We check your local invoices against the government's data to make sure everything matches perfectly *before* you file, saving you from automated tax notices.
* **Fixes Wrong Tax Brackets (Classification Grader):** Should you file as a business or a professional? Our engine scans your invoices and tells you the exact right category so you don't overpay or get rejected.
* **GST & ITC Engine:** It automatically calculates what you owe for GST (GSTR-3B) and makes sure you don't lose out on any Input Tax Credit (ITC) by matching your purchases.
* **TDS & Transfer Pricing:** We automated the new Section 393 withholding tax rates and built a simulator for cross-border pricing so startups don't run into double-taxation issues.
* **Saves Your Cash Flow:** It figures out exactly how much tax you owe on money you've *actually received*, vs money you're still waiting for, so you don't go broke paying advance tax.

## How to run it on your machine

It's super easy to get it running. You just need Python installed.

1. **Open your terminal** and go into the `taxos` folder.
2. **Start the server** by running this command:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```
3. **Open your browser** and go to `http://localhost:8000`. 

That's it! You'll see the full dashboard with all the metrics and compliance tools working live.

## How to run our tests
If you want to see the math and logic engines working behind the scenes, you can run our test suite:
```powershell
.\.venv\Scripts\python.exe -m unittest test_production.py
```

## Why we built it this way (Zero-Trust)
We used an architecture where everything is encrypted locally. There are no cloud servers, no web databases, and no third parties reading your invoices. The code just does the math and gives you the exact forms you need.
