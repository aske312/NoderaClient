param(
    [switch]$Clean,
    [switch]$Installer,
    [switch]$TestBuild,
    [string]$PythonExecutable,
    [ValidatePattern('^[0-9a-fA-F]{40}$')][string]$SigningCertificateThumbprint,
    [uri]$SigningTimestampUrl,
    [string]$SignToolPath
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Sign-BuildArtifact([string]$Path) {
    if (-not $SigningCertificateThumbprint) { return }
    & $SignToolPath sign /sha1 $SigningCertificateThumbprint /fd SHA256 /tr $SigningTimestampUrl.AbsoluteUri /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed: $Path" }
    & $SignToolPath verify /pa /tw $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed: $Path" }
}

Push-Location -LiteralPath $projectRoot
try {
    if ($SigningCertificateThumbprint) {
        if (-not $SigningTimestampUrl -or -not $SigningTimestampUrl.IsAbsoluteUri -or $SigningTimestampUrl.Scheme -ne 'https') {
            throw 'Signing requires an HTTPS RFC 3161 timestamp URL.'
        }
        $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$SigningCertificateThumbprint" -ErrorAction SilentlyContinue
        if (-not $certificate -or -not $certificate.HasPrivateKey) {
            throw 'The signing certificate and its private key must be available in CurrentUser\My.'
        }
        if ($SignToolPath) {
            $SignToolPath = (Resolve-Path -LiteralPath $SignToolPath).Path
        } else {
            $SignToolPath = (Get-Command signtool.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
            if (-not $SignToolPath) {
                throw 'Use -SignToolPath to point to signtool.exe from the Windows SDK.'
            }
        }
    } elseif ($SigningTimestampUrl -or $SignToolPath) {
        throw 'Use -SigningCertificateThumbprint to enable Authenticode signing.'
    }
    $pythonPath = if ($PythonExecutable) { (Resolve-Path -LiteralPath $PythonExecutable).Path } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
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
    Sign-BuildArtifact $appPath
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
        $enginePath = Join-Path $engineOutput 'Nodera-Engine.exe'
        Sign-BuildArtifact $enginePath
        & $pythonPath scripts/prepare_installer_bundle.py --source (Split-Path -Parent $appPath) --engine $enginePath
        if ($LASTEXITCODE -ne 0) { throw 'Installer engine verification failed' }
        $installerOutput = Join-Path $projectRoot 'dist\installers'
        & $pythonPath -m PyInstaller --noconfirm @cleanArguments --distpath $installerOutput --workpath build/installer-bootstrap packaging/Installer.spec
        if ($LASTEXITCODE -ne 0) { throw 'Installer bootstrap build failed' }
        $installerPath = Join-Path $installerOutput 'Nodera-Setup.exe'
        Sign-BuildArtifact $installerPath
        $installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($installerPath + '.sha256', $installerHash + '  ' + (Split-Path -Leaf $installerPath) + [Environment]::NewLine)
        Write-Output ('Installer: ' + $installerPath)
    }
}
finally {
    Pop-Location
}
