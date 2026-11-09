#if VER < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7 or newer is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif
#ifndef InstallerOutput
  #error InstallerOutput is required
#endif

[Setup]
AppId={{6201CE8F-E587-47BC-BE30-024E3E63C508}
AppName=Nodera
AppVersion={#AppVersion}
AppVerName=Nodera
AppPublisher=Nodera
AppPublisherURL=https://github.com/aske312/NoderaClient
AppUpdatesURL=https://github.com/aske312/NoderaClient/releases
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DefaultDirName={autopf64}\Nodera Client
UsePreviousAppDir=yes
DefaultGroupName=Nodera
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
UninstallDisplayName=Nodera
UninstallDisplayIcon={app}\Nodera.exe
SetupIconFile={#AppSource}\_internal\nodera\resources\nodera.ico
OutputDir={#InstallerOutput}
OutputBaseFilename=Nodera-Engine
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Excludes: "*.dpapi,settings.json,geo-cache.json,nodera.lock,maintenance.lock"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Nodera"; Filename: "{app}\Nodera.exe"
Name: "{autodesktop}\Nodera"; Filename: "{app}\Nodera.exe"; Tasks: desktopicon

[UninstallRun]
Filename: "{app}\Nodera.exe"; Parameters: "--remove-autostart"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveNoderaStartup"

[Code]
var
  CleanupMode: String;

function InitializeUninstall: Boolean;
var
  Choice: Integer;
begin
  CleanupMode := 'keep';
  Result := True;
  { Silent removal is used by upgrades and must never erase profiles. }
  if UninstallSilent then exit;
  { A damaged installation must still be removable, preserving its user data. }
  if not FileExists(ExpandConstant('{app}\Nodera.exe')) then exit;
  Result := Exec(ExpandConstant('{app}\Nodera.exe'), '--uninstall-choice', '',
    SW_SHOWNORMAL, ewWaitUntilTerminated, Choice);
  if Result then begin
    if Choice = 11 then CleanupMode := 'clean';
    if Choice = 13 then CleanupMode := 'cache';
    if Choice = 14 then CleanupMode := 'profiles';
    Result := (Choice = 10) or (Choice = 11) or (Choice = 13) or (Choice = 14);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExitCode: Integer;
  Arguments: String;
begin
  if (CurUninstallStep <> usUninstall) or UninstallSilent then exit;
  if not FileExists(ExpandConstant('{app}\Nodera.exe')) then exit;
  Arguments := '--finish-uninstall=' + CleanupMode;
  if not Exec(ExpandConstant('{app}\Nodera.exe'), Arguments, '',
    SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Не удалось завершить работу Nodera. Удаление отменено.');
  if ExitCode <> 0 then
    RaiseException('Не удалось подготовить удаление. Повторите попытку.');
end;

function InitializeSetup: Boolean;
begin
  { This engine is never a user-facing wizard. The signed-off frontend owns consent. }
  Result := IsAdminLoggedOn and WizardSilent and
    (ExpandConstant('{param:ACCEPTLICENSE|0}') = '1');
  if not Result then
    Log('Run Nodera-Setup.exe as administrator and accept the license in its window.');
end;
