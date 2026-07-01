Set WshShell = CreateObject("WScript.Shell")
strPythonW = "C:\Users\Anderson Chaves\AppData\Local\Programs\Python\Python314\pythonw.exe"
strScript  = "C:\Users\Anderson Chaves\Downloads\bot\hypebot_service.py"
strDir     = "C:\Users\Anderson Chaves\Downloads\bot"

WshShell.CurrentDirectory = strDir
WshShell.Run Chr(34) & strPythonW & Chr(34) & " " & Chr(34) & strScript & Chr(34), 0, False
