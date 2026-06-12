Option Explicit

Dim arguments, shell, command, index, waitForExit
Set arguments = WScript.Arguments

If arguments.Count < 1 Then
    WScript.Quit 2
End If

waitForExit = False
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden"

If LCase(arguments(0)) = "--sta" Then
    command = command & " -STA"
    index = 1
Else
    index = 0
End If

If index < arguments.Count And LCase(arguments(index)) = "--wait" Then
    waitForExit = True
    index = index + 1
End If

If index >= arguments.Count Then
    WScript.Quit 2
End If

command = command & " -File " & Quote(arguments(index))
index = index + 1

Do While index < arguments.Count
    command = command & " " & Quote(arguments(index))
    index = index + 1
Loop

Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run(command, 0, waitForExit)

Function Quote(value)
    Quote = """" & Replace(value, """", """""") & """"
End Function
