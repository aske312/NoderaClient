param([switch]$Clean, [switch]$Installer, [switch]$TestBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $pythonPath scripts/prepare_core.py
    if ($LASTEXITCODE -ne 0) { throw 'Embedded Mihomo preparation failed' }
    & $pythonPath scripts/prepare_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Application icon preparation failed' }
    & $pythonPath scripts/prepare_licenses.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime license preparation failed' }
    if ($TestBuild -and $Installer) { throw 'The release installer cannot install a test build.' }
    $channel = if ($TestBuild) { 'test' } else { 'release' }
    $metadata = (& $pythonPath scripts/build_metadata.py --channel $channel) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Build metadata generation failed' }
    if ($metadata.artifact_id -notmatch '^\d{8}-\d{6}-\d{6}$') { throw 'Invalid artifact ID' }
    $buildOutput = Join-Path $projectRoot ('dist\builds\' + $metadata.artifact_id)
    $cleanArguments = @()
    if ($Clean) { $cleanArguments = @('--clean') }
    & $pythonPath -m PyInstaller --noconfirm @cleanArguments --distpath $buildOutput packaging/Nodera.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    $appPath = Join-Path $buildOutput 'Nodera\Nodera.exe'
    $iconPath = Join-Path $buildOutput 'Nodera\_internal\nodera\resources\nodera.ico'
    if (-not (Test-Path -LiteralPath $iconPath)) { throw 'Application icon missing' }
    $shell = New-Object -ComObject WScript.Shell
    $temporaryShortcut = Join-Path $projectRoot ('dist\Nodera-' + [guid]::NewGuid().ToString('N') + '.lnk')
    $shortcut = $shell.CreateShortcut($temporaryShortcut)
    $shortcut.TargetPath = $appPath
    $shortcut.WorkingDirectory = Split-Path -Parent $appPath
    $shortcut.IconLocation = $iconPath + ',0'
    $shortcut.Description = if ($TestBuild) { 'Nodera Test' } else { 'Nodera' }
    $shortcut.Save()
    $shortcutName = if ($TestBuild) { 'Nodera-Test.lnk' } else { 'Nodera.lnk' }
    Move-Item -LiteralPath $temporaryShortcut -Destination (Join-Path $projectRoot ('dist\' + $shortcutName)) -Force
    Write-Output ('Portable application: ' + $appPath + ' (distribute the entire Nodera directory)')
    Write-Output ('Latest build shortcut: dist\' + $shortcutName)
    if ($Installer) {
        & $pythonPath scripts/prepare_installer_bundle.py --source (Split-Path -Parent $appPath)
        if ($LASTEXITCODE -ne 0) { throw 'Installer payload manifest preparation failed' }
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source } else { Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
        if (-not (Test-Path -LiteralPath $compilerPath)) {
            throw 'Inno Setup 6.7+ is required on the build machine.'
        }
        $engineOutput = Join-Path $projectRoot 'build\installer-engine'
        & $compilerPath ('/DAppVersion=' + $metadata.version) ('/DAppSource=' + (Split-Path -Parent $appPath)) ('/DInstallerOutput=' + $engineOutput) packaging/Nodera.iss
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
        & $pythonPath scripts/prepare_installer_bundle.py --source (Split-Path -Parent $appPath) --engine (Join-Path $engineOutput 'Nodera-Engine.exe')
        if ($LASTEXITCODE -ne 0) { throw 'Installer engine verification failed' }
        $installerOutput = Join-Path $projectRoot 'dist\installers'
        & $pythonPath -m PyInstaller --noconfirm @cleanArguments --distpath $installerOutput --workpath build/installer-bootstrap packaging/Installer.spec
        if ($LASTEXITCODE -ne 0) { throw 'Installer bootstrap build failed' }
        $previewOutput = Join-Path $projectRoot 'dist\preview'
        New-Item -ItemType Directory -Force -Path $previewOutput | Out-Null
        Move-Item -LiteralPath (Join-Path $installerOutput 'Nodera-Setup-Preview.exe') -Destination (Join-Path $previewOutput 'Nodera-Setup-Preview.exe') -Force
        $installerPath = Join-Path $installerOutput 'Nodera-Setup.exe'
        $installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($installerPath + '.sha256', $installerHash + '  ' + (Split-Path -Leaf $installerPath) + [Environment]::NewLine)
        Write-Output ('Installer: ' + $installerPath)
    }
}
finally {
    Pop-Location
}
