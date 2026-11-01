<#
.SYNOPSIS
  hermes-deploy - one command that builds the Hermes environment in WSL or on a remote Linux host and
  points VS Code on Windows at it. Windows PowerShell 5.1 and PowerShell 7, built-in OpenSSH.

.DESCRIPTION
  powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -HostName <addr> [-Name <alias>] [options]   remote host
  powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -Name <alias> [options]     host already in ssh config
  powershell -ExecutionPolicy Bypass -File .\hermes-deploy.ps1 -Local [-Distro Ubuntu]     this PC, inside WSL

  Remote:  -User hermes  -Port 22  -Key ~\.ssh\id_ed25519  -RootLogin (connect as root first: packages, create
           -User, authorize the key, linger)  -SkipSystem
  Hermes:  -Repo <fork>  -Ref main  -Dir ~/repos/hermes-agent  -Workspace <= Dir>  -Clis claude,codex,gemini,agy
           -Provider claude-cli|none  -Update  -WithBrowser  -Gateway
  State:   -Restore <backup-pull .tar.gz>  -CloneFrom <alias> (snapshot a live host now)
  IDE:     -Ide code|none  -NoOpen  -IdeOnly (only the Windows side: ssh config, extensions, settings, open)
  -DryRun: checks and the plan only, writes nothing.

  Hermes, tmux sessions, the gateway service and the subscription CLIs are Linux-side, so a local deploy
  goes into WSL; VS Code stays on Windows (Remote-SSH or WSL). Native Windows Hermes is the official
  installer (scripts/install.ps1). The target half is bootstrap.sh next to this file; run from a single
  downloaded file, the rest is fetched from -Repo at -Ref. Rerunning is safe; an existing checkout is
  left alone unless -Update. Subscription logins stay with each CLI: no token is copied.
