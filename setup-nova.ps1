# ============================================================================
# Nova Agent Setup Script (Windows) — THE dev-environment entry point.
# ============================================================================
# Sets up the pm-managed development environment from a fresh clone:
#   1. Stage the pinned uv from pm/lock.json (sha256-verified, into the pm
#      store slot) - pm needs uv to bootstrap, so it cannot stage uv itself.
#   2. Use uv to install and locate bootstrap Python, then let uv exit.
#      Run `python -m pm.cli install` directly so PM can safely replace uv.
#      PM owns the final interpreter, tool store, and dependency generation.
#   3. Point you at `.\activate.ps1` - the venv-style way to put the pm env
#      (PATH + tool vars) into your current session.
# ============================================================================
# Setup and activation both prepare the isolated test interpreter; installers
# invoke pm.cli directly and do not select it. -TestExtras overrides coverage.
param([switch]$RuntimeOnly, [string]$TestExtras = '')
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host 'NOVA by Nova Labs — Setup' -ForegroundColor Cyan
Write-Host ''

$repo = $PSScriptRoot
$lockPath = Join-Path $repo 'pm/lock.json'
if (-not (Test-Path $lockPath)) { throw 'pm/lock.json not found' }
$lock = Get-Content -Raw $lockPath | ConvertFrom-Json

$machineArch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').PROCESSOR_ARCHITECTURE
$arch = if ($machineArch -eq 'ARM64') { 'arm64' } else { 'x64' }
$target = "win32-$arch"

# ---------------------------------------------------------------------------
# Stage the pinned uv from pm/lock.json into the pm store slot
# ---------------------------------------------------------------------------

$uvPin = $lock.packages.uv
if (-not $uvPin) { throw 'no uv pin in pm/lock.json' }
$artifact = $uvPin.artifacts.$target
if (-not $artifact) { $artifact = $uvPin.artifacts.any }
if (-not $artifact) { throw "no uv artifact for $target" }

$pyPin = $lock.packages.python
$pyVersion = if ($pyPin) { ($pyPin.version -split '\+')[0] -replace '^(\d+\.\d+).*', '$1' } else { '3.11' }

$store = if ($env:NOVA_RUNTIME_DIR) { $env:NOVA_RUNTIME_DIR } else { Join-Path $HOME '.nova/tools' }
$entry = Join-Path $store "uv-$($uvPin.version)-$target"
$uv = Join-Path $entry 'uv.exe'

if (Test-Path $uv) {
    Write-Host ("pinned uv found: " + (& $uv --version)) -ForegroundColor Green
} else {
    Write-Host "Staging pinned uv $($uvPin.version) ($target) into the pm store..." -ForegroundColor Cyan
    $url = $artifact.url
    $sha = $artifact.sha256
    $storeDir = Split-Path $entry -Parent
    New-Item -ItemType Directory -Force -Path $storeDir | Out-Null
    $tmp = New-TemporaryFile
    $tmpDir = $tmp.DirectoryName + '\bootstrap-' + [System.IO.Path]::GetRandomFileName()
    Remove-Item $tmp
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    $archive = Join-Path $tmpDir ($url -split '/')[-1]
    function Fetch-Pinned {
        param($url, $out)
        try {
            Invoke-WebRequest -Uri $url -OutFile $out -ErrorAction Stop
            return $true
        } catch {
            if ($_.Exception.Status -in @('ReceiveTimeout','SendTimeout','ConnectFailure','ProtocolError','ProxyUnavailable','ServerError','ServerOverload')) { return $false }
            throw
        }
    }
    if (-not (Fetch-Pinned $url $archive)) {
        $tried = $url
        if ($m = (Resolve-Mirror $sha)) { $tried = "$tried or $m" }
        Write-Error "failed to download pinned uv from $tried"
        exit 1
    }
    $got = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $sha.ToLower()) {
        Write-Error "sha256 mismatch for uv (got $got, pinned $sha)"
        exit 1
    }
    $tree = Join-Path $tmpDir 'tree'
    if ($archive -like '*.zip') { Expand-Archive -Path $archive -DestinationPath $tree -Force }
    else { tar -xzf $archive -C $tmpDir/tree }
    # flatten a single wrapping dir (uv tarballs ship uv-<triple>/uv.exe).
    $innerCount = 0
    $inner = $null
    foreach ($child in (Get-ChildItem -Path "$tmpDir/tree" -Force -Recurse -Depth 1)) {
        if ($child.PSIsContainer) { $inner = $child.FullName; $innerCount++ }
    }
    if ($innerCount -eq 1 -and (Test-Path $inner)) {
        Move-Item $inner $tmpDir/entry -Force
    } else {
        Move-Item $tmpDir/tree $tmpDir/entry -Force
    }
    Remove-Item $entry -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $tmpDir/entry $entry -Force
    Write-Host "uv installed: " + (& $uv --version) -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Delegate to pm: python + venv + tool store + hash-verified venv sync
