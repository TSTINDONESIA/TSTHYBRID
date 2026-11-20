[Setup]
AppName=TAIDEN Interpreter
AppVersion=1.0
AppPublisher=TAIDEN
DefaultDirName={localappdata}\Programs\TAIDEN
DefaultGroupName=TAIDEN
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
OutputDir=dist
OutputBaseFilename=TAIDEN-Setup
Compression=lzma2/fast
SolidCompression=no
DisableProgramGroupPage=yes
UninstallDisplayName=TAIDEN Interpreter

[Tasks]
Name: "desktopicon"; Description: "Buat ikon di Desktop"; Flags: unchecked

[Files]
Source: "build\TAIDEN\*"; DestDir: "{app}"; Excludes: "app\config.json,app\settings.env,*.pyc,__pycache__"; Flags: recursesubdirs createallsubdirs ignoreversion
; pengaturan tidak ditimpa saat update
Source: "build\TAIDEN\app\config.json"; DestDir: "{app}\app"; Flags: onlyifdoesntexist
Source: "build\TAIDEN\app\settings.env"; DestDir: "{app}\app"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\TAIDEN Interpreter"; Filename: "{app}\TAIDEN.bat"; WorkingDir: "{app}"
Name: "{group}\Pengaturan AI (settings.env)"; Filename: "notepad.exe"; Parameters: """{app}\app\settings.env"""
Name: "{group}\Uninstall TAIDEN"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TAIDEN Interpreter"; Filename: "{app}\TAIDEN.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\TAIDEN.bat"; Description: "Jalankan TAIDEN sekarang"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent
