#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Snapshot,
    [Parameter(Mandatory)][string]$SshHost,
    [string]$Deployer,
    [string]$IdentityFile
)
$ErrorActionPreference = 'Stop'
$base = $PSScriptRoot
$log = Join-Path $base 'rime-bridge.log'
try {
    if ($Deployer) {
        $started = [DateTime]::UtcNow
        $process = Start-Process -FilePath $Deployer -ArgumentList '/sync' -WindowStyle Hidden -PassThru
        if (!$process.WaitForExit(120000) -or $process.ExitCode -ne 0 -or (Get-Item -LiteralPath $Snapshot).LastWriteTimeUtc -lt $started.AddSeconds(-2)) {
            throw 'Rime did not generate a fresh snapshot.'
        }
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $rows = [Collections.Generic.List[string]]::new()
    foreach ($line in [IO.File]::ReadLines($snapshot, $utf8)) {
        if (!$line -or $line.StartsWith('#')) { continue }
        $cols = $line.Split([char]9)
        if ($cols.Count -ne 3) { throw 'Unexpected Rime snapshot format.' }
        $count = [regex]::Match($cols[2], '(?:^|\s)c=(-?\d+)')
        if (!$count.Success -or [int]$count.Groups[1].Value -le 0) { continue }
        $code = $cols[0].Trim()
        $word = $cols[1]
        if ($word -notmatch '^\p{IsCJKUnifiedIdeographs}+$' -or $code -cnotmatch '^[a-z]+(?: [a-z]+)*$') { continue }
        $parts = $code.Split(' ')
        if ($parts.Count -ne $word.Length -or ($parts | Where-Object {$_ -notmatch '[aeiouv]'})) { continue }
        $rows.Add($word + [char]9 + ($parts -join "'") + [char]9 + $count.Groups[1].Value)
    }
    if (!$rows.Count) { throw 'The converted snapshot was empty.' }
    $sshArgs = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
    if ($IdentityFile) { $sshArgs += @('-o', 'IdentitiesOnly=yes', '-i', $IdentityFile) }
    if ($SshHost -notmatch '^[a-zA-Z0-9_.@-]+$' -or $SshHost.StartsWith('-')) { throw 'Invalid SSH host.' }
    $OutputEncoding = $utf8
    $result = ($rows -join [char]10) | & ssh @sshArgs $SshHost 'python3 "$HOME/rime-msime-bridge/bridge.py" publish' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "NAS did not confirm publication (exit $LASTEXITCODE): $result" }
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $result"
    $result
} catch {
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) ERROR $($_.Exception.Message)"
    throw
}
