param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPath = Join-Path $projectDir "server.py"
$hiddenLauncher = Join-Path $projectDir "launch-hidden.vbs"
$trayScript = Join-Path $projectDir "tray.ps1"
$appUrl = "http://127.0.0.1:$Port"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$startupValueName = "JNovelsCache"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class JNovelsNativeIcon {
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

$pathBytes = [Text.Encoding]::UTF8.GetBytes($projectDir.ToLowerInvariant())
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $pathHash = (
        [BitConverter]::ToString($sha256.ComputeHash($pathBytes)) -replace "-", ""
    ).Substring(0, 12)
}
finally {
    $sha256.Dispose()
}
$createdNew = $false
$mutex = [Threading.Mutex]::new(
    $true,
    "Local\JNovelsCacheTray_$pathHash",
    [ref]$createdNew
)
if (-not $createdNew) {
    $mutex.Dispose()
    exit 0
}

$script:serverProcess = $null
$script:currentState = ""
$script:currentIcon = $null

function Get-AppStatus {
    try {
        return Invoke-RestMethod `
            -Uri "$appUrl/api/status" `
            -Method Get `
            -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Start-AppServer {
    if (Get-AppStatus) {
        return
    }

    $python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    $windowStyle = "Hidden"
    if (-not $python) {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
    }
    if (-not $python) {
        throw "Python was not found on PATH."
    }

    $script:serverProcess = Start-Process `
        -FilePath $python.Source `
        -ArgumentList @(
            "`"$serverPath`"",
            "--host",
            "127.0.0.1",
            "--port",
            [string]$Port
        ) `
        -WorkingDirectory $projectDir `
        -WindowStyle $windowStyle `
        -PassThru
}

function Stop-OwnedServer {
    if (
        $script:serverProcess `
        -and -not $script:serverProcess.HasExited
    ) {
        Stop-Process -Id $script:serverProcess.Id -ErrorAction SilentlyContinue
        $script:serverProcess.WaitForExit(3000) | Out-Null
    }
    $script:serverProcess = $null
}

function New-TrayIcon {
    param(
        [Drawing.Color]$IndicatorColor
    )

    $bitmap = [Drawing.Bitmap]::new(32, 32)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([Drawing.Color]::FromArgb(23, 21, 15))

    $borderPen = [Drawing.Pen]::new(
        [Drawing.Color]::FromArgb(215, 173, 75),
        2
    )
    $graphics.DrawRectangle($borderPen, 2, 2, 27, 27)

    $font = [Drawing.Font]::new(
        "Georgia",
        18,
        [Drawing.FontStyle]::Bold -bor [Drawing.FontStyle]::Italic,
        [Drawing.GraphicsUnit]::Pixel
    )
    $textBrush = [Drawing.SolidBrush]::new(
        [Drawing.Color]::FromArgb(241, 205, 114)
    )
    $graphics.DrawString("J", $font, $textBrush, 7, 4)

    $indicatorBrush = [Drawing.SolidBrush]::new($IndicatorColor)
    $graphics.FillEllipse($indicatorBrush, 22, 22, 8, 8)
    $graphics.DrawEllipse([Drawing.Pens]::Black, 22, 22, 8, 8)

    $handle = $bitmap.GetHicon()
    $icon = [Drawing.Icon]::FromHandle($handle).Clone()
    [JNovelsNativeIcon]::DestroyIcon($handle) | Out-Null

    $indicatorBrush.Dispose()
    $textBrush.Dispose()
    $font.Dispose()
    $borderPen.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
    return $icon
}

function Open-App {
    Start-Process $appUrl
}