#>
param(
    [string]$HostName = "",
    [string]$Name = "",
    [string]$User = "hermes",
    [int]$Port = 22,
    [string]$Key = "",
    [switch]$RootLogin,
    [switch]$SkipSystem,
    [switch]$Local,
    [string]$Distro = "",
    [string]$Repo = "https://github.com/StApZzZ/hermes-agent.git",
    [string]$Ref = "main",
    [string]$Dir = "",
    [string]$Workspace = "",
    [string]$Clis = "",
    [string]$Provider = "",
    [switch]$Update,
    [switch]$WithBrowser,
    [switch]$Gateway,
    [string]$Restore = "",
    [string]$CloneFrom = "",
    [ValidateSet("code", "none")][string]$Ide = "code",
    [switch]$NoOpen,
    [switch]$IdeOnly,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$utf8 = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8

function Say($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Fail($t, $code) { Write-Host $t -ForegroundColor Red; exit $code }
# Remote commands travel as one string: single-quote each word for the remote shell. PowerShell 5.1
# re-quotes native arguments that contain double quotes, so values must not carry any.
function Q([string]$s) { if ($s.Contains('"')) { Fail "a value contains a double quote: $s" 2 }; "'" + $s.Replace("'", "'\''") + "'" }
function Native([scriptblock]$b) { & $b; if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" } }

$Remote = [bool]($HostName -or $Name)
if ($Local -and $Remote) { Fail "-Local or -HostName/-Name, not both" 2 }
if (-not $Local -and -not $Remote) { Fail "pass -HostName/-Name for a remote host, or -Local for WSL (Get-Help $PSCommandPath)" 2 }
if ($Restore -and $CloneFrom) { Fail "-Restore or -CloneFrom, not both" 2 }
if ($Restore -and -not (Test-Path -LiteralPath $Restore)) { Fail "no archive $Restore" 2 }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { Fail "no ssh: Settings > Apps > Optional features > OpenSSH Client" 2 }
$Home_ = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$Cache = Join-Path $(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $Home_ ".cache" }) "hermes-deploy"
$Work = Join-Path ([IO.Path]::GetTempPath()) ("hermes-deploy-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
# Created only for downloads and the -CloneFrom snapshot (which holds ~/.hermes/.env); removed on exit.
function WorkDir {
    if (-not (Test-Path $Work)) {
        New-Item -ItemType Directory -Force -Path $Work | Out-Null
        Register-EngineEvent PowerShell.Exiting -MessageData $Work -Action { Remove-Item -Recurse -Force $Event.MessageData -ErrorAction SilentlyContinue } | Out-Null
    }
    $Work
}

# ---------------------------------------------------------------- bundle
$Files = @("bootstrap.sh", "vscode_settings.py", "ide/machine-settings.json", "ide/vscode/local.txt", "ide/vscode/remote.txt")
function RawBase {
    $p = $Repo -replace '\.git$', ''
    if ($p -notmatch '^https://github\.com/(.+)$') { Fail "a single-file run needs a GitHub -Repo; run from a checkout instead" 3 }
    "https://raw.githubusercontent.com/$($Matches[1])/$Ref"
}
$Bundle = $PSScriptRoot
if (-not $Bundle -or -not (Test-Path (Join-Path $Bundle "bootstrap.sh"))) {
    $Bundle = Join-Path (WorkDir) "bundle"
    $base = "$(RawBase)/scripts/deploy"
    foreach ($f in $Files) {
        $dst = Join-Path $Bundle $f
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        Invoke-WebRequest -UseBasicParsing "$base/$f" -OutFile $dst
    }
}
function ExtList($file) {
    Get-Content (Join-Path $Bundle "ide/vscode/$file") | ForEach-Object { ($_ -replace '#.*', '').Trim() } | Where-Object { $_ }
}

$UserArgs = @("--repo", $Repo, "--ref", $Ref)
if ($Dir) { $UserArgs += @("--dir", $Dir) }
if ($Workspace) { $UserArgs += @("--workspace", $Workspace) }
if ($Clis) { $UserArgs += @("--clis", $Clis) }
if ($Provider) { $UserArgs += @("--provider", $Provider) }
if ($Update) { $UserArgs += "--update" }
if ($WithBrowser) { $UserArgs += "--with-browser" }
if ($Gateway) { $UserArgs += "--gateway" }

# --CloneFrom: the snapshot backup-pull.sh takes. cmd.exe pipes bytes; a PowerShell 5.1 pipe would re-encode the tar.
if ($CloneFrom -and -not $DryRun -and -not $IdeOnly) {
    Say "Snapshot of $CloneFrom"
    $snap = Join-Path $PSScriptRoot "..\remote-dev\backup-snapshot.sh"
    if (-not $PSScriptRoot -or -not (Test-Path $snap)) {
        $snap = Join-Path (WorkDir) "backup-snapshot.sh"
        Invoke-WebRequest -UseBasicParsing "$(RawBase)/scripts/remote-dev/backup-snapshot.sh" -OutFile $snap
    }
    $Restore = Join-Path (WorkDir) "clone.tar.gz"
    Native { cmd /c "ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 $CloneFrom bash -s < `"$snap`" > `"$Restore`"" }
    Write-Host ("{0:N0} MB" -f ((Get-Item $Restore).Length / 1MB))
    if ($Gateway) { Write-Host "note: $CloneFrom's gateway polls the same bot tokens - stop one of the two" -ForegroundColor Yellow }
}

# ---------------------------------------------------------------- VS Code (Windows side)
function CodeCli {
    $c = Get-Command code -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd", "$env:ProgramFiles\Microsoft VS Code\bin\code.cmd")) {
        if (Test-Path $p) { return $p }
    }
    $null
}
function StripJsonc([string]$text) {
    # comments and trailing commas outside strings: VS Code's settings.json is JSONC
    $sb = New-Object System.Text.StringBuilder; $i = 0; $n = $text.Length; $s = $false; $e = $false
    while ($i -lt $n) {
        $c = $text[$i]
        if ($s) { [void]$sb.Append($c); $s = $e -or ($c -ne '"'); $e = (-not $e) -and ($c -eq '\') }
        elseif ($c -eq '"') { $s = $true; [void]$sb.Append($c) }
        elseif ($c -eq '/' -and $i + 1 -lt $n -and $text[$i + 1] -eq '/') { while ($i -lt $n -and $text[$i] -ne "`n") { $i++ }; continue }
        elseif ($c -eq '/' -and $i + 1 -lt $n -and $text[$i + 1] -eq '*') { $j = $text.IndexOf("*/", $i + 2); if ($j -lt 0) { $i = $n } else { $i = $j + 2 }; continue }
        else { [void]$sb.Append($c) }
        $i++
    }
    $t = $sb.ToString(); $sb = New-Object System.Text.StringBuilder; $s = $false; $e = $false
    for ($j = 0; $j -lt $t.Length; $j++) {
        $c = $t[$j]
        if ($s) { $s = $e -or ($c -ne '"'); $e = (-not $e) -and ($c -eq '\') }
        elseif ($c -eq '"') { $s = $true }
        elseif ($c -eq ',' -and $t.Substring($j + 1).TrimStart().Length -gt 0 -and '}]'.Contains($t.Substring($j + 1).TrimStart()[0])) { continue }
        [void]$sb.Append($c)
    }
    $sb.ToString()
}
function ToHash($o) {
    # ConvertFrom-Json gives PSCustomObject in 5.1; ordered hashtables merge and serialize predictably
    if ($o -is [System.Management.Automation.PSCustomObject]) {
        $h = [ordered]@{}; foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = ToHash $p.Value }; return $h
    }
    if ($o -is [System.Collections.IEnumerable] -and $o -isnot [string]) { return , @($o | ForEach-Object { ToHash $_ }) }
    $o
}
function MergeSettings($path, $patch) {
    $orig = if (Test-Path -LiteralPath $path) { [IO.File]::ReadAllText($path) } else { "" }
    $cur = if ((StripJsonc $orig).Trim()) { ToHash (ConvertFrom-Json (StripJsonc $orig)) } else { [ordered]@{} }
    foreach ($k in $patch.Keys) {
        $v = $patch[$k]
        if ($v -is [System.Collections.IDictionary] -and $cur[$k] -is [System.Collections.IDictionary]) {
            foreach ($kk in $v.Keys) { $cur[$k][$kk] = $v[$kk] }
        } elseif ($v -is [array] -and $cur[$k] -is [array]) {
            $have = @($cur[$k] | ForEach-Object { "$_".ToLower() })
            $cur[$k] = @($cur[$k]) + @($v | Where-Object { $have -notcontains "$_".ToLower() })
        } else { $cur[$k] = $v }
    }
    New-Item -ItemType Directory -Force -Path $Cache, (Split-Path $path) | Out-Null
    $bak = Join-Path $Cache "vscode-settings.json.bak"
    if ($orig -and -not (Test-Path $bak)) { [IO.File]::WriteAllText($bak, $orig, $utf8) }   # the first original, with comments
    [IO.File]::WriteAllText($path, (ConvertTo-Json -InputObject $cur -Depth 20) + "`n", $utf8)
    Write-Host "  ${path}: $($cur.Count) keys"
}
function InstallExts($cli, $ids, [string[]]$extra) {
    # code.cmd prints Node deprecation warnings on stderr; under "Stop" 5.1 makes redirected stderr an exception
    $ErrorActionPreference = "Continue"
    foreach ($e in $ids) {
        & $cli @extra --install-extension $e --force *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host "  $e - ok" } else { Write-Host "  $e - failed" -ForegroundColor Yellow }
    }
}

# ---------------------------------------------------------------- local: WSL
if ($Local) {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) { Fail "no WSL: wsl --install -d Ubuntu, reboot, rerun" 2 }
    $W = @(); if ($Distro) { $W = @("-d", $Distro) }
    $inner = "bash ~/.cache/hermes-deploy/bootstrap.sh user " + (($UserArgs + @("--vscode-settings", "machine", "--acp-name", "Hermes")) | ForEach-Object { Q $_ }) -join " "
    if ($DryRun) {
        Say "Plan (-DryRun)"
        if (-not $SkipSystem) { Write-Host "wsl $W bootstrap.sh system" }
        Write-Host "wsl $W $inner"
        Write-Host "VS Code (WSL): $((ExtList remote.txt) -join ' ')"
        exit 0
    }
    # wslpath turns the Windows bundle path into /mnt/c/...; bootstrap and its files are copied, not run from /mnt.
    $wb = (& wsl @W -- wslpath -a ($Bundle -replace '\\', '/')).Trim()
    Native { & wsl @W -- bash -c "umask 077; rm -rf ~/.cache/hermes-deploy/ide; mkdir -p ~/.cache/hermes-deploy && cp -r $(Q $wb)/. ~/.cache/hermes-deploy/" }
    if (-not $SkipSystem) { Native { & wsl @W -- bash ~/.cache/hermes-deploy/bootstrap.sh system } }
    if ($Restore) {
        $wr = (& wsl @W -- wslpath -a ((Resolve-Path $Restore).Path -replace '\\', '/')).Trim()
        $inner += " --restore " + (Q $wr)
    }
    Native { & wsl @W -- bash -c $inner }
    $home_wsl = (& wsl @W -- bash -c 'printf %s $HOME').Trim()
    $ws = if ($Workspace) { $Workspace } elseif ($Dir) { $Dir } else { "$home_wsl/repos/hermes-agent" }
    $ws = $ws -replace '^~', $home_wsl
    if ($Ide -eq "code") {
        Say "VS Code (WSL)"
        $cli = CodeCli
        if (-not $cli) { Write-Host "no VS Code: install it (winget install Microsoft.VisualStudioCode), then rerun" }
        else {
            InstallExts $cli @("ms-vscode-remote.remote-wsl") @()
            # `code` inside WSL is the Windows wrapper and installs into the WSL server, where the workspace lives.
            foreach ($e in (ExtList remote.txt)) { & wsl @W -- bash -lc "code --install-extension $(Q $e) --force >/dev/null 2>&1 && echo '  $e - ok' || echo '  $e - failed'" }
            $dn = (& wsl @W -- bash -c 'printf %s $WSL_DISTRO_NAME').Trim()
            if (-not $NoOpen) { & $cli --remote "wsl+$dn" $ws }
        }
    }
    Say "Done"
    Write-Host "Sign in once per CLI in WSL: claude auth login / codex login --device-auth / agy"
    exit 0
}

# ---------------------------------------------------------------- remote
if (-not $Name) { $Name = "hermes-" + ($HostName -replace '[^A-Za-z0-9_-]', '-') }
if (-not $Key) {
    foreach ($k in @("id_ed25519", "id_ecdsa", "id_rsa")) { $p = Join-Path $Home_ ".ssh\$k"; if (Test-Path $p) { $Key = $p; break } }
}
$Key = $Key -replace '\.pub$', ''
$SshBase = @("-o", "ConnectionAttempts=3", "-o", "ServerAliveInterval=30")
# -o, not -l: scp's -l is a bandwidth limit. Passed first: ssh keeps the first value, and -DryRun adds -o User.
$AsRoot = @("-o", "User=root")
$Tty = @(); if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) { $Tty = @("-t") }
$CfgManual = $false

if ($HostName) {
    if (-not $Key -or -not (Test-Path $Key)) { Fail "no SSH key: pass -Key (ssh-keygen -t ed25519 makes one)" 4 }
    Say "SSH: Host $Name -> $User@${HostName}:$Port ($Key)"
    $SshCfg = Join-Path $Home_ ".ssh\config"
    $keyPath = $Key -replace '\\', '/'
    # First in the file: ssh takes the first value it finds. "Match all" closes the block so global
    # options and Includes that used to open the file apply to every host again.
    $block = "# >>> hermes-deploy $Name`nHost $Name`n  HostName $HostName`n  User $User`n  Port $Port`n  IdentityFile $keyPath`n  IdentitiesOnly yes`n  ServerAliveInterval 30`nMatch all`n# <<< hermes-deploy $Name"
    $direct = @("-o", "HostName=$HostName", "-o", "User=$User", "-o", "Port=$Port", "-o", "IdentityFile=$keyPath", "-o", "IdentitiesOnly=yes")
    if ($DryRun) {
        Write-Host $block; Write-Host "(-DryRun: not written)"
        $SshBase += $direct
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path $SshCfg), $Cache | Out-Null
        $text = if (Test-Path $SshCfg) { [IO.File]::ReadAllText($SshCfg) } else { "" }
        $bak = Join-Path $Cache "ssh-config.bak"
        if ($text -and -not (Test-Path $bak)) { [IO.File]::WriteAllText($bak, $text, $utf8) }
        $text = [regex]::Replace($text, "(?s)# >>> hermes-deploy $([regex]::Escape($Name))\r?\n.*?# <<< hermes-deploy $([regex]::Escape($Name))\r?\n?", "")
        try {
            # no BOM: OpenSSH would read it as part of the first keyword
            [IO.File]::WriteAllText($SshCfg, $block + "`n`n" + $text.TrimStart("`r", "`n"), $utf8)
            Write-Host "written (original kept in $bak)"
        } catch {
            # A read-only config (attribute or ACL) is the owner's choice: go on with the same values as -o.
            $CfgManual = $true
            $SshBase += $direct
            Write-Host "cannot write $SshCfg ($($_.Exception.InnerException.Message)) - going on without it." -ForegroundColor Yellow
            Write-Host "For VS Code, add this block at the top of that file yourself:" -ForegroundColor Yellow
            Write-Host $block
        }
    }
}
function SshErr($err, $who) {
    Write-Host (($err | Select-Object -Last 2) -join "`n") -ForegroundColor Red
    if ("$err" -match "Permission denied") { Write-Host "the host refused the key: authorize $Key.pub for $who, or use -RootLogin" }
    elseif ("$err" -match "timed out|refused|no route|unreachable") { Write-Host "host unreachable: check the address and the SSH allowlist" }
    exit 5
}
function SshCheck([string[]]$extra, $who) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $err = & ssh @extra @SshBase -n -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new $Name true 2>&1
    $code = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($code -ne 0) { SshErr $err $who }
}
function UploadBundle([string[]]$extra) {
    # scp, not a tar pipe: Windows PowerShell 5.1 re-encodes bytes between native commands
    Native { & ssh @extra @SshBase -n $Name "umask 077; rm -rf ~/.cache/hermes-deploy/ide; mkdir -p ~/.cache/hermes-deploy" }
    $items = @("bootstrap.sh", "vscode_settings.py", "ide") | ForEach-Object { Join-Path $Bundle $_ }
    Native { & scp -q -r @extra @SshBase @items "${Name}:.cache/hermes-deploy/" }
}

$Rcmd = "bash ~/.cache/hermes-deploy/bootstrap.sh"
if (-not $IdeOnly) {
    if ($RootLogin) {
        Say "System stage as root@$Name"
        if (-not (Test-Path "$Key.pub")) { Fail "no $Key.pub to authorize for $User" 4 }
        SshCheck $AsRoot "root"
        if ($DryRun) { Write-Host "would run: bootstrap.sh system --user $User --pubkey <$Key.pub>" }
        else {
            UploadBundle $AsRoot
            $pub = ([IO.File]::ReadAllText("$Key.pub")).Trim()
            Native { & ssh @AsRoot @SshBase @Tty $Name "$Rcmd system --user $(Q $User) --pubkey $(Q $pub)" }
        }
    }
    Say "Login as $User@$Name"
    if ($DryRun -and $RootLogin) { Write-Host "(-DryRun: $User may not exist yet)" } else { SshCheck @() $User; Write-Host "ok" }
    if ($DryRun) {
        Say "Plan (-DryRun)"
        if (-not $SkipSystem -and -not $RootLogin) { Write-Host "${Name}: bootstrap.sh system (sudo only for missing packages)" }
        if ($Restore -or $CloneFrom) { Write-Host "upload $(if ($Restore) { $Restore } else { "snapshot of $CloneFrom" }) -> restore" }
        Write-Host "${Name}: bootstrap.sh user $($UserArgs -join ' ') --vscode-settings machine"
        Write-Host "VS Code here: $((ExtList local.txt) -join ' '); defaultExtensions: $((ExtList remote.txt) -join ' ')"
        exit 0
    }
    UploadBundle @()
    if (-not $SkipSystem -and -not $RootLogin) {
        Say "System stage on $Name"
        Native { & ssh @SshBase @Tty $Name "$Rcmd system" }
    }
}
if ($IdeOnly) { SshCheck @() $User }   # first contact from Windows: accepts the new host key without a prompt
$rhome = (& ssh @SshBase -n -o BatchMode=yes $Name 'printf %s $HOME')
if ($LASTEXITCODE -ne 0) { SshCheck @() $User }
if (-not $IdeOnly) {
    $vsc = if ($Ide -eq "code") { "machine" } else { "none" }
    $ua = $UserArgs + @("--vscode-settings", $vsc, "--acp-name", "Hermes Remote")
    if ($Restore) {
        Say "Uploading $(Split-Path $Restore -Leaf) ($('{0:N0}' -f ((Get-Item $Restore).Length / 1MB)) MB)"
        Native { & scp -q @SshBase $Restore "${Name}:.cache/hermes-deploy/restore.tar.gz" }
        $ua += @("--restore", "$rhome/.cache/hermes-deploy/restore.tar.gz")
    }
    & ssh @SshBase @Tty $Name ("$Rcmd user " + (($ua | ForEach-Object { Q $_ }) -join " "))
    $rc = $LASTEXITCODE
    if ($Restore) { & ssh @SshBase -n $Name "rm -f ~/.cache/hermes-deploy/restore.tar.gz" }
    if ($rc -ne 0) { Fail "user stage failed on $Name (exit $rc)" $rc }
}

$ws = if ($Workspace) { $Workspace } elseif ($Dir) { $Dir } else { "$rhome/repos/hermes-agent" }
$ws = $ws -replace '^~', $rhome
if ($Ide -eq "code") {
    Say "VS Code"
    $cli = CodeCli
    if (-not $cli) { Write-Host "no VS Code: install it (winget install Microsoft.VisualStudioCode), then rerun with -IdeOnly" }
    else {
        InstallExts $cli (ExtList local.txt) @()
        $settings = Join-Path $env:APPDATA "Code\User\settings.json"
        MergeSettings $settings ([ordered]@{
            "remote.SSH.defaultExtensions" = @(ExtList remote.txt)
            "remote.SSH.remotePlatform"    = [ordered]@{ $Name = "linux" }
        })
        if ($CfgManual) { Write-Host "Not opening: VS Code finds $Name only after the Host block above is in $SshCfg." -ForegroundColor Yellow }
        elseif (-not $NoOpen) { & $cli --folder-uri "vscode-remote://ssh-remote+$Name$ws" }
    }
}
Say "Done: ${Name}:$ws"
if (-not $IdeOnly) {
    Write-Host "Sign in once per CLI on the host (the browser opens here):"
    Write-Host "  ssh -t $Name claude auth login    ssh -t $Name codex login --device-auth    ssh -t $Name agy"
}
