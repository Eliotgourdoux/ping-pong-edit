; Installeur Windows (Inno Setup). Build : iscc /DAppVersion=1.0.0 installer\pongedit.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C2E-3A4D-4E8B-9C55-1A2B3C4D5E6F}
AppName=Ping Pong Edit
AppVersion={#AppVersion}
AppPublisher=Eliot Gourdoux
AppCopyright=Copyright (c) 2026 Eliot Gourdoux. Tous droits réservés.
DefaultDirName={localappdata}\Programs\Ping Pong Edit
DefaultGroupName=Ping Pong Edit
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PingPongEdit-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\PingPongEdit.exe
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

[Code]
var
  InstallDone: Boolean;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    InstallDone := True;
end;

{ À la fermeture de l'assistant : propose de supprimer le fichier d'installation, devenu
  inutile. Il ne peut pas s'effacer lui-même pendant qu'il tourne : on lance une
  commande qui attend quelques secondes puis le supprime. }
procedure DeinitializeSetup();
var
  Code: Integer;
begin
  if InstallDone and (not WizardSilent) then
    if MsgBox('Ping Pong Edit est installé.' + #13#10 + #13#10 +
              'Supprimer le fichier d''installation maintenant ?' + #13#10 +
              '(Vous n''en avez plus besoin.)',
              mbConfirmation, MB_YESNO) = IDYES then
      Exec(ExpandConstant('{cmd}'),
           '/C ping 127.0.0.1 -n 4 > nul & del /F /Q "' + ExpandConstant('{srcexe}') + '"',
           '', SW_HIDE, ewNoWait, Code);
end;
