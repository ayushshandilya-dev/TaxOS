# load_all_data.ps1
# Run this in PowerShell while the server is running

$base_url = "http://localhost:8000"

# Clear existing data (optional - be careful!)
# Invoke-RestMethod -Method DELETE -Uri "$base_url/reset"

# Load 20 invoices (mix of paid and unpaid)
$invoices = @(
    @{vendor="Acme Corp (US)"; amount=250000; date="2025-04-05"},
    @{vendor="Fintech India Pvt Ltd"; amount=180000; date="2025-05-15"},
    @{vendor="Global Tech LLC"; amount=420000; date="2025-06-22"},
    @{vendor="HealthWave Startup"; amount=95000; date="2025-07-10"},
    @{vendor="Ecom Retail Solutions"; amount=210000; date="2025-08-25"},
    @{vendor="Acme Corp (US)"; amount=300000; date="2025-09-05"},
    @{vendor="Fintech India Pvt Ltd"; amount=220000; date="2025-10-20"},
    @{vendor="The Design Firm"; amount=75000; date="2025-11-02"},
    @{vendor="Global Tech LLC"; amount=450000; date="2025-11-15"},
    @{vendor="HealthWave Startup"; amount=110000; date="2025-12-01"},
    @{vendor="Ecom Retail Solutions"; amount=190000; date="2025-12-10"},
    @{vendor="NRI Client (UK)"; amount=600000; date="2026-01-05"},
    @{vendor="Fintech India Pvt Ltd"; amount=250000; date="2026-01-20"},
    @{vendor="Green Energy Corp"; amount=350000; date="2026-02-01"},
    @{vendor="Acme Corp (US)"; amount=400000; date="2026-02-10"},
    @{vendor="Global Tech LLC"; amount=500000; date="2026-02-20"},
    @{vendor="AI Startup Hub"; amount=200000; date="2026-02-28"},
    @{vendor="Fintech India Pvt Ltd"; amount=300000; date="2026-03-05"},
    @{vendor="The Design Firm"; amount=120000; date="2026-03-10"},
    @{vendor="HealthWave Startup"; amount=280000; date="2026-03-12"}
)

foreach ($inv in $invoices) {
    $body = $inv | ConvertTo-Json
    Invoke-RestMethod -Method POST -Uri "$base_url/invoice" -Body $body -ContentType "application/json"
    Write-Host "Loaded invoice: $($inv.vendor) - ₹$($inv.amount)"
}

Write-Host "`n✅ All 20 invoices loaded successfully!"
Write-Host "Open dashboard: http://localhost:8000"
