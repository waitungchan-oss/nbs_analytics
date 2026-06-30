param(
    [ValidateSet("cu128", "cu126", "cpu")]
    [string]$TorchCuda = "cu128",
    [string]$PythonCommand = "py -3.11",
    [switch]$SkipTorch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
}

function Invoke-Cmd {
    param(
        [string]$Command,
        [string]$Description
    )
    Write-Host ""
    Write-Host "-> $Description" -ForegroundColor Green
    Write-Host "   $Command" -ForegroundColor DarkGray
    cmd /c $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Command"
    }
}

function Show-CommandSource {
    param([string]$Name)
    Write-Host ""
    Write-Host "Checking command: $Name" -ForegroundColor Yellow
    $items = Get-Command $Name -All -ErrorAction SilentlyContinue
    if ($items) {
        $items | Select-Object CommandType, Name, Source, Version | Format-Table -AutoSize
    } else {
        Write-Host "Not found: $Name" -ForegroundColor Red
    }
}

Write-Section "NBS Analytics Windows GPU Setup"
Write-Host "Project root: $ProjectRoot"
Write-Host "Torch CUDA target: $TorchCuda"
Write-Host "Python command: $PythonCommand"

Write-Section "Toolchain Discovery"
Show-CommandSource "python"
Show-CommandSource "pip"
Show-CommandSource "git"
Show-CommandSource "nvidia-smi"

Invoke-Cmd "$PythonCommand --version" "Verify Python"
Invoke-Cmd "$PythonCommand -m pip --version" "Verify pip"

if ($TorchCuda -ne "cpu") {
    $nvidia = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
    if (-not $nvidia) {
        throw "nvidia-smi not found. Install or update the NVIDIA driver first, or rerun with -TorchCuda cpu."
    }
    Invoke-Cmd "nvidia-smi" "Verify NVIDIA driver and GPU visibility"
}

Write-Section "Virtual Environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Invoke-Cmd "$PythonCommand -m venv .venv" "Create .venv"
} else {
    Write-Host ".venv already exists. Reusing it." -ForegroundColor Green
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment python not found: $VenvPython"
}

Invoke-Cmd "`"$VenvPython`" -m pip install --upgrade pip setuptools wheel" "Upgrade packaging tools"
Invoke-Cmd "`"$VenvPython`" -m pip install -r requirements.txt" "Install NBS Analytics requirements"

if (-not $SkipTorch) {
    Write-Section "PyTorch Install"
    $TorchIndex = switch ($TorchCuda) {
        "cu128" { "https://download.pytorch.org/whl/cu128" }
        "cu126" { "https://download.pytorch.org/whl/cu126" }
        "cpu" { "https://download.pytorch.org/whl/cpu" }
    }
    Invoke-Cmd "`"$VenvPython`" -m pip install torch torchvision torchaudio --index-url $TorchIndex" "Install PyTorch ($TorchCuda)"
} else {
    Write-Host "Skipping PyTorch install because -SkipTorch was provided." -ForegroundColor Yellow
}

Write-Section "Verification"
Invoke-Cmd "`"$VenvPython`" scripts\verify_windows_gpu.py" "Run Windows GPU verification"
if ($TorchCuda -ne "cpu") {
    Invoke-Cmd "`"$VenvPython`" -c `"import torch; raise SystemExit(0 if torch.cuda.is_available() else 2)`"" "Require torch.cuda.is_available() for CUDA setup"
}
Invoke-Cmd "`"$VenvPython`" scripts\validate_business_calendar.py" "Validate business calendar"
Invoke-Cmd "`"$VenvPython`" -m py_compile app.py forecasting.py pipeline.py business_calendar.py visuals.py scripts\prewarm_ai_cache.py scripts\verify_windows_gpu.py" "Compile core Python files"

Write-Section "Done"
Write-Host "Setup completed. Start dashboard with:" -ForegroundColor Green
Write-Host "  .\啟動NBS系統_windows.bat"
Write-Host ""
Write-Host "Inspect AI cache with:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status"
