param(
    [switch]$Clean,
    [string]$SubscriptionProxy,
    [switch]$ImportDevData
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & (Join-Path $PSScriptRoot 'build.ps1') -TestBuild -Clean:$Clean
    $shell = New-Object -ComObject WScript.Shell
    $buildShortcut = $shell.CreateShortcut((Join-Path $projectRoot 'dist\Nodera-Test.lnk'))
    $appPath = $buildShortcut.TargetPath
    if (-not (Test-Path -LiteralPath $appPath)) { throw 'Test executable was not built.' }

    $testData = Join-Path $env:LOCALAPPDATA 'NoderaClient-Test'
    New-Item -ItemType Directory -Force -Path $testData | Out-Null
    if ($ImportDevData) {
        foreach ($name in @('settings.json', 'profiles.dpapi')) {
            $source = Join-Path $projectRoot ('artifacts\dev-data\' + $name)
            $destination = Join-Path $testData $name
            if ((Test-Path -LiteralPath $source) -and -not (Test-Path -LiteralPath $destination)) {
                if ($name -eq 'settings.json') {
                    # The source-only UI harness disables capture; the full test build uses TUN.
                    $settings = Get-Content -LiteralPath $source -Raw -Encoding utf8 | ConvertFrom-Json
                    $settings.tun = $true
                    [IO.File]::WriteAllText($destination, ($settings | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
                }
                else {
                    Copy-Item -LiteralPath $source -Destination $destination
                }
            }
        }
    }
    if ($SubscriptionProxy) {
        $validation = @'
import sys
from nodera.app import development_subscription_proxy
development_subscription_proxy(["--subscription-proxy=" + sys.argv[1]])
'@
        $validation | & (Join-Path $projectRoot '.venv\Scripts\python.exe') - $SubscriptionProxy
        if ($LASTEXITCODE -ne 0) { throw 'Invalid subscription proxy.' }
        @{ subscription_proxy = $SubscriptionProxy } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $testData 'test-network.json') -Encoding utf8
    }
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'launch_test.py') --record-build $appPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not record the completed test build.' }
    & (Join-Path $PSScriptRoot 'install-test-launcher.ps1')
    $desktop = [Environment]::GetFolderPath('Desktop')
    Write-Output ('Test application: ' + $appPath)
    Write-Output ('Test data: ' + $testData)
    Write-Output ('Desktop shortcut: ' + (Join-Path $desktop 'Nodera Test.lnk'))
    Write-Output 'Start menu shortcut: Nodera Test'
}
finally { Pop-Location }
