Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
dashboard = folder & "\dashboard.py"
Set processes = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'pythonw.exe'")
For Each process In processes
    If Not IsNull(process.CommandLine) Then
        If InStr(1, process.CommandLine, dashboard, vbTextCompare) > 0 Then
            If shell.AppActivate(process.ProcessId) Then WScript.Quit
        End If
    End If
Next
shell.Run "pyw -3 """ & folder & "\dashboard.py""", 0, False
