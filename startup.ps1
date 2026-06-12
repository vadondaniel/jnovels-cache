param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Enable", "Disable")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$hiddenLauncher = Join-Path $projectDir "launch-hidden.vbs"
$trayScript = Join-Path $projectDir "tray.ps1"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$valueName = "JNovelsCache"

try {
    if ($Action -eq "Enable") {
        $command = 'wscript.exe //B "{0}" --sta "{1}"' -f $hiddenLauncher, $trayScript
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty `
            -Path $runKey `
            -Name $valueName `
            -Value $command `
            -PropertyType String `
            -Force | Out-Null

        Start-Process `
            -FilePath "wscript.exe" `
            -ArgumentList @(
                "//B",
                "`"$hiddenLauncher`"",
                "--sta",
                "`"$trayScript`""
            ) `
            -WindowStyle Hidden
        $message = (
            "JNovels Cache will start when you sign in.`n`n" +
            "The tray app has also been started."
        )
    }
    else {
        Remove-ItemProperty `
            -Path $runKey `
            -Name $valueName `
            -ErrorAction SilentlyContinue
        $message = (
            "JNovels Cache will no longer start when you sign in.`n`n" +
            "Use Exit JNovels Cache from the tray icon to stop the current instance."
        )
    }

    [Windows.Forms.MessageBox]::Show(
        $message,
        "JNovels Cache",
        [Windows.Forms.MessageBoxButtons]::OK,
        [Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
}
catch {
    [Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "JNovels Cache startup setting failed",
        [Windows.Forms.MessageBoxButtons]::OK,
        [Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
