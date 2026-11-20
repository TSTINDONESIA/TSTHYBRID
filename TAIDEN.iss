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
Name: "autostart"; Description: "Jalankan TAIDEN otomatis di latar belakang saat Windows login"; Flags: unchecked

[Files]
Source: "build\TAIDEN\*"; DestDir: "{app}"; Excludes: "app\config.json,app\settings.env,*.pyc,__pycache__"; Flags: recursesubdirs createallsubdirs ignoreversion
; pengaturan tidak ditimpa saat update
Source: "build\TAIDEN\app\config.json"; DestDir: "{app}\app"; Flags: onlyifdoesntexist
Source: "build\TAIDEN\app\settings.env"; DestDir: "{app}\app"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\TAIDEN Interpreter"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN.vbs"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 168
Name: "{group}\Hentikan TAIDEN"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN-stop.vbs"""; WorkingDir: "{app}"
Name: "{group}\TAIDEN (tampilkan jendela, untuk debug)"; Filename: "{app}\TAIDEN.bat"; WorkingDir: "{app}"
Name: "{group}\Log TAIDEN"; Filename: "notepad.exe"; Parameters: """{app}\app\taiden.log"""
Name: "{userstartup}\TAIDEN"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN.vbs"" silent"; WorkingDir: "{app}"; Tasks: autostart
Name: "{group}\Pengaturan AI (settings.env)"; Filename: "notepad.exe"; Parameters: """{app}\app\settings.env"""
Name: "{group}\Uninstall TAIDEN"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TAIDEN Interpreter"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon

[UninstallRun]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN-stop.vbs"""; RunOnceId: "StopTaiden"; Flags: runhidden

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\TAIDEN.vbs"""; Description: "Jalankan TAIDEN sekarang (latar belakang)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent
