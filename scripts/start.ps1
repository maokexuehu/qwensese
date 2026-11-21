#requires -version 5.1
<#
  qwensese launcher (Windows / PowerShell 5.1+)

  This file is intentionally pure ASCII: Windows PowerShell 5.1 reads .ps1
  files that have no BOM using the system ANSI code page, so non-ASCII text in
  a UTF-8-no-BOM script would be garbled (and can even break parsing). All
  Chinese text this tool needs is read at runtime from the UTF-8 .md files.

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 -Lan
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 -Check

  What it does:
    1. derives the repo root from this script's own location (no hard-coded paths)
    2. creates web\config.json from web\config.example.json on the first real run
    3. reads the port from web\config.json (default 8717)
    4. if Ollama is not reachable: uses ollama\ollama.exe when present, otherwise
       ollama from PATH; sets OLLAMA_* env vars, starts it in the background and
       writes logs\ollama.out.log / logs\ollama.err.log
    5. runs web\app.py in the foreground (-Lan is forwarded as app.py --lan)

  -Check only inspects the environment (read only, starts nothing) and exits.
#>

[CmdletBinding()]
param(
    [switch]$Lan,
    [switch]$Check
)

$ErrorActionPreference = 'Continue'

# ---- repo root = parent of scripts\ -------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$WebDir      = Join-Path $Root 'web'
$LogsDir     = Join-Path $Root 'logs'
$ConfigPath  = Join-Path $WebDir 'config.json'
$ExamplePath = Join-Path $WebDir 'config.example.json'
$AppPath     = Join-Path $WebDir 'app.py'
$LocalOllama = Join-Path $Root 'ollama\ollama.exe'
$OllamaUrl   = 'http://127.0.0.1:11434'

function Write-Step([string]$msg) { Write-Host "[start] $msg" -ForegroundColor Cyan }
function Write-Note([string]$msg) { Write-Host "[note]  $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "[error] $msg" -ForegroundColor Red }

if (-not (Test-Path -LiteralPath $AppPath)) {
    Write-Fail "web\app.py not found (repo root derived from the script location: $Root). Keep this script in scripts\ inside the repo."
    exit 1
}

function Test-Ollama {
    try {
        $null = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-ConfigInfo {
    $info = [pscustomobject]@{
        Exists = $false
        Valid  = $false
        Port   = 8717
        Model  = 'qwensese-27b'
    }
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return $info }
    $info.Exists = $true
    try {
        $cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $cfg) {
            if ($null -ne $cfg.port) {
                $parsedPort = 0
                if ([int]::TryParse([string]$cfg.port, [ref]$parsedPort) -and $parsedPort -gt 0 -and $parsedPort -lt 65536) {
                    $info.Port = $parsedPort
                }
            }
            if ($cfg.model) { $info.Model = [string]$cfg.model }
            $info.Valid = $true
        }
    } catch {
        Write-Note "web\config.json could not be parsed, using defaults: $($_.Exception.Message)"
    }
    return $info
}

function Get-Python {
    $result = [pscustomobject]@{ Exe = $null; Prefix = @(); Version = '' }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $result.Exe = [string]$cmd.Source
    } else {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($py) {
            $result.Exe = [string]$py.Source
            $result.Prefix = @('-3')
        }
    }
    if ($result.Exe) {
        try {
            $prefixArgs = $result.Prefix
            $result.Version = (& $result.Exe @prefixArgs --version 2>&1 | Out-String).Trim()
            if ($result.Version -match 'Python\s+(\d+)\.(\d+)') {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
                    Write-Note "$($result.Version) detected; Python 3.10+ is recommended."
                }
            }
        } catch {
            Write-Note "could not query the Python version: $($_.Exception.Message)"
        }
    }
    return $result
}

function Get-OllamaModelNames {
    $names = @()
    if (-not (Test-Ollama)) { return $names }
    try {
        $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 2 -ErrorAction Stop
        if ($tags -and $tags.models) {
            $names = @($tags.models | ForEach-Object { [string]$_.name })
        }
    } catch { }
    return $names
}

$configInfo = Get-ConfigInfo
$python = Get-Python
$ollamaUp = Test-Ollama
$modelNames = @()
$modelPresent = $false
if ($ollamaUp) {
    $modelNames = @(Get-OllamaModelNames)
    foreach ($n in $modelNames) {
        if ($n -eq $configInfo.Model -or $n -like ($configInfo.Model + ':latest')) { $modelPresent = $true }
    }
}

# ---- -Check: read only, starts nothing ----------------------------------
if ($Check) {
    $configState = 'missing (the first real start copies web\config.example.json)'
    if ($configInfo.Exists) { $configState = "$ConfigPath (parsed: $($configInfo.Valid))" }
    $ollamaState = 'down'
    if ($ollamaUp) { $ollamaState = "up at $OllamaUrl" }

    Write-Host ''
    Write-Host '=== qwensese self check (-Check: read only, no service is started) ==='
    Write-Host ("  repo root      : {0}" -f $Root)
    Write-Host ("  web/app.py     : {0}" -f 'ok')
    Write-Host ("  web/config.json: {0}" -f $configState)
    Write-Host ("  port           : {0}" -f $configInfo.Port)
    Write-Host ("  model          : {0}" -f $configInfo.Model)
    Write-Host ("  python         : {0} {1}" -f $python.Exe, $python.Version)
    Write-Host ("  ollama         : {0}" -f $ollamaState)
    if ($ollamaUp) {
        $presentState = 'no'
        if ($modelPresent) { $presentState = 'yes' }
        Write-Host ("  model imported : {0}" -f $presentState)
        if ($modelNames.Count -gt 0) { Write-Host ("  models         : {0}" -f ($modelNames -join ', ')) }
    }
    Write-Host ''
    exit 0
}

