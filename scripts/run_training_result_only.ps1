param(
    [Parameter(Mandatory = $true)]
    [string]$Command,
    [string]$RunName = "manual_gpu_experiment",
    [string]$OutputDir = "reports",
    [int]$TimeoutMinutes = 0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$StartedAt = Get-Date
$SafeRunName = ($RunName -replace '[^A-Za-z0-9_.-]', '_')
$LogPath = Join-Path $OutputDir "$SafeRunName.log"
$SummaryPath = Join-Path $OutputDir "windows_gpu_training_summary.json"

Write-Host "NBS result-only training runner" -ForegroundColor Cyan
Write-Host "Run name: $RunName"
Write-Host "Command: $Command"
Write-Host "Log path: $LogPath"
Write-Host "Summary path: $SummaryPath"

$ProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
$ProcessInfo.FileName = "cmd.exe"
$ProcessInfo.Arguments = "/c `"$Command > `"$LogPath`" 2>&1`""
$ProcessInfo.WorkingDirectory = "$ProjectRoot"
$ProcessInfo.UseShellExecute = $false
$ProcessInfo.CreateNoWindow = $true

$Process = New-Object System.Diagnostics.Process
$Process.StartInfo = $ProcessInfo
$null = $Process.Start()

if ($TimeoutMinutes -gt 0) {
    $Exited = $Process.WaitForExit($TimeoutMinutes * 60 * 1000)
    if (-not $Exited) {
        $Process.Kill()
        $TimedOut = $true
    } else {
        $TimedOut = $false
    }
} else {
    $Process.WaitForExit()
    $TimedOut = $false
}

$FinishedAt = Get-Date
$LogText = if (Test-Path $LogPath) { Get-Content -Path $LogPath -Raw -Encoding UTF8 } else { "" }
$LogTail = if ($LogText.Length -gt 4000) { $LogText.Substring($LogText.Length - 4000) } else { $LogText }

$Summary = [ordered]@{
    run_name = $RunName
    command = $Command
    project_root = "$ProjectRoot"
    started_at = $StartedAt.ToString("o")
    finished_at = $FinishedAt.ToString("o")
    elapsed_seconds = [math]::Round(($FinishedAt - $StartedAt).TotalSeconds, 2)
    exit_code = if ($TimedOut) { -1 } else { $Process.ExitCode }
    timed_out = $TimedOut
    log_path = (Resolve-Path $LogPath).Path
    log_tail = $LogTail
}

$Summary | ConvertTo-Json -Depth 6 | Set-Content -Path $SummaryPath -Encoding UTF8

Write-Host ""
Write-Host "Run complete. Review summary only:" -ForegroundColor Green
Write-Host "  $SummaryPath"
Write-Host "Full log:" -ForegroundColor Green
Write-Host "  $LogPath"

if ($TimedOut) {
    exit 124
}
exit $Process.ExitCode
