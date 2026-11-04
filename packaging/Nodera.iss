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
AppId={{6201CE8F-E587-47BC-BE30-024E3E63C508}
AppName=Nodera
AppVersion={#AppVersion}
AppPublisher=Nodera
AppPublisherURL=https://github.com/aske312/NoderaClient
AppUpdatesURL=https://github.com/aske312/NoderaClient/releases
DefaultDirName={autopf}\Nodera
DefaultGroupName=Nodera
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#InstallerOutput}
OutputBaseFilename=Nodera-Setup-{#AppVersion}-x64
SetupIconFile={#AppSource}\_internal\nodera\resources\nodera.ico
UninstallDisplayIcon={app}\Nodera.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Nodera"; Filename: "{app}\Nodera.exe"
Name: "{autodesktop}\Nodera"; Filename: "{app}\Nodera.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Nodera.exe"; Description: "{cm:LaunchProgram,Nodera}"; Flags: nowait postinstall skipifsilent runascurrentuser
