# Source this file to sync and apply the PM environment; deactivate restores it.
# Trusts the recorded tool digest. `nova pm install` re-checks the bytes.
# -TestExtras a,b selects runtime extras in the test environment (default: [all]).
param([string]$TestExtras = '')
$ErrorActionPreference = 'Stop'

$OutputEncoding = [System.Console]::OutputEncoding = [System.Console]::InputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

$repo = $PSScriptRoot
$bootstrapSaved = @{}
foreach ($key in @('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')) {
    $bootstrapSaved[$key] = [Environment]::GetEnvironmentVariable($key)
    Remove-Item "env:$key" -ErrorAction SilentlyContinue
}
try {
    # Run separately so setup's exit/failure cannot terminate the sourced shell.
    $shell = (Get-Process -Id $PID).Path
    $testArgs = @()
    if ($TestExtras) { $testArgs = @('-TestExtras', $TestExtras) }
    & $shell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$repo\setup-nova.ps1" -RuntimeOnly @testArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'activate: setup failed; shell environment unchanged' }
} finally {
    foreach ($key in $bootstrapSaved.Keys) {
        if ($null -eq $bootstrapSaved[$key]) { Remove-Item "env:$key" -ErrorAction SilentlyContinue }
        else { Set-Item "env:$key" $bootstrapSaved[$key] }
    }
}
if (Test-Path function:deactivate) { deactivate }
$py = $null
foreach ($candidate in @("$repo\.venv\Scripts\python.exe", "$repo\venv\Scripts\python.exe")) {
    if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
}
if (-not $py) {
    $roots = @($env:NOVA_RUNTIME_DIR, "$repo\..\tools")
    $homeRoot = if ($env:NOVA_HOME) { $env:NOVA_HOME } else { "$env:LOCALAPPDATA\nova" }
    $roots += (Join-Path $homeRoot 'tools')
    foreach ($root in $roots) {
        if (-not $root) { continue }
        foreach ($entry in @(Get-ChildItem -LiteralPath $root -Directory -Filter 'python-*' -ErrorAction SilentlyContinue)) {
            $candidate = Join-Path $entry.FullName 'python.exe'
            if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
        }
        if ($py) { break }
    }
}
if (-not $py) { throw 'activate: no bootstrap Python found; run setup-nova.ps1' }
$priorPath = $env:PYTHONPATH
$priorHome = $env:PYTHONHOME
try {
    $script = & $py -m pm.environments --format powershell
    $script | Out-String | Invoke-Expression
} finally {
    if ($null -ne $priorPath) { $env:PYTHONPATH = $priorPath } else { Remove-Item env:PYTHONPATH -ErrorAction SilentlyContinue }
    if ($null -ne $priorHome) { $env:PYTHONHOME = $priorHome } else { Remove-Item env:PYTHONHOME -ErrorAction SilentlyContinue }
}

if (Test-Path function:deactivate) { deactivate }

$script = & $py -m pm.environments --format powershell
$script | Out-String | Invoke-Expression

$__NOVA_WORKTREE = $repo
$__NOVA_WORKTREE_NAME = (& git -C $repo rev-parse --abbrev-ref HEAD 2>$null) ?? (Split-Path $repo -Leaf)
$__NOVA_SAVED_PS1 = $function:prompt
$__NOVA_SAVED_PROMPT_COMMAND = $null

function global:nova_prompt {
    $base = $__NOVA_SAVED_PS1
    if ($base -eq $null) { $base = '' }
    if (Test-Path -LiteralPath (Join-Path $__NOVA_WORKTREE '.git')) {
        $gitRoot = (git -C $__NOVA_WORKTREE rev-parse --show-toplevel 2>$null)
        if ($gitRoot -and (Convert-Path $gitRoot) -eq (Convert-Path $__NOVA_WORKTREE)) {
            $global:prompt = "($__NOVA_WORKTREE_NAME) $base"
        } else {
            $global:prompt = $base
        }
    } else {
        $global:prompt = $base
    }
}

$global:PROMPT_COMMAND = 'nova_prompt'
nova_prompt

