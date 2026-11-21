#requires -version 5.1
<#
  qwensese model importer (Windows / PowerShell 5.1+)

  This file is intentionally pure ASCII: Windows PowerShell 5.1 reads .ps1
  files without a BOM using the system ANSI code page, so non-ASCII text in a
  UTF-8-no-BOM script would be garbled (and can even break parsing). All
  Chinese content (persona card, system prompt, style guide, scale labels) is
  read at runtime from the UTF-8 .md files in this repo.

  Builds a Modelfile and runs "ollama create" so the model can chat on its own:
    - SYSTEM = prompts\system-core.md + the persona card fields of
      personas\persona.md + the current scale level + prompts\style-guide.md
      (the assembly mirrors web\app.py build_system; -NoStyle skips the guide)
    - PARAMETER num_ctx 16384 and temperature 0.8 by default
    - the Modelfile is generated in the OS temp directory; no repo file is moved

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1 -Name qwensese-27b
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1 -NoStyle
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1 -Vision
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1 -DryRun

  Note: Ollama must already be running, and its model store must match the one
        scripts\start.ps1 uses (<repo root>\models), otherwise start.ps1 may not
        find the model created here.
#>

[CmdletBinding()]
param(
    [string]$Name = 'qwensese-27b',

    [ValidateSet('UD-IQ4_XS', 'UD-DW-Q4_K_M', 'UD-Q4_K_XL', 'UD-Q3_K_XL', 'UD-Q5_K_XL')]
    [string]$Quant = 'UD-IQ4_XS',

    [switch]$NoStyle,
    [switch]$Vision,
    [double]$Temperature = 0.8,
    [int]$NumCtx = 16384,

    [string]$GgufPath = '',
    [string]$ModelfileOut = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# ---- paths (all derived from the repo root) -----------------------------
$Root        = Split-Path -Parent $PSScriptRoot
$PersonaMd   = Join-Path $Root 'personas\persona.md'
$CoreMd      = Join-Path $Root 'prompts\system-core.md'
$StyleMd     = Join-Path $Root 'prompts\style-guide.md'
$GgufDir     = Join-Path $Root 'models\gguf'
$FilePrefix  = 'Huihui-Qwen3.8-27B-abliterated'
$OllamaUrl   = 'http://127.0.0.1:11434'
$LocalOllama = Join-Path $Root 'ollama\ollama.exe'

# CJK punctuation used by the web UI formatting, built from code points so
# this script stays pure ASCII: U+3010 / U+3011 are the fullwidth brackets.
$OpenBracket  = [string][char]0x3010
$CloseBracket = [string][char]0x3011
# The persona card field name for the content scale is U+5C3A + U+5EA6.
$ScaleField = ([string][char]0x5C3A) + ([string][char]0x5EA6)

function Write-Step([string]$msg) { Write-Host "[import] $msg" -ForegroundColor Cyan }
function Write-Note([string]$msg) { Write-Host "[note]   $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "[error]  $msg" -ForegroundColor Red }

function Read-Utf8Text([string]$Path) {
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

# Like web\app.py clean_doc: drop quote-block helper lines and H1 headings.
function Remove-DocLines([string]$Text) {
    $keep = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -match '^\s*>\s*') { continue }
        if ($line -match '^#\s') { continue }
        $keep.Add($line)
    }
    return (($keep -join "`n").Trim())
}

# Like web\app.py parse_persona: split the card on "## field" headings.
function Get-CardFields([string]$Text) {
    $noComments = [regex]::Replace($Text, '(?s)<!--.*?-->', '')
    $fields = [ordered]@{}
    $cur = $null
    $buf = New-Object System.Collections.Generic.List[string]
    foreach ($line in ($noComments -split "`r?`n")) {
        $m = [regex]::Match($line, '^##\s+(.+?)\s*$')
        if ($m.Success) {
            if ($null -ne $cur) { $fields[$cur] = (($buf -join "`n").Trim()) }
            $cur = $m.Groups[1].Value.Trim()
            $buf = New-Object System.Collections.Generic.List[string]
            continue
        }
        if ($null -ne $cur) { $buf.Add($line) }
    }
    if ($null -ne $cur) { $fields[$cur] = (($buf -join "`n").Trim()) }
    return $fields
}

function Find-OllamaExe {
    if (Test-Path -LiteralPath $LocalOllama) { return $LocalOllama }
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return [string]$cmd.Source }
    return $null
}

