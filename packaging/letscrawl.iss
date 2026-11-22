; Windows installer for LetsCrawlit (Inno Setup 6):  iscc /DMyVersion=0.3.0 packaging\letscrawl.iss
; Packs dist\LetsCrawlit (built by PyInstaller) into one file: dist\LetsCrawlit-Setup-<version>.exe

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise updates and the uninstaller.
AppId={{6F3C2A9E-4B1D-4E7A-9C55-2D8B0F6A1E33}
AppName=LetsCrawlit
AppVersion={#MyVersion}
AppVerName=LetsCrawlit {#MyVersion}
AppPublisher=Diego Hortelano
AppPublisherURL=https://github.com/diego-hortelano-seo/letscrawlit
AppCopyright=© 2026 Diego Hortelano
VersionInfoVersion={#MyVersion}
VersionInfoDescription=Instalador de LetsCrawlit
; Per-user install (no administrator prompt), into %LOCALAPPDATA%\Programs\LetsCrawlit.
PrivilegesRequired=lowest
; The wizard speaks the language of Windows without asking first.
ShowLanguageDialog=no
DisableWelcomePage=no
DefaultDirName={autopf}\LetsCrawlit
; Always the new folder: an update from «LetsCrawl» (0.2.x) moves out of the old one, which is deleted below.
UsePreviousAppDir=no
DefaultGroupName=LetsCrawlit
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=LetsCrawlit-Setup-{#MyVersion}
SetupIconFile=letscrawl.ico
UninstallDisplayIcon={app}\LetsCrawlit.exe
UninstallDisplayName=LetsCrawlit
WizardStyle=modern
WizardImageFile=wizard-100.bmp,wizard-125.bmp,wizard-150.bmp,wizard-200.bmp
WizardSmallImageFile=wizard-small-100.bmp,wizard-small-125.bmp,wizard-small-150.bmp,wizard-small-200.bmp
Compression=lzma2/max
SolidCompression=yes
; Closes a running LetsCrawlit before updating it.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; An update replaces the program files completely (old libraries never linger).
Type: filesandordirs; Name: "{app}\_internal"
; Up to 0.2.x the program was called «LetsCrawl»: remove its files and shortcuts (the user's audits are not here,
; they live in %APPDATA% and the program moves them to the new folder on its first start).
Type: files; Name: "{app}\LetsCrawl.exe"
Type: filesandordirs; Name: "{userpf}\LetsCrawl"
Type: files; Name: "{userprograms}\LetsCrawl.lnk"
Type: files; Name: "{userdesktop}\LetsCrawl.lnk"

[Files]
Source: "..\dist\LetsCrawlit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\LetsCrawlit"; Filename: "{app}\LetsCrawlit.exe"
Name: "{autodesktop}\LetsCrawlit"; Filename: "{app}\LetsCrawlit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LetsCrawlit.exe"; Description: "{cm:LaunchProgram,LetsCrawlit}"; Flags: nowait postinstall skipifsilent

; Audits, notes and API keys live in %APPDATA%\LetsCrawlit and are kept on uninstall on purpose.

[Code]
// An old «LetsCrawl» window still open would keep its files locked and the user's data folder in use.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),'/F /IM LetsCrawl.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
