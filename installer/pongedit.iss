; Installeur Windows (Inno Setup). Build : iscc /DAppVersion=1.0.0 installer\pongedit.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C2E-3A4D-4E8B-9C55-1A2B3C4D5E6F}
AppName=Ping Pong Edit
AppVersion={#AppVersion}
AppPublisher=Eliot Gourdoux
DefaultDirName={localappdata}\Programs\Ping Pong Edit
DefaultGroupName=Ping Pong Edit
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PingPongEdit-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName=Ping Pong Edit
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\PingPongEdit\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Ping Pong Edit"; Filename: "{app}\PingPongEdit.exe"
Name: "{autodesktop}\Ping Pong Edit"; Filename: "{app}\PingPongEdit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PingPongEdit.exe"; Description: "Lancer Ping Pong Edit"; Flags: nowait postinstall skipifsilent
