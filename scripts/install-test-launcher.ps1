$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $PSScriptRoot 'launch_test.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Prepare .venv first.' }
$shell = New-Object -ComObject WScript.Shell
foreach ($directory in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $link = $shell.CreateShortcut((Join-Path $directory 'Nodera Test.lnk'))
    $link.TargetPath = $pythonPath
    $link.Arguments = '"' + $launcher + '"'
    $link.WorkingDirectory = $projectRoot
    $link.IconLocation = (Join-Path $projectRoot 'nodera\resources\nodera.ico') + ',0'
    $link.Description = 'Nodera Test - rebuild changed sources and launch'
    $link.Save()
}
Write-Output 'Nodera Test shortcuts now automatically build and launch changed sources.'
