param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Enable", "Disable")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$trayScript = Join-Path $projectDir "tray.ps1"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$valueName = "JNovelsCache"

if ($Action -eq "Enable") {
    $command = (
        'powershell.exe -NoProfile -STA -ExecutionPolicy Bypass ' +
        '-WindowStyle Hidden -File "{0}"'
    ) -f $trayScript
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty `
        -Path $runKey `
        -Name $valueName `
        -Value $command `
        -PropertyType String `
        -Force | Out-Null

    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            "`"$trayScript`""
        ) `
        -WindowStyle Hidden
    Write-Host "JNovels Cache will now start when you sign in."
    Write-Host "The tray app has also been started now."
}
else {
    Remove-ItemProperty `
        -Path $runKey `
        -Name $valueName `
        -ErrorAction SilentlyContinue
    Write-Host "JNovels Cache will no longer start when you sign in."
    Write-Host "Use Exit JNovels Cache from the tray icon to stop the current instance."
}