function Get-StartupEnabled {
    return $null -ne (
        Get-ItemProperty `
            -Path $runKey `
            -Name $startupValueName `
            -ErrorAction SilentlyContinue
    )
}

function Set-StartupEnabled {
    param(
        [bool]$Enabled
    )

    if ($Enabled) {
        $command = 'wscript.exe //B "{0}" --sta "{1}"' -f $hiddenLauncher, $trayScript
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty `
            -Path $runKey `
            -Name $startupValueName `
            -Value $command `
            -PropertyType String `
            -Force | Out-Null
        return
    }

    Remove-ItemProperty `
        -Path $runKey `
        -Name $startupValueName `
        -ErrorAction SilentlyContinue
}

$notifyIcon = [Windows.Forms.NotifyIcon]::new()
$notifyIcon.Visible = $true
$menu = [Windows.Forms.ContextMenuStrip]::new()

$statusItem = [Windows.Forms.ToolStripMenuItem]::new("Starting...")
$statusItem.Enabled = $false
$openItem = [Windows.Forms.ToolStripMenuItem]::new("Open JNovels Cache")
$restartItem = [Windows.Forms.ToolStripMenuItem]::new("Restart server")
$startupItem = [Windows.Forms.ToolStripMenuItem]::new("Start with Windows")
$startupItem.Checked = Get-StartupEnabled
$exitItem = [Windows.Forms.ToolStripMenuItem]::new("Exit JNovels Cache")

$menu.Items.Add($statusItem) | Out-Null
$menu.Items.Add([Windows.Forms.ToolStripSeparator]::new()) | Out-Null
$menu.Items.Add($openItem) | Out-Null
$menu.Items.Add($restartItem) | Out-Null
$menu.Items.Add($startupItem) | Out-Null
$menu.Items.Add([Windows.Forms.ToolStripSeparator]::new()) | Out-Null
$menu.Items.Add($exitItem) | Out-Null
$notifyIcon.ContextMenuStrip = $menu

function Set-TrayState {
    param(
        [string]$State,
        [string]$Label,
        [Drawing.Color]$Color
    )

    $statusItem.Text = $Label
    $notifyIcon.Text = "JNovels Cache - $Label"
    if ($script:currentState -eq $State) {
        return
    }
    $script:currentState = $State
    $nextIcon = New-TrayIcon $Color
    $notifyIcon.Icon = $nextIcon
    if ($script:currentIcon) {
        $script:currentIcon.Dispose()
    }
    $script:currentIcon = $nextIcon
}

function Update-TrayState {
    $status = Get-AppStatus
    if (-not $status) {
        Set-TrayState `
            "stopped" `
            "Server stopped" `
            ([Drawing.Color]::FromArgb(190, 70, 65))
        return
    }
    if ($status.sync.running) {
        Set-TrayState `
            "syncing" `
            "Sync in progress" `
            ([Drawing.Color]::FromArgb(225, 174, 55))
        return
    }
    if ($status.sync.error) {
        Set-TrayState `
            "warning" `
            "Running - last sync failed" `
            ([Drawing.Color]::FromArgb(225, 115, 45))
        return
    }
    Set-TrayState `
        "running" `
        "Server running" `
        ([Drawing.Color]::FromArgb(75, 185, 105))
}

$openItem.Add_Click({ Open-App })
$notifyIcon.Add_DoubleClick({ Open-App })
$menu.Add_Opening({
    $startupItem.Checked = Get-StartupEnabled
})
$startupItem.Add_Click({
    try {
        $enabled = -not (Get-StartupEnabled)
        Set-StartupEnabled $enabled
        $startupItem.Checked = $enabled
    }
    catch {
        $startupItem.Checked = Get-StartupEnabled
        [Windows.Forms.MessageBox]::Show(
            $_.Exception.Message,
            "Could not update Windows startup",
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
})
$restartItem.Add_Click({
    Stop-OwnedServer
    Start-Sleep -Milliseconds 300
    try {
        Start-AppServer
    }
    catch {
        [Windows.Forms.MessageBox]::Show(
            $_.Exception.Message,
            "JNovels Cache",
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    Update-TrayState
})
$exitItem.Add_Click({
    $timer.Stop()
    Stop-OwnedServer
    $notifyIcon.Visible = $false
    [Windows.Forms.Application]::Exit()
})

$timer = [Windows.Forms.Timer]::new()
$timer.Interval = 3000
$timer.Add_Tick({ Update-TrayState })

try {
    Set-TrayState `
        "starting" `
        "Starting server" `
        ([Drawing.Color]::FromArgb(120, 145, 175))
    Start-AppServer
    $timer.Start()
    Update-TrayState
    [Windows.Forms.Application]::Run()
}
catch {
    [Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "JNovels Cache could not start",
        [Windows.Forms.MessageBoxButtons]::OK,
        [Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}
finally {
    $timer.Dispose()
    Stop-OwnedServer
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    $menu.Dispose()
    if ($script:currentIcon) {
        $script:currentIcon.Dispose()
    }
    if ($createdNew) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
