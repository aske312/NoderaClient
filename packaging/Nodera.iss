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
function InitializeSetup: Boolean;
begin
  { This engine is never a user-facing wizard. The signed-off frontend owns consent. }
  Result := IsAdminLoggedOn and WizardSilent and
    (ExpandConstant('{param:ACCEPTLICENSE|0}') = '1');
  if not Result then
    Log('Run Nodera-Setup.exe as administrator and accept the license in its window.');
end;