function global:deactivate {
    $env:PATH = $__NOVA_SAVED_PATH
    foreach ($k in $__NOVA_KEY_LIST) {
        $was = Get-Variable "__NOVA_SAVED_$k" -ErrorAction SilentlyContinue
        $old = Get-Variable "__NOVA_PRIOR_$k" -ErrorAction SilentlyContinue
        if (-not $was) { Remove-Variable $k -ErrorAction SilentlyContinue }
        else { Set-Variable $k $old.Value -Scope Global }
        Remove-Variable "__NOVA_SAVED_$k", "__NOVA_PRIOR_$k" -ErrorAction SilentlyContinue
    }
    if ($__NOVA_SAVED_PS1 -eq $null) { Remove-Item function:prompt -ErrorAction SilentlyContinue }
    else { $function:prompt = $__NOVA_SAVED_PS1 }
    Remove-Variable __NOVA_SAVED_PATH, __NOVA_KEY_LIST, __NOVA_ACTIVATED, __NOVA_WORKTREE, __NOVA_WORKTREE_NAME, __NOVA_SAVED_PS1 -ErrorAction SilentlyContinue
    Remove-Item function:deactivate, function:nova, function:nova_prompt, function:_nova_worktree_here -ErrorAction SilentlyContinue
}

$py = $null
foreach ($candidate in @("$repo\.venv\Scripts\python.exe", "$repo\venv\Scripts\python.exe")) {
    if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
}
if (-not $py) {
    $roots = @($env:NOVA_RUNTIME_DIR, "$repo\..\tools")
    $homeRoot = if ($env:NOVA_HOME) { $env:NOVA_HOME } else { "$env:LOCALAPPDATA\nova" }
    $roots += (Join-Path $homeRoot 'tools')
    foreach ($root in $roots) {
        if (-not $root) { continue }
        foreach ($entry in @(Get-ChildItem -LiteralPath $root -Directory -Filter 'python-*' -ErrorAction SilentlyContinue)) {
            $candidate = Join-Path $entry.FullName 'python.exe'
            if (Test-Path -LiteralPath $candidate) { $py = $candidate; break }
        }
        if ($py) { break }
    }
}
if (-not $py) { throw 'activate: no bootstrap Python found; run setup-nova.ps1' }
$script = & $py -m pm.environments --format powershell
$script | Out-String | Invoke-Expression

# Snapshot for deactivate
$__NOVA_SAVED_PATH = $env:PATH
$__NOVA_KEY_LIST = $script | Select-String -Pattern '^export ([A-Za-z_][A-Za-z0-9_]*)=' | ForEach-Object { $_.Matches[0].Groups[1].Value } | ForEach-Object { $_ }
foreach ($k in $__NOVA_KEY_LIST) {
    $v = Get-Variable $k -ValueOnly -ErrorAction SilentlyContinue
    Set-Variable "__NOVA_SAVED_$k" (if ($null -ne $v) { 'set' } else { '' })
    Set-Variable "__NOVA_PRIOR_$k" $v -Scope Global
}

$__NOVA_WORKTREE = $repo
$__NOVA_WORKTREE_NAME = (& git -C $repo rev-parse --abbrev-ref HEAD 2>$null) ?? (Split-Path $repo -Leaf)

function global:_nova_worktree_here {
    $top = git -C $__NOVA_WORKTREE rev-parse --show-toplevel 2>$null
    if (-not $top) { return $false }
    (Resolve-Path $top) -eq (Resolve-Path $__NOVA_WORKTREE)
}

function global:nova {
    if (-not (_nova_worktree_here)) {
        Write-Error "nova: $(pwd) is outside $__NOVA_WORKTREE; refusing (the installed command is hidden while this checkout is active)"
        return 1
    }
    cd $__NOVA_WORKTREE
    if ($env:PYTHON) { & $env:PYTHON nova @args }
    elseif (Test-Path .venv\Scripts\python.exe) { & .venv\Scripts\python.exe nova @args }
    elseif (Test-Path .venv\bin\python) { & .venv\bin\python nova @args }
    elseif (Test-Path venv\Scripts\python.exe) { & venv\Scripts\python.exe nova @args }
    elseif (Test-Path venv\bin\python) { & venv\bin\python nova @args }
    else { python nova @args }
}

$global:prompt = ""
function global:nova_prompt {
    $base = $__NOVA_SAVED_PS1
    if (_nova_worktree_here) {
        $global:prompt = "($__NOVA_WORKTREE_NAME) $base"
    } else {
        $global:prompt = $base
    }
}

if ($null -ne $PROMPT_COMMAND) {
    $global:PROMPT_COMMAND = 'nova_prompt; ' + $PROMPT_COMMAND
} else {
    $global:PROMPT_COMMAND = 'nova_prompt'
}
nova_prompt

# Clean up internal functions
Remove-Item function:_nova_worktree_here -ErrorAction SilentlyContinue

# The composition library is not part of the user's shell.
Remove-Item function:nova_sync, function:nova_bootstrap_python, function:nova_compose_env, function:nova_apply_env, function:nova_activation_current, function:nova_ensure_env -ErrorAction SilentlyContinue
Remove-Variable _NOVA_REPO, _nova_script, _nova_keys, _nova_k, _nova_top -ErrorAction SilentlyContinue