function Test-OllamaUp {
    try {
        $null = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Write-Modelfile([string]$Text) {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('qwensese-modelfile-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $file = Join-Path $dir 'Modelfile'
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($file, $Text, $utf8NoBom)
    return $file
}

function Remove-TempModelfile([string]$File) {
    if ($File -and (Test-Path -LiteralPath $File)) {
        Remove-Item -LiteralPath (Split-Path -Parent $File) -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---- 0. project files ---------------------------------------------------
if (-not (Test-Path -LiteralPath $PersonaMd)) {
    Write-Fail "personas\persona.md not found (repo root derived from the script location: $Root). Keep this script in scripts\ inside the repo."
    exit 1
}
if (-not (Test-Path -LiteralPath $CoreMd)) {
    Write-Fail 'prompts\system-core.md not found; cannot build the SYSTEM block.'
    exit 1
}
if (-not $NoStyle -and -not (Test-Path -LiteralPath $StyleMd)) {
    Write-Note 'prompts\style-guide.md not found; continuing as if -NoStyle was given.'
    $NoStyle = $true
}

if (-not $GgufPath) { $GgufPath = Join-Path $GgufDir "$FilePrefix-$Quant.gguf" }
if (-not (Test-Path -LiteralPath $GgufPath)) {
    Write-Fail "model file not found: $GgufPath"
    Write-Note "download it first: scripts\download-model.ps1 -Quant $Quant   (or point -GgufPath to an existing .gguf)"
    exit 1
}
$GgufFull = (Resolve-Path -LiteralPath $GgufPath).Path
$GgufSizeGb = [math]::Round((Get-Item -LiteralPath $GgufFull).Length / 1GB, 2)

# ---- 0.5 optional vision projector -------------------------------------
$ProjectorFull = $null
if ($Vision) {
    $ggufDirPath = Split-Path -Parent $GgufFull
    $candidate = Get-ChildItem -LiteralPath $ggufDirPath -Filter 'mmproj*.gguf' -File -ErrorAction SilentlyContinue |
                 Select-Object -First 1
    if ($candidate) {
        $ProjectorFull = $candidate.FullName
        Write-Step "vision projector: $($candidate.Name) (added as a second FROM line)"
    } else {
        Write-Note '-Vision was given but no mmproj*.gguf was found next to the model; importing as a text-only model.'
    }
}

# ---- 1. build the SYSTEM block (mirrors web\app.py build_system) --------
$coreText = Read-Utf8Text $CoreMd
$cardText = Read-Utf8Text $PersonaMd
$fields   = Get-CardFields $cardText

$sys = New-Object System.Collections.Generic.List[string]
$sys.Add((Remove-DocLines $coreText))
$sys.Add('')
$sys.Add('======== CHARACTER CARD ========')
$sys.Add('')
foreach ($key in $fields.Keys) {
    $value = ([string]$fields[$key]).Trim()
    if ($value -eq '') { continue }
    $sys.Add($OpenBracket + $key + $CloseBracket)
    $sys.Add($value)
    $sys.Add('')
}

# current scale level: the number from the card field, described by the table
# row of the same level in prompts\system-core.md (single source of truth)
$level = 2
if ($fields.Contains($ScaleField)) {
    $digits = [regex]::Replace([string]$fields[$ScaleField], '[^0-9]', '')
    $parsedLevel = 0
    if ($digits -ne '' -and [int]::TryParse($digits, [ref]$parsedLevel)) { $level = $parsedLevel }
}
$level = [Math]::Max(0, [Math]::Min(3, $level))

$levelLabel = ''
$levelRow = [regex]::Match($coreText, '(?m)^\|\s*' + $level + '\s*\|\s*(.+?)\s*\|\s*$')
if ($levelRow.Success) { $levelLabel = $levelRow.Groups[1].Value.Trim() }

$sys.Add('======== CURRENT SCALE LEVEL ========')
if ($levelLabel -ne '') {
    $sys.Add(('level {0}: {1}' -f $level, $levelLabel))
} else {
    $sys.Add(('level {0} (see the scale table in prompts\system-core.md)' -f $level))
}
$sys.Add('')

if (-not $NoStyle) {
    $sys.Add('======== STYLE GUIDE ========')
    $sys.Add((Remove-DocLines (Read-Utf8Text $StyleMd)))
}

$systemBlock = ($sys -join "`n").Trim()
if ($systemBlock -match '"""') {
    Write-Fail 'the SYSTEM block contains a triple quote ("""), which would break the Modelfile format; fix the .md files first.'
    exit 1
}

# ---- 2. build the Modelfile text ---------------------------------------
$fromPath = $GgufFull -replace '\\', '/'
$tempLine = $Temperature.ToString([System.Globalization.CultureInfo]::InvariantCulture)

$mf = New-Object System.Collections.Generic.List[string]
$mf.Add("FROM $fromPath")
if ($ProjectorFull) { $mf.Add("FROM $($ProjectorFull -replace '\\', '/')") }
$mf.Add("PARAMETER num_ctx $NumCtx")
$mf.Add("PARAMETER temperature $tempLine")
$mf.Add('SYSTEM """')
$mf.Add($systemBlock)
$mf.Add('"""')
$mfText = ($mf -join "`n") + "`n"

$styleDesc = 'without style-guide.md'
if (-not $NoStyle) { $styleDesc = 'with style-guide.md' }

Write-Step "name   : $Name"
Write-Step "gguf   : $GgufFull ($GgufSizeGb GB)"
Write-Step "system : $($systemBlock.Length) chars (system-core.md + persona card fields, $styleDesc)"
Write-Step "params : num_ctx=$NumCtx temperature=$tempLine"

if ($ModelfileOut) {
    $outDir = Split-Path -Parent $ModelfileOut
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ModelfileOut, $mfText, $utf8NoBom)
    Write-Step "Modelfile written to: $ModelfileOut"
}

# ---- 3. -DryRun: show the Modelfile, never call ollama ------------------
if ($DryRun) {
    $mfPath = Write-Modelfile $mfText
    Write-Host ''
    Write-Host '----- Modelfile preview (-DryRun: ollama create is NOT executed) -----'
    Write-Host $mfText
    Write-Host '----- end of preview -----'
    Write-Step "temporary Modelfile: $mfPath"
    Write-Note 'a real run deletes the temp directory and passes the same content to ollama create.'
    Remove-TempModelfile $mfPath
    exit 0
}

# ---- 4. Ollama reachable? ----------------------------------------------
if (-not (Test-OllamaUp)) {
    Write-Fail "Ollama is not running ($OllamaUrl is unreachable); ollama create cannot run."
    Write-Host ''
    Write-Note 'start Ollama first (the launcher below keeps the model store at <repo root>\models):'
    Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1'
    Write-Host ''
    Write-Note 'or start it manually in PowerShell with the same model store:'
    Write-Host "    `$env:OLLAMA_MODELS = '$Root\models'"
    Write-Host '    ollama serve'
    Write-Note 'if Ollama is already running but without OLLAMA_MODELS pointing at <repo root>\models, the model lands elsewhere and start.ps1 may not find it.'
    exit 1
}

$ollamaExe = Find-OllamaExe
if (-not $ollamaExe) {
    Write-Fail 'Ollama is running but the ollama command was not found (no ollama\ollama.exe, no ollama on PATH); cannot run ollama create.'
    exit 1
}

# ---- 5. ollama create ---------------------------------------------------
$mfPath = Write-Modelfile $mfText
Write-Step "temporary Modelfile: $mfPath"
Write-Step "running: ollama create $Name -f <temporary Modelfile>"

$rc = 0
try {
    & $ollamaExe create $Name -f $mfPath
    $rc = $LASTEXITCODE
} finally {
    Remove-TempModelfile $mfPath
}
if ($null -eq $rc) { $rc = 0 }

if ($rc -ne 0) {
    Write-Fail "ollama create failed (exit code $rc)."
    Write-Note 'common causes: the Ollama model store differs from what start.ps1 expects, not enough disk space, corrupt gguf.'
    Write-Note 'run ollama create manually to see the full output; if a model with the same name exists, try ollama rm first.'
    exit $rc
}

# ---- 6. verify + next steps --------------------------------------------
try {
    $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 3 -ErrorAction Stop
    $present = $false
    foreach ($m in @($tags.models)) {
        $n = [string]$m.name
        if ($n -eq $Name -or $n -like ($Name + ':latest')) { $present = $true }
    }
    if ($present) {
        Write-Step "done: model '$Name' is now listed by Ollama."
    } else {
        Write-Note "ollama create reported success, but '$Name' was not found in /api/tags; confirm with ollama list."
    }
} catch {
    Write-Note "could not query the model list ($($_.Exception.Message)); confirm with ollama list."
}

Write-Host ''
Write-Step 'next steps:'
Write-Host '    1) start the page: double-click scripts\start.bat, or run:'
Write-Host '       powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1'
Write-Host "    2) make sure web\config.json has ""model"": ""$Name"" (that is the default), port 8717 by default"
Write-Host '    3) after editing personas\persona.md or prompts\*.md, re-run this script so the new SYSTEM takes effect'
