#define MyAppName "Library Face Access System"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Face Recognition Team"
#define MyAppExeName "launch-kiosk.bat"

[Setup]
AppId={{DAB2E390-6DE0-4A9A-9D8B-7144DB9B66CC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\LibraryFaceAccessSystem
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=LibraryFaceAccessSystemInstaller
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; root-level python/runtime files
Source: "{#SourcePath}\..\..\*.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\alembic.ini"; DestDir: "{app}"; Flags: ignoreversion

; app runtime packages
Source: "{#SourcePath}\..\..\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\core\*"; DestDir: "{app}\core"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\database\*"; DestDir: "{app}\database"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\routes\*"; DestDir: "{app}\routes"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\services\*"; DestDir: "{app}\services"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\utils\*"; DestDir: "{app}\utils"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\workers\*"; DestDir: "{app}\workers"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\alembic\*"; DestDir: "{app}\alembic"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\scripts\*"; DestDir: "{app}\scripts"; Flags: recursesubdirs createallsubdirs ignoreversion

; web assets
Source: "{#SourcePath}\..\..\static\*"; DestDir: "{app}\static"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\templates\*"; DestDir: "{app}\templates"; Flags: recursesubdirs createallsubdirs ignoreversion

; model assets used at runtime
Source: "{#SourcePath}\..\..\models\*"; DestDir: "{app}\models"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourcePath}\..\..\model-training\Yolo-model\*"; DestDir: "{app}\model-training\Yolo-model"; Flags: recursesubdirs createallsubdirs ignoreversion

; installer helpers
Source: "{#SourcePath}\*"; DestDir: "{app}\installer\windows"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\installer\windows\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\installer\windows\{#MyAppExeName}"

[Run]
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\installer\windows\setup.ps1"""; \
    WorkingDir: "{app}"; \
    Flags: waituntilterminated runasoriginaluser

