' Start LectureLab with no visible window (target of the Desktop/Start-menu shortcut).
' uv's .venv\Scripts\pythonw.exe is a console trampoline, so started directly it shows an empty
' terminal that stops the app when closed. Run with window style 0, nothing is shown; quit from
' the tray icon. The launcher itself is backend\launcher.py.
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = repo
shell.Run """" & repo & "\.venv\Scripts\pythonw.exe"" -m backend.launcher", 0, False
