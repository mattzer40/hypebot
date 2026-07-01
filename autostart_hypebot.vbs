' HypeBot Autostart
' Inicia silenciosamente o servico no boot do Windows

Set objShell = CreateObject("WScript.Shell")
strPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

strPython = "pythonw.exe"
strScript = strPath & "hypebot_service.py"

objShell.Run strPython & " """ & strScript & """", 0, False
