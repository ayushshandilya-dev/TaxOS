# TaxOS Production - Setup and Run Helper Script
# This script sets up a local Python virtual environment, installs dependencies, and launches the server.

$ErrorActionPreference = "Stop"

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "          TaxOS Production Edge Hub          " -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# 1. Locate Python (with dynamic environment path recovery for newly installed packages)
$pythonExe = "python"
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Found Python in PATH: $pythonVersion" -ForegroundColor Green
} catch {
    # Check default winget user path
    $defaultWingetPath = "C:\Users\qcwor\AppData\Local\Programs\Python\Python311-arm64"
    $localPythonExe = Join-Path $defaultWingetPath "python.exe"
    
    if (Test-Path $localPythonExe) {
        Write-Host "Located newly installed Python at: $localPythonExe" -ForegroundColor Green
        # Inject Python and Scripts directories into session PATH
        $env:PATH = "$defaultWingetPath;" + (Join-Path $defaultWingetPath "Scripts") + ";" + $env:PATH
        $pythonExe = $localPythonExe
    } else {
        Write-Host "[ERROR] Python was not found." -ForegroundColor Red
        Write-Host "Please download and install Python 3.10+ (specifically the ARM64 version if on a Snapdragon PC)." -ForegroundColor Yellow
        Write-Host "Ensure 'Add Python to PATH' is selected during installation." -ForegroundColor Yellow
        Exit
    }
}

# 2. Setup virtual environment
$venvDir = Join-Path $PSScriptRoot ".venv"
if (-not (Test-Path $venvDir)) {
    Write-Host "Creating Python Virtual Environment in $venvDir..." -ForegroundColor Cyan
    & $pythonExe -m venv .venv
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Using existing Virtual Environment." -ForegroundColor Green
}

# 3. Activate virtual environment
$activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
Write-Host "Activating virtual environment..." -ForegroundColor Cyan
& $activateScript

# 4. Install dependencies
Write-Host "Checking and installing dependencies from requirements.txt..." -ForegroundColor Cyan
python -m pip install --upgrade pip
pip install -r requirements.txt
Write-Host "Dependencies successfully verified." -ForegroundColor Green

# 5. Run automated tests
Write-Host ""
Write-Host "Running automated production test suite..." -ForegroundColor Cyan
python -m unittest test_production.py
Write-Host "All tests verified successfully!" -ForegroundColor Green
Write-Host ""

# 6. Launch Uvicorn Edge Hub
Write-Host "Starting TaxOS Edge Hub server at http://localhost:8000..." -ForegroundColor Cyan
Write-Host "Make sure paired devices are on the same local network." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to terminate the server." -ForegroundColor Yellow
Write-Host ""

uvicorn main:app --host 0.0.0.0 --port 8000
