#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$SyncRoot,
    [Parameter(Mandatory)][string]$Snapshot,
    [Parameter(Mandatory)][string]$Deployer,
    [Parameter(Mandatory)][array]$Incoming,
    [Parameter(Mandatory)][Collections.Generic.HashSet[string]]$Existing
)

$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$device = 'rime-msime-bridge'
$directory = Join-Path $SyncRoot $device
$target = Join-Path $directory 'rime_ice.userdb.txt'
if (Test-Path -LiteralPath $target) {
    throw 'An earlier bridge snapshot is still present. Inspect it before retrying.'
}
$rows = [Collections.Generic.List[string]]::new()
$verify = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($pair in $Incoming) {
    $word, $code = $pair
    if ($word -cnotmatch '^\p{IsCJKUnifiedIdeographs}+$' -or $code -cnotmatch "^[a-z]+(?:'[a-z]+)*$") {
        throw 'Invalid incoming word or spelling.'
    }
    $parts = $code.Split("'")
    if ($parts.Count -ne $word.Length) { throw 'Incoming word and spelling have different lengths.' }
    $spaced = $parts -join ' '
    $key = $spaced + [char]9 + $word
    if (!$Existing.Contains($key) -and $verify.Add($key)) {
        $rows.Add($spaced + ' ' + [char]9 + $word + [char]9 + 'c=1 d=0 t=1')
    }
}
if (!$rows.Count) { return 0 }
$null = New-Item -ItemType Directory -Path $directory -Force
$metadata = @(
    '# Rime user dictionary'
    "#@/db_name`trime_ice"
    "#@/db_type`tuserdb"
    "#@/tick`t1"
    "#@/user_id`t$device"
)
$pending = Join-Path $directory ([guid]::NewGuid().ToString() + '.tmp')
[IO.File]::WriteAllLines($pending, [string[]]($metadata + ($rows | Sort-Object -CaseSensitive)), $utf8)
[IO.File]::Move($pending, $target)
try {
    $started = [DateTime]::UtcNow
    $process = Start-Process -FilePath $Deployer -ArgumentList '/sync' -WindowStyle Hidden -PassThru
    if (!$process.WaitForExit(120000) -or $process.ExitCode -ne 0 -or (Get-Item -LiteralPath $Snapshot).LastWriteTimeUtc -lt $started.AddSeconds(-2)) {
        throw 'Rime did not generate a fresh snapshot after merging.'
    }
    foreach ($line in [IO.File]::ReadLines($Snapshot, $utf8)) {
        if ($line.StartsWith('#') -or !$line) { continue }
        $cols = $line.Split([char]9)
        if ($cols.Count -eq 3 -and $cols[2] -match '(?:^|\s)c=[1-9]\d*(?:\s|$)') {
            [void]$verify.Remove($cols[0].Trim() + [char]9 + $cols[1])
        }
    }
    if ($verify.Count) { throw "Rime did not confirm $($verify.Count) incoming words." }
    return $rows.Count
} finally {
    # This file belongs to the current transfer. Do not leave it to resurrect deleted words.
    Remove-Item -LiteralPath $target
}
