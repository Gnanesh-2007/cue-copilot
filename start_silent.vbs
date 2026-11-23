Set WshShell = CreateObject("WScript.Shell")
' Runs pythonw on app.py without opening any black CMD window (0 = hide window)
WshShell.Run "pythonw app.pyw", 0, False
