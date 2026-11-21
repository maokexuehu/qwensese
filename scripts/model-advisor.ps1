# qwensese - hardware detection and Qwen model selection (Windows wrapper)
#
# This is a thin wrapper around scripts/model_advisor.py. All the real logic
# lives in Python so that Linux / macOS users get exactly the same behavior:
#
#   python scripts/model_advisor.py
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -List
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Json
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Check
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Download qwen3-8b -Quant Q4_K_M
#   powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Download huihui-27b -DryRun
#
# Keep this file ASCII-only: Windows PowerShell 5.1 reads scripts without a BOM
# using the system ANSI code page, so non-ASCII text would be garbled.

[CmdletBinding()]
param(
    [string]$Download = '',
    [string]$Quant = '',
    [string]$Dest = '',
    [switch]$List,
    [switch]$Check,
    [switch]$Json,
    [switch]$NoUncensored,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Advisor = Join-Path $Root 'scripts\model_advisor.py'
if (-not (Test-Path $Advisor)) {
    Write-Host "[error] not found: $Advisor" -ForegroundColor Red
    exit 2
}

# Find a Python 3.10+ interpreter (no third-party packages required).
$Python = $null
foreach ($name in @('python', 'python3', 'py')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
        $Python = $cmd.Source
        break
    }
}
if (-not $Python) {
    Write-Host '[error] Python 3.10+ was not found in PATH.' -ForegroundColor Red
    Write-Host '        Install Python from https://www.python.org/downloads/ and try again.'
    exit 2
}

$argv = @()
if ($List) { $argv += '--list' }
if ($Check) { $argv += '--check' }
if ($Json) { $argv += '--json' }
if ($NoUncensored) { $argv += '--no-uncensored' }
if ($DryRun) { $argv += '--dry-run' }
if ($Dest -ne '') { $argv += @('--dest', $Dest) }
if ($Download -ne '') { $argv += @('--download', $Download) }
if ($Quant -ne '') { $argv += @('--quant', $Quant) }

& $Python $Advisor @argv
exit $LASTEXITCODE
