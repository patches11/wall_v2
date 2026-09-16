<#
.SYNOPSIS
    Boot-safe launcher for the wall_v2 companion app.

.DESCRIPTION
    Non-interactive counterpart to start.bat: never prompts, never pauses, logs
    everything to logs\wall_<date>.log and returns a non-zero exit code on
    failure so Task Scheduler's restart-on-failure can act.

    Performs the same first-run setup start.bat does (create .env, generate the
    TLS certificate) and then runs run.py, which serves HTTPS on 443 with an
    HTTP->HTTPS redirect on 80.

.PARAMETER Python
    Interpreter to run the server with.  The scheduled task passes the absolute
    path of the dedicated venv.  When omitted, the script looks for that venv,
    then falls back to python.exe on PATH.

.PARAMETER SetupOnly
    Do the setup checks (.env, certificate) and exit without starting the
    server.  Used by install-autostart.ps1 as a smoke test.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\start-wall.ps1
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SetupOnly,
    [int]$NetworkWaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# -- Logging ----------------------------------------------------------------

$LogDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("wall_{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
    Write-Host $line
}

# Keep two weeks of logs.
Get-ChildItem $LogDir -Filter 'wall_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Log "=========================================================="
Write-Log ("Starting wall_v2 (user={0}, dir={1})" -f $env:USERNAME, $PSScriptRoot)

# Run a native command with its output appended to the log.  Redirection is
# handed to cmd.exe so PowerShell does not wrap native stderr in error records.
function Invoke-Logged {
    param([string]$Exe, [string]$Arguments)
    & cmd.exe /c "`"$Exe`" $Arguments >> `"$LogFile`" 2>&1"
    return $LASTEXITCODE
}

function Get-LocalIPv4 {
    # Same trick gen_cert.py uses: which local address would reach the internet.
    try {
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Connect('8.8.8.8', 80)
        $ip = $udp.Client.LocalEndPoint.Address.IPAddressToString
        $udp.Close()
        return $ip
    } catch {
        return $null
    }
}

try {
    # -- Resolve the interpreter -------------------------------------------

    if (-not $Python) {
        $candidates = @(
            (Join-Path $env:USERPROFILE '.venvs\wall_v2\Scripts\python.exe'),
            (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
        )
        $Python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $Python) {
            $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($cmd) { $Python = $cmd.Source }
        }
    }
    if (-not $Python -or -not (Test-Path $Python)) {
        Write-Log "No Python interpreter found. Run install-autostart.ps1, or pass -Python <path>." 'ERROR'
        exit 2
    }
    Write-Log "Interpreter: $Python"

    # -- Wait for the network (first-run cert generation needs an IP) -------

    $certExists = (Test-Path 'cert.pem') -and (Test-Path 'key.pem')
    if (-not $certExists) {
        $deadline = (Get-Date).AddSeconds($NetworkWaitSeconds)
        while (-not (Get-LocalIPv4) -and (Get-Date) -lt $deadline) {
            Write-Log "Waiting for network..."
            Start-Sleep -Seconds 3
        }
    }

    # -- .env ---------------------------------------------------------------

    if (-not (Test-Path '.env')) {
        Copy-Item '.env.example' '.env'
        $ports = @([System.IO.Ports.SerialPort]::GetPortNames())
        if ($ports.Count -eq 1) {
            (Get-Content '.env') -replace '^SERIAL_PORT=.*', "SERIAL_PORT=$($ports[0])" |
                Set-Content '.env' -Encoding ascii
            Write-Log "Created .env with SERIAL_PORT=$($ports[0])"
        } else {
            Write-Log ("Created .env from .env.example. Set SERIAL_PORT by hand - serial ports found: {0}" -f
                       $(if ($ports.Count) { $ports -join ', ' } else { 'none' })) 'WARN'
        }
    }

    # -- Certificate --------------------------------------------------------

    if (-not $certExists) {
        Write-Log "No certificate found - generating cert.pem / key.pem"
        $rc = Invoke-Logged $Python 'gen_cert.py'
        if ($rc -ne 0 -or -not (Test-Path 'cert.pem')) {
            Write-Log "Certificate generation failed (exit $rc). Is 'cryptography' installed in this interpreter?" 'ERROR'
            exit 3
        }
    }

    # -- Warn if the certificate no longer matches this machine's IP --------

    $ip = Get-LocalIPv4
    if ($ip) {
        try {
            $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 (Resolve-Path 'cert.pem').Path
            $san = $cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' }
            if ($san -and ($san.Format($false) -notmatch [regex]::Escape($ip))) {
                Write-Log ("Certificate does not cover the current IP $ip. Phones will show a warning and " +
                           "camera/motion/PWA will be blocked. Re-run gen_cert.py and reinstall cert.pem on the " +
                           "phone, then set a DHCP reservation for this PC.") 'WARN'
            }
        } catch {
            Write-Log "Could not inspect cert.pem ($($_.Exception.Message)) - skipping IP check" 'WARN'
        }
        Write-Log "Open on your phone: https://$ip"
    }

    if ($SetupOnly) {
        Write-Log "Setup complete (-SetupOnly), not starting the server."
        exit 0
    }

    # -- Refuse to start if something already owns the ports ----------------

    $busy = Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Log ("Port 443 is already in use by PID {0} - is the 'Wall v2' task already running?" -f
                   ($busy | Select-Object -First 1).OwningProcess) 'ERROR'
        exit 4
    }

    # -- Serve --------------------------------------------------------------

    Write-Log "Starting server (HTTPS 443, HTTP redirect 80)"
    $rc = Invoke-Logged $Python 'run.py'
    Write-Log "Server exited with code $rc" $(if ($rc -eq 0) { 'INFO' } else { 'ERROR' })
    exit $rc
}
catch {
    Write-Log "Unhandled error: $($_.Exception.Message)" 'ERROR'
    Write-Log $_.ScriptStackTrace 'ERROR'
    exit 1
}
