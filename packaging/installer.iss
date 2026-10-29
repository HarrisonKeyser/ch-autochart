; Inno Setup script for the Windows installer (ch-autochart-setup.exe).
; Built by .github/workflows/windows-installer.yml after PyInstaller:
;   ISCC.exe /DAppVersion=2.0.0 packaging\installer.iss
; Installs for the current user only, so Windows doesn't ask for admin rights.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

[Setup]
AppId={{8C0E3B1F-7A3D-4E2B-9B7C-5F1A2D3C4B5E}
AppName=ch-autochart
AppVersion={#AppVersion}
AppVerName=ch-autochart {#AppVersion}
AppPublisher=Harrison Keyser
AppPublisherURL=https://harrisonkeyser.fun
AppSupportURL=https://github.com/HarrisonKeyser/ch-autochart
AppUpdatesURL=https://github.com/HarrisonKeyser/ch-autochart/releases
DefaultDirName={autopf}\ch-autochart
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=ch-autochart-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ch-autochart.exe
UninstallDisplayName=ch-autochart
Compression=lzma2/normal
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\ch-autochart\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ch-autochart"; Filename: "{app}\ch-autochart.exe"; Comment: "Turn songs into Clone Hero charts"
Name: "{autodesktop}\ch-autochart"; Filename: "{app}\ch-autochart.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ch-autochart.exe"; Description: "{cm:LaunchProgram,ch-autochart}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings, the log and the cache of separated songs.
Type: filesandordirs; Name: "{localappdata}\ch-autochart"
