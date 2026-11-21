#requires -version 5.1
<#
  qwensese model downloader (Windows / PowerShell 5.1+)

  This file is intentionally pure ASCII: Windows PowerShell 5.1 reads .ps1
  files without a BOM using the system ANSI code page, so non-ASCII text in a
  UTF-8-no-BOM script would be garbled (and can even break parsing).

  Downloads a GGUF from HuggingFace into <repo root>\models\gguf\.
  Resume is supported via curl -C -; re-run the script after an interruption.

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1 -Quant UD-Q4_K_XL
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1 -Repo owner/repo
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1 -Dest "some\dir\gguf"
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [ValidateSet('UD-IQ4_XS', 'UD-DW-Q4_K_M', 'UD-Q4_K_XL', 'UD-Q3_K_XL', 'UD-Q5_K_XL')]
    [string]$Quant = 'UD-IQ4_XS',

    [string]$Repo = 'huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF',

    [string]$Dest = '',

    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# ---- repo root and output path -----------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Dest) { $Dest = Join-Path $Root 'models\gguf' }

$FilePrefix = 'Huihui-Qwen3.8-27B-abliterated'
$FileName = "$FilePrefix-$Quant.gguf"
$Url = 'https://huggingface.co/' + $Repo + '/resolve/main/' + $FileName + '?download=true'
$OutFile = Join-Path $Dest $FileName

function Write-Step([string]$msg) { Write-Host "[download] $msg" -ForegroundColor Cyan }
function Write-Note([string]$msg) { Write-Host "[note]     $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "[error]    $msg" -ForegroundColor Red }

Write-Step "repo  : $Repo"
Write-Step "quant : $Quant"
Write-Step "target: $OutFile"
Write-Step "url   : $Url"

if ($Repo -notmatch '^[^/\s]+/[^/\s]+$') {
    Write-Note 'Repo should look like "owner/repo", e.g. huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF.'
}
if ($Quant -eq 'UD-IQ4_XS') {
    Write-Note 'UD-IQ4_XS is about 13.4 GB; an interrupted download resumes on the next run (-C -).'
}

if ($DryRun) {
    Write-Note '-DryRun: printing the resolved values only, nothing is downloaded.'
    exit 0
}

# ---- curl ---------------------------------------------------------------
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    Write-Fail 'curl.exe not found. Windows 10 1803+ ships it; on older systems download the GGUF manually with a browser.'
    exit 1
}

# ---- create the directory, report resume progress ----------------------
if (-not (Test-Path -LiteralPath $Dest)) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Write-Step "created directory: $Dest"
}
if (Test-Path -LiteralPath $OutFile) {
    $have = (Get-Item -LiteralPath $OutFile).Length
    if ($have -gt 0) {
        Write-Note ("found an existing file ({0} MB), resuming from it." -f [math]::Round($have / 1MB, 1))
    }
}

# ---- download -----------------------------------------------------------
Write-Step 'start: curl -L --ssl-no-revoke --retry 10 --retry-delay 5 -C - ...'
$curlArgs = @(
    '-L',
    '--ssl-no-revoke',
    '--retry', '10',
    '--retry-delay', '5',
    '-C', '-',
    '--fail',
    '--show-error',
    '-o', $OutFile,
    $Url
)
& $curl.Source @curlArgs
$rc = $LASTEXITCODE
if ($null -eq $rc) { $rc = 0 }

if ($rc -ne 0) {
    Write-Fail "curl exited with code $rc; the download did not finish."
    if ($rc -eq 22) {
        Write-Note '22 = HTTP error (e.g. 404): check that -Repo and -Quant point to an existing file on HuggingFace.'
    }
    if ($rc -eq 33) {
        Write-Note '33 = the server does not support resume, or the local file is already complete; delete the old file and retry.'
    }
    Write-Note "the partial file is kept at $OutFile; re-run this script to resume (-C -)."
    exit $rc
}

if (-not (Test-Path -LiteralPath $OutFile)) {
    Write-Fail 'curl reported success but the target file does not exist; check disk space and directory permissions.'
    exit 1
}

$size = (Get-Item -LiteralPath $OutFile).Length
$sizeGb = [math]::Round($size / 1GB, 2)
Write-Step ("done: {0} ({1} GB)" -f $OutFile, $sizeGb)
Write-Host ''
Write-Step "next: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1 -Quant $Quant"