# ---- ensure web\config.json exists (first real run) ---------------------
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    if (-not (Test-Path -LiteralPath $ExamplePath)) {
        Write-Fail 'web\config.json and web\config.example.json are both missing; the repo is incomplete.'
        exit 1
    }
    Copy-Item -LiteralPath $ExamplePath -Destination $ConfigPath
    Write-Step 'created web\config.json from web\config.example.json (edit it to change port / access_key)'
    $configInfo = Get-ConfigInfo
}
$VisitUrl = "http://127.0.0.1:$($configInfo.Port)/"

# ---- start Ollama when needed -------------------------------------------
if ($ollamaUp) {
    Write-Step "Ollama is already running at $OllamaUrl"
} else {
    $ollamaExe = $null
    if (Test-Path -LiteralPath $LocalOllama) {
        $ollamaExe = $LocalOllama
        Write-Step 'using the bundled ollama\ollama.exe'
    } else {
        $cmd = Get-Command ollama -ErrorAction SilentlyContinue
        if ($cmd) { $ollamaExe = [string]$cmd.Source }
    }

    if (-not $ollamaExe) {
        Write-Note 'Ollama is not running and no executable was found (no ollama\ollama.exe, no ollama on PATH).'
        Write-Note 'The web page will still start, but chat needs Ollama; install it, then run this script again (https://ollama.com/download).'
    } else {
        if (-not (Test-Path -LiteralPath $LogsDir)) {
            New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
        }
        $outLog = Join-Path $LogsDir 'ollama.out.log'
        $errLog = Join-Path $LogsDir 'ollama.err.log'

        # inherited by the Ollama process: keep its model store inside the repo
        $env:OLLAMA_MODELS            = Join-Path $Root 'models'
        $env:OLLAMA_HOST              = '127.0.0.1:11434'
        $env:OLLAMA_KEEP_ALIVE        = '30m'
        $env:OLLAMA_FLASH_ATTENTION   = '1'
        $env:OLLAMA_KV_CACHE_TYPE     = 'q8_0'
        $env:OLLAMA_NUM_PARALLEL      = '1'
        $env:OLLAMA_MAX_LOADED_MODELS = '1'

        Write-Step "starting Ollama in the background (models: $($env:OLLAMA_MODELS); logs: logs\ollama.out.log)"
        try {
            Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $Root `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
                -WindowStyle Hidden | Out-Null
        } catch {
            Write-Note "failed to start Ollama: $($_.Exception.Message)"
        }

        $ready = $false
        for ($i = 0; $i -lt 60; $i++) {
            if (Test-Ollama) { $ready = $true; break }
            Start-Sleep -Seconds 1
        }
        if ($ready) {
            Write-Step "Ollama is ready at $OllamaUrl"
        } else {
            Write-Note 'Ollama did not come up within 60 seconds; check logs\ollama.err.log. The web page will still start.'
        }
    }
}

# ---- friendly hint about the configured model ---------------------------
if (Test-Ollama) {
    $names = @(Get-OllamaModelNames)
    $found = $false
    foreach ($n in $names) {
        if ($n -eq $configInfo.Model -or $n -like ($configInfo.Model + ':latest')) { $found = $true }
    }
    if ($names.Count -eq 0) {
        Write-Note 'Ollama has no models yet. Run scripts\download-model.ps1, then scripts\import-model.ps1.'
    } elseif (-not $found) {
        Write-Note "Ollama does not have model '$($configInfo.Model)'. Run the download/import scripts, or change web\config.json model."
    }
}

# ---- python env + launch ------------------------------------------------
if (-not $python.Exe) {
    Write-Fail 'Python not found. Install Python 3.10+ and run this script again (https://www.python.org/downloads/).'
    exit 1
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

Write-Host ''
Write-Step "page: $VisitUrl (the browser should open automatically; if the port is busy the page may already be open)"
if ($Lan) {
    Write-Step 'LAN mode: listening on 0.0.0.0, other devices on the same Wi-Fi/LAN can open the page (app.py --lan).'
    Write-Note 'every device on the LAN can reach it; set access_key in web\config.json if you need a gate.'
}
Write-Step 'docs: docs\quickstart.md (first run), docs\configuration.md (port / sampling params / env vars)'
Write-Step 'closing this window stops the page (Ollama keeps running)'
Write-Host ''

$appArgs = @()
if ($Lan) { $appArgs += '--lan' }
$allArgs = @()
$allArgs += $python.Prefix
$allArgs += $AppPath
$allArgs += $appArgs

& $python.Exe @allArgs
$rc = $LASTEXITCODE
if ($null -eq $rc) { $rc = 0 }
exit $rc
