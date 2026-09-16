<#
.SYNOPSIS
    Make the wall_v2 app start automatically when Windows boots.

.DESCRIPTION
    One-time installer.  Run it from an *elevated* PowerShell window:

        powershell -NoProfile -ExecutionPolicy Bypass -File .\install-autostart.ps1

    It will:
      1. Create a dedicated virtualenv (outside OneDrive) and install the
         server dependencies into it.
      2. Run start-wall.ps1 -SetupOnly once, so .env and the TLS certificate
         exist and any problem shows up now rather than at 3am after a reboot.
      3. Allow inbound TCP 80/443 on the Private firewall profile - a task
         running before login has no desktop to show the firewall prompt on,
         so without this the phone cannot connect.
      4. Register the scheduled task "Wall v2": trigger At startup, runs
         whether you are logged on or not, restarts itself if it dies.

    Elevation is required: "At startup" triggers and tasks that run without an
    interactive session can only be registered by an administrator.

.PARAMETER AsSystem
    Register the task as NT AUTHORITY\SYSTEM instead of your account.  No
    password needed.  Note that SYSTEM cannot hydrate OneDrive "cloud only"
    files, so keep this folder pinned locally if you use it.

.PARAMETER Uninstall
    Remove the scheduled task and the firewall rules.  Leaves the venv, .env,
    certificate and logs alone.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$AsSystem,
    [switch]$SkipSmokeTest,
    [string]$TaskName = 'Wall v2',
    [string]$VenvPath = (Join-Path $env:USERPROFILE '.venvs\wall_v2'),
    [string]$BasePython
)

$ErrorActionPreference = 'Stop'
$AppDir      = $PSScriptRoot
$StartScript = Join-Path $AppDir 'start-wall.ps1'
$VenvPython  = Join-Path $VenvPath 'Scripts\python.exe'
$FwHttp      = 'Wall v2 (HTTP 80)'
$FwHttps     = 'Wall v2 (HTTPS 443)'

function Write-Step { param([string]$m) Write-Host "`n== $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "   $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "   $m" -ForegroundColor Yellow }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "This script must be run from an elevated PowerShell window (right-click -> Run as administrator)."
}

# -- Uninstall ---------------------------------------------------------------

if ($Uninstall) {
    Write-Step "Removing scheduled task '$TaskName'"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "Task removed."
    } else {
        Write-Warn "Task not found - nothing to do."
    }

    Write-Step "Removing firewall rules"
    foreach ($name in @($FwHttp, $FwHttps)) {
        if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $name
            Write-Ok "Removed '$name'."
        }
    }

    Write-Host "`nDone. The venv, .env, certificate and logs were left in place." -ForegroundColor Cyan
    return
}

# -- 1. Virtualenv -----------------------------------------------------------

Write-Step "Setting up the virtualenv at $VenvPath"

if (-not (Test-Path $VenvPython)) {
    if (-not $BasePython) {
        # Prefer a real filesystem install: the Microsoft Store build is an
        # app-packaged alias and is unreliable in a pre-login session.
        $found = @()
        $found += Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-*\python.exe') -ErrorAction SilentlyContinue
        $found += Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') -ErrorAction SilentlyContinue
        $found += Get-ChildItem 'C:\Python3*\python.exe' -ErrorAction SilentlyContinue
        $found += Get-ChildItem 'C:\Program Files\Python3*\python.exe' -ErrorAction SilentlyContinue
        $BasePython = ($found | Sort-Object FullName -Descending | Select-Object -First 1).FullName
    }
    if (-not $BasePython) {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd) {
            $BasePython = $cmd.Source
            Write-Warn "Falling back to $BasePython."
            if ($BasePython -like '*WindowsApps*') {
                Write-Warn "That is the Microsoft Store Python; it may fail to launch before login."
                Write-Warn "If the task never starts, install Python from python.org and re-run with -BasePython."
            }
        }
    }
    if (-not $BasePython) { throw "No Python interpreter found. Install Python, then re-run with -BasePython <path>." }

    Write-Ok "Base interpreter: $BasePython"
    & $BasePython -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed (exit $LASTEXITCODE)." }
} else {
    Write-Ok "Virtualenv already exists."
}

Write-Step "Installing server dependencies"
# Only what run.py/main.py need.  numpy + opencv-python are for the video/
# tools, which you run by hand - no reason to carry them in the service venv.
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install --upgrade fastapi "uvicorn[standard]" pyserial python-dotenv websockets cryptography
if ($LASTEXITCODE -ne 0) {
    throw ("pip install failed (exit $LASTEXITCODE). If a wheel is missing for this Python version, " +
           "install an older Python (e.g. 3.12) and re-run with -BasePython <path> after deleting $VenvPath.")
}
Write-Ok "Dependencies installed."

# -- 2. Smoke test -----------------------------------------------------------

if (-not $SkipSmokeTest) {
    Write-Step "Running setup checks (.env, certificate)"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -Python $VenvPython -SetupOnly
    if ($LASTEXITCODE -ne 0) { throw "start-wall.ps1 -SetupOnly failed (exit $LASTEXITCODE). See logs\ for details." }
    Write-Ok "Setup checks passed."
}

# -- 3. Firewall -------------------------------------------------------------

Write-Step "Allowing inbound 80/443 on the Private profile"
foreach ($rule in @(@{ Name = $FwHttp; Port = 80 }, @{ Name = $FwHttps; Port = 443 })) {
    if (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue) {
        Write-Ok "'$($rule.Name)' already exists."
    } else {
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $rule.Port -Profile Private | Out-Null
        Write-Ok "Created '$($rule.Name)'."
    }
}
$publicNet = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' })
if ($publicNet.Count) {
    Write-Warn ("Network '{0}' is set to Public, so these Private-profile rules will not apply." -f $publicNet[0].Name)
    Write-Warn "Set it to Private:  Set-NetConnectionProfile -InterfaceIndex $($publicNet[0].InterfaceIndex) -NetworkCategory Private"
}

# -- 4. Scheduled task -------------------------------------------------------

Write-Step "Registering the scheduled task '$TaskName'"

$action = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`" -Python `"$VenvPython`"") `
    -WorkingDirectory $AppDir

$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'   # let USB enumeration and the network settle

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -Hidden `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if ($AsSystem) {
    $principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description 'Starts the wall_v2 LED display companion app at boot.' | Out-Null
    Write-Ok "Registered to run as SYSTEM."
} else {
    Write-Host "   Windows needs your account password to run the task before you log in." -ForegroundColor Yellow
    Write-Host "   It is stored encrypted by the Task Scheduler credential store." -ForegroundColor Yellow
    $cred = Get-Credential -UserName "$env:COMPUTERNAME\$env:USERNAME" -Message "Password for the 'Wall v2' startup task"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Limited `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password `
        -Description 'Starts the wall_v2 LED display companion app at boot.' | Out-Null
    Write-Ok "Registered to run as $($cred.UserName), logged on or not."
}

Write-Host @"

Done.

  Start it now      Start-ScheduledTask -TaskName "$TaskName"
  Check it          Get-ScheduledTaskInfo -TaskName "$TaskName"
  Stop it           Stop-ScheduledTask -TaskName "$TaskName"
  Logs              $AppDir\logs\
  Remove            powershell -NoProfile -ExecutionPolicy Bypass -File .\install-autostart.ps1 -Uninstall

Give this PC a static IP or a DHCP reservation: the TLS certificate is issued
for its current address, and the phone stops trusting it if the address moves.
"@ -ForegroundColor Cyan
