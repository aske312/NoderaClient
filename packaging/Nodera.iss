#if VER < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7 or newer is required for the Nodera installer theme
#endif
#ifndef AppVersion
  #error AppVersion must be provided by scripts/build.ps1
#endif
#ifndef AppSource
  #error AppSource must be provided by scripts/build.ps1
#endif
#ifndef InstallerOutput
  #error InstallerOutput must be provided by scripts/build.ps1
#endif

[Setup]
#ifdef Preview
AppId=NoderaInstallerPreview
Uninstallable=no
CreateAppDir=no
PrivilegesRequired=lowest
OutputBaseFilename=Nodera-Setup-Preview
#else
AppId={{6201CE8F-E587-47BC-BE30-024E3E63C508}
PrivilegesRequired=admin
OutputBaseFilename=Nodera-Setup
#endif
AppName=Nodera
AppVersion={#AppVersion}
AppPublisher=Nodera
AppPublisherURL=https://github.com/aske312/NoderaClient
AppUpdatesURL=https://github.com/aske312/NoderaClient/releases
DefaultDirName={autopf}\Nodera
DefaultGroupName=Nodera
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#InstallerOutput}
SetupIconFile={#AppSource}\_internal\nodera\resources\nodera.ico
UninstallDisplayIcon={app}\Nodera.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dark polar includetitlebar hidebevels
WizardBackColor=#0a1821
WizardBackImageFile=..\build\installer\background.png
WizardImageFile=..\build\installer\side.png
WizardSmallImageFile=..\build\installer\mark.png
WizardImageBackColor=#0a1821
WizardSmallImageBackColor=#0a1821
DisableWelcomePage=no
DefaultDialogFontName=Segoe UI
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "autostart"; Description: "{cm:AutostartTask}"; Flags: unchecked

[CustomMessages]
russian.AutostartTask=Запускать Nodera при входе в Windows
english.AutostartTask=Start Nodera when signing in to Windows
russian.AutostartFailed=Не удалось настроить автозапуск. Его можно включить в настройках Nodera.
english.AutostartFailed=Could not configure startup. You can enable it in Nodera settings.

[Files]
#ifndef Preview
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Nodera"; Filename: "{app}\Nodera.exe"
Name: "{autodesktop}\Nodera"; Filename: "{app}\Nodera.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Nodera.exe"; Description: "{cm:LaunchProgram,Nodera}"; Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
Filename: "{app}\Nodera.exe"; Parameters: "--remove-autostart"; Flags: runhidden waituntilterminated skipifdoesntexist
#endif

[Code]
#ifndef Preview
var
  AutostartChoiceLoaded: Boolean;

procedure CurPageChanged(CurPageID: Integer);
var
  ExistingApp: String;
  VersionMS, VersionLS: Cardinal;
  ResultCode: Integer;
begin
  if (CurPageID = wpSelectTasks) and not AutostartChoiceLoaded then
  begin
    AutostartChoiceLoaded := True;
    ExistingApp := ExpandConstant('{app}\Nodera.exe');
    { Older clients do not understand the status command. }
    if GetVersionNumbers(ExistingApp, VersionMS, VersionLS) and
       ((VersionMS > 0) or (VersionLS >= $00020000)) then
    begin
      if Exec(ExistingApp, '--autostart-status', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
      begin
        if ResultCode = 0 then WizardSelectTasks('autostart');
        if ResultCode = 1 then WizardSelectTasks('!autostart');
      end;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Parameters: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('autostart') then
      Parameters := '--configure-autostart enable'
    else
      Parameters := '--configure-autostart disable';
    if not Exec(ExpandConstant('{app}\Nodera.exe'), Parameters, '', SW_HIDE,
                ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException(CustomMessage('AutostartFailed'));
  end;
end;
#endif
