' Meluncurkan TAIDEN di LATAR BELAKANG (tanpa jendela). Argumen "silent" = jangan buka browser (untuk jalan otomatis saat login).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
extra = ""
If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "silent" Then extra = " silent"
End If
sh.Run "cmd /c " & Chr(34) & Chr(34) & d & "\TAIDEN.bat" & Chr(34) & " bg" & extra & Chr(34), 0, False