# ---------------------------------------------------------------------------

Write-Host "Installing python + tools + dependencies via pm (hash-verified via uv.lock)..." -ForegroundColor Cyan
Write-Host "(first run on a fresh checkout can take 1-5 minutes)" -ForegroundColor Cyan

$pyRequest = $pyVersion
$uv python install --no-bin --no-registry $pyVersion
$bootPy = & $uv python find --managed-python $pyVersion

$pmArgs = @()
if ($RuntimeOnly) { $pmArgs += '--trust-recorded' }
$pmArgs += $TestExtras
if (-not (& $bootPy -m pm.cli install @pmArgs)) {
    Write-Error "pm install failed — see output above."
    exit 1
}
Write-Host "Tools + dependencies installed (hash-verified via pm + uv.lock)" -ForegroundColor Green

if ($RuntimeOnly) { exit 0 }

# ---------------------------------------------------------------------------
# Environment file
# ---------------------------------------------------------------------------

if (-not (Test-Path .env)) {
    if (Test-Path .env.example) {
        Copy-Item .env.example .env
        # .env holds API keys — restrict to owner-only access
        $acl = Get-Acl .env
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl .env $acl
        Write-Host "Created .env from template" -ForegroundColor Green
    }
} else {
    # Tighten an existing .env's perms in case it was created elsewhere
    $acl = Get-Acl .env
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.SetAccessRule($rule)
    Set-Acl .env $acl
    Write-Host ".env exists" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Publish user-facing launchers
# ---------------------------------------------------------------------------

Write-Host "Setting up Nova and compatibility launchers..." -ForegroundColor Cyan

$binDir = Join-Path $HOME '.local/bin'
$bootPyArgs = '-I', '-X', 'utf8', 'nova_cli/_launchers.py', $binDir
if (-not (& $bootPy @bootPyArgs)) {
    Write-Error "launcher publication failed"
    exit 1
}
Write-Host "Published compatibility launchers in $binDir" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Seed bundled skills into ~/.nova/skills/
# ---------------------------------------------------------------------------

$novaSkillsDir = Join-Path (if ($env:NOVA_HOME) { $env:NOVA_HOME } else { Join-Path $HOME '.nova' }) 'skills'
New-Item -ItemType Directory -Force -Path $novaSkillsDir | Out-Null

Write-Host ""
Write-Host "Syncing bundled skills to ~/.nova/skills/ ..."
if (& $bootPy -m tools.skills_sync 2>$null) {
    Write-Host "Skills synced" -ForegroundColor Green
} else {
    # Fallback: copy if sync script fails (missing deps, etc.)
    if (Test-Path (Join-Path $repo 'skills')) {
        Copy-Item (Join-Path $repo 'skills\*') $novaSkillsDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Skills copied" -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "  1. Activate the dev environment (venv-style, in THIS session):"
Write-Host "     .\activate.ps1"
Write-Host ""
Write-Host "  2. Run the setup wizard to configure API keys:"
Write-Host "     nova setup"
Write-Host ""
Write-Host "  3. Start chatting:"
Write-Host "     nova"
Write-Host ""
Write-Host "Other commands:"
Write-Host "  nova pm install       # Re-run the tool + dependency install"
Write-Host "  nova status           # Check configuration"
Write-Host "  nova doctor           # Diagnose issues"
Write-Host ""
