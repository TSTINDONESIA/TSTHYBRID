' Menghentikan TAIDEN yang berjalan di latar belakang.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & d & "\TAIDEN-stop.ps1" & Chr(34), 0, True
MsgBox "TAIDEN dihentikan.", 64, "TAIDEN"
