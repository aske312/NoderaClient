[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'run', 'test', 'check')]
    [string]$Action = 'run',
    [string]$SubscriptionProxy,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$TestArguments
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    if ($Action -eq 'setup') {
        if (-not (Test-Path -LiteralPath $pythonPath)) {
            & py -3 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
        }
        & $pythonPath -m pip install -e '.[dev]'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $pythonPath scripts/prepare_core.py
        if ($LASTEXITCODE -ne 0) { throw 'Embedded core preparation failed.' }
        & $pythonPath -m pip freeze --exclude-editable |
            Set-Content -LiteralPath (Join-Path $projectRoot '.venv\installed.txt') -Encoding utf8
        Write-Output 'Ready. Run: .\scripts\dev.ps1 run'
    }
    else {
        if (-not (Test-Path -LiteralPath $pythonPath)) {
            throw 'Run .\scripts\dev.ps1 setup first.'
        }
        switch ($Action) {
            'run' {
                & $pythonPath -c 'from nodera.bundled_core import resolve_core; print(resolve_core())'
                if ($LASTEXITCODE -ne 0) { throw 'Run .\scripts\dev.ps1 setup first.' }
                $runArguments = @('main.py', '--dev')
                if ($SubscriptionProxy) { $runArguments += "--subscription-proxy=$SubscriptionProxy" }
                & $pythonPath @runArguments
            }
            'test' {
                $previousPlatform = $env:QT_QPA_PLATFORM
                try {
                    $env:QT_QPA_PLATFORM = 'offscreen'
                    & $pythonPath -m pytest @TestArguments
                }
                finally { $env:QT_QPA_PLATFORM = $previousPlatform }
            }
            'check' {
                & $pythonPath -m ruff check .
                if ($LASTEXITCODE -ne 0) { throw 'Lint checks failed.' }
                & $pythonPath -m ruff format --check .
            }
        }
        if ($LASTEXITCODE -ne 0) { throw "$Action failed (exit code $LASTEXITCODE)." }
    }
}
finally { Pop-Location }
