function Get-RecoveryJsonCopy([object]$Value) {
    return ConvertFrom-Json -InputObject (ConvertTo-Json -InputObject $Value -Depth 100 -Compress)
}
function Get-RecoveryCreatePlan([object]$Details, [string]$TargetImage, [string]$ExpectedName='hybrid-windows-api-1') {
    if ((Read-InstallProperty $Details 'Name') -ne ('/' + $ExpectedName)) { throw 'RECOVERY_IDENTITY_MISMATCH' }
    if ((Read-InstallProperty $Details 'Id') -notmatch '^[0-9a-f]{64}$') { throw 'RECOVERY_IDENTITY_MISMATCH' }
    $state = Read-InstallProperty $Details 'State'
    if ((Read-InstallProperty $state 'Running') -ne $true) { throw 'API_CONTAINER_NOT_RUNNING' }
    if ((Read-InstallProperty (Read-InstallProperty $state 'Health') 'Status') -ne 'healthy') { throw 'API_CONTAINER_NOT_HEALTHY' }
    $config = Read-InstallProperty $Details 'Config'
    $hostConfig = Read-InstallProperty $Details 'HostConfig'
    $labels = Read-InstallProperty $config 'Labels'
    if ((Read-InstallProperty $labels 'com.docker.compose.project') -ne 'hybrid-windows' -or
        (Read-InstallProperty $labels 'com.docker.compose.service') -ne 'api') { throw 'RECOVERY_COMPOSE_IDENTITY_MISMATCH' }
    if (@('10001','10001:10001') -notcontains (Read-InstallProperty $config 'User')) { throw 'RECOVERY_USER_UNSUPPORTED' }
    if ((Read-InstallProperty $config 'WorkingDir') -ne '/app') { throw 'RECOVERY_WORKDIR_UNSUPPORTED' }
    if ((Read-InstallProperty $hostConfig 'AutoRemove') -eq $true -or
        (Read-InstallProperty $hostConfig 'Privileged') -eq $true -or
        (Read-InstallProperty $hostConfig 'PublishAllPorts') -eq $true) { throw 'RECOVERY_HOST_OPTIONS_UNSUPPORTED' }
    $idFile = Read-InstallProperty $hostConfig 'ContainerIDFile'
    if ($idFile -is [string] -and $idFile.Length -gt 0) { throw 'RECOVERY_HOST_OPTIONS_UNSUPPORTED' }
    $volumesFrom = @(Read-InstallProperty $hostConfig 'VolumesFrom')
    if (@($volumesFrom | Where-Object { $null -ne $_ }).Count -gt 0) { throw 'RECOVERY_VOLUME_INHERITANCE_UNSUPPORTED' }
    $mode = Read-InstallProperty $hostConfig 'NetworkMode'
    $networks = Read-InstallProperty (Read-InstallProperty $Details 'NetworkSettings') 'Networks'
    if ($null -eq $networks -or @('host','none') -contains $mode -or $mode -like 'container:*') { throw 'RECOVERY_NETWORK_MODE_UNSUPPORTED' }
    $endpoints = [ordered]@{}
    foreach ($network in $networks.PSObject.Properties) {
        $ipam = Read-InstallProperty $network.Value 'IPAMConfig'
        $links = @(Read-InstallProperty $network.Value 'Links')
        if ($null -ne $ipam -or @($links | Where-Object { $null -ne $_ }).Count -gt 0) { throw 'RECOVERY_STATIC_NETWORK_UNSUPPORTED' }
        $endpoint = [ordered]@{Aliases=@(Read-InstallProperty $network.Value 'Aliases' | Where-Object { $null -ne $_ })}
        $driver = Read-InstallProperty $network.Value 'DriverOpts'
        if ($null -ne $driver) { $endpoint.DriverOpts = $driver }
        $priority = Read-InstallProperty $network.Value 'GwPriority'
        if ($null -ne $priority) { $endpoint.GwPriority = $priority }
        $endpoints[$network.Name] = $endpoint
    }
    if ($endpoints.Count -eq 0 -or -not $endpoints.Contains($mode)) { throw 'RECOVERY_NETWORK_MODE_UNSUPPORTED' }
    $bindings = Read-InstallProperty $hostConfig 'PortBindings'
    if ($null -ne $bindings) {
        foreach ($port in $bindings.PSObject.Properties) {
            foreach ($binding in @($port.Value)) {
                $number = Read-InstallProperty $binding 'HostPort'
                if ($number -isnot [string] -or $number -notmatch '^[1-9][0-9]{0,4}$' -or [int]$number -gt 65535) { throw 'RECOVERY_DYNAMIC_PORT_UNSUPPORTED' }
            }
        }
    }
    # Reject unrepresented volumes rather than allocating replacements for user data.
    $mountDeclarations = @(Read-InstallProperty $hostConfig 'Mounts' | Where-Object { $null -ne $_ })
    $bindDeclarations = @(Read-InstallProperty $hostConfig 'Binds' | Where-Object { $null -ne $_ })
    foreach ($mount in @(Read-InstallProperty $Details 'Mounts')) {
        if ($null -eq $mount) { continue }
        $kind = Read-InstallProperty $mount 'Type'
        $destination = Read-InstallProperty $mount 'Destination'
        if ($destination -eq '/app' -or $destination -match '^/app/(services|packages|integrations|configs|scripts|migrations)(/|$)') { throw 'RECOVERY_MOUNT_SHADOWS_CODE' }
        if (@('volume','bind','tmpfs') -notcontains $kind) { throw 'RECOVERY_MOUNT_TYPE_UNSUPPORTED' }
        if ($kind -eq 'volume') {
            $name = Read-InstallProperty $mount 'Name'
            $represented = $false
            foreach ($decl in $mountDeclarations) {
                if ((Read-InstallProperty $decl 'Type') -eq 'volume' -and
                    (Read-InstallProperty $decl 'Source') -eq $name -and
                    (Read-InstallProperty $decl 'Target') -eq $destination) { $represented = $true }
            }
            foreach ($decl in $bindDeclarations) {
                if ($decl.StartsWith($name + ':') -and ($decl.EndsWith(':' + $destination) -or $decl.Contains(':' + $destination + ':'))) { $represented = $true }
            }
            if (-not $represented) { throw 'RECOVERY_UNREPRESENTED_VOLUME' }
        }
    }
    $body = [ordered]@{}
    foreach ($property in $config.PSObject.Properties) { $body[$property.Name] = $property.Value }
    $body.Image = $TargetImage
    $body.HostConfig = Get-RecoveryJsonCopy $hostConfig
    $body.NetworkingConfig = @{EndpointsConfig=$endpoints}
    # This object contains original env values. It remains in memory only.
    return $body
}
function Get-RecoveryPlanDigest([object]$Plan) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Plan -Depth 100 -Compress))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Convert-RecoveryHttpResponse([string]$Raw) {
    $split = $Raw.IndexOf("`r`n`r`n")
    if ($split -lt 0) { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
    $header = $Raw.Substring(0,$split)
    if ($header -notmatch '^HTTP/1\.[01] ([0-9]{3})') { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
    $status = [int]$Matches[1]
    if ($status -lt 200 -or $status -gt 299) { throw 'RECOVERY_ENGINE_REQUEST_REJECTED' }
    $body = $Raw.Substring($split+4)
    if ($header -match '(?im)^Transfer-Encoding:\s*chunked\s*$') {
        $data = [Text.Encoding]::UTF8.GetBytes($body)
        $cursor = 0
        $stream = New-Object IO.MemoryStream
        try {
            while ($true) {
                $end = $cursor
                while ($end+1 -lt $data.Length -and -not ($data[$end] -eq 13 -and $data[$end+1] -eq 10)) { $end += 1 }
                if ($end+1 -ge $data.Length) { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
                $line = [Text.Encoding]::ASCII.GetString($data,$cursor,$end-$cursor)
                if ($line -notmatch '^[0-9a-fA-F]+$') { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
                $size = [Convert]::ToInt32($line,16)
                $cursor = $end+2
                if ($size -eq 0) { break }
                if ($size -lt 0 -or $cursor+$size+2 -gt $data.Length) { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
                $stream.Write($data,$cursor,$size)
                $cursor += $size
                if ($data[$cursor] -ne 13 -or $data[$cursor+1] -ne 10) { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
                $cursor += 2
            }
            $body = [Text.Encoding]::UTF8.GetString($stream.ToArray())
        } finally { $stream.Dispose() }
    }
    try { return ConvertFrom-Json -InputObject $body }
    catch { throw 'RECOVERY_ENGINE_RESPONSE_INVALID' }
}
function Invoke-RecoveryEngine([string]$Method,[string]$Path,[object]$Body=$null) {
    if (-not (($Method -eq 'GET' -and $Path -eq '/version') -or ($Method -eq 'POST' -and $Path -match '^/v1\.[0-9]+/containers/create\?name=[a-zA-Z0-9_.%-]+$'))) { throw 'RECOVERY_ENGINE_ROUTE_FORBIDDEN' }
    $payload = ''
    if ($null -ne $Body) { $payload = ConvertTo-Json -InputObject $Body -Depth 100 -Compress }
    $data = [Text.Encoding]::UTF8.GetBytes($payload)
    $header = "$Method $Path HTTP/1.1`r`nHost: docker`r`nConnection: close`r`nContent-Type: application/json`r`nContent-Length: $($data.Length)`r`n`r`n"
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = 'docker'
    $start.Arguments = 'system dial-stdio'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'RECOVERY_ENGINE_CONNECTION_FAILED' }
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $prefix = [Text.Encoding]::ASCII.GetBytes($header)
        $process.StandardInput.BaseStream.Write($prefix,0,$prefix.Length)
        if ($data.Length -gt 0) { $process.StandardInput.BaseStream.Write($data,0,$data.Length) }
        $process.StandardInput.BaseStream.Flush()
        if (-not $process.WaitForExit(20000)) { $process.Kill(); throw 'RECOVERY_ENGINE_TIMEOUT' }
        if ($process.ExitCode -ne 0) { throw 'RECOVERY_ENGINE_CONNECTION_FAILED' }
        return Convert-RecoveryHttpResponse $output.Result
    } finally { $process.Dispose() }
}
function New-PreservedRecoveryContainer([object]$Plan,[string]$Name) {
    if ($Name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,100}$') { throw 'RECOVERY_CANDIDATE_NAME_INVALID' }
    $version = Invoke-RecoveryEngine -Method GET -Path '/version'
    $api = Read-InstallProperty $version 'ApiVersion'
    if ($api -isnot [string] -or $api -notmatch '^1\.[0-9]+$') { throw 'RECOVERY_ENGINE_VERSION_INVALID' }
    $result = Invoke-RecoveryEngine -Method POST -Path ('/v'+$api+'/containers/create?name='+[Uri]::EscapeDataString($Name)) -Body $Plan
    $id = Read-InstallProperty $result 'Id'
    if ($id -isnot [string] -or $id -notmatch '^[0-9a-f]{64}$') { throw 'RECOVERY_CREATED_ID_INVALID' }
    return $id
}
function Read-RecoveryContainer([string]$Id) {
    if ($Id -notmatch '^[0-9a-f]{64}$') { throw 'RECOVERY_ROLLBACK_FAILED' }
    $raw = @(& docker inspect $Id 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_ROLLBACK_FAILED' }
    $parsed = ConvertFrom-Json -InputObject ($raw -join "`n")
    if ($parsed -is [System.Array]) {
        if ($parsed.Length -ne 1) { throw 'RECOVERY_ROLLBACK_FAILED' }
        $parsed = $parsed.GetValue(0)
    }
    if ((Read-InstallProperty $parsed 'Id') -ne $Id) { throw 'RECOVERY_ROLLBACK_FAILED' }
    return $parsed
}
function Restore-PreservedRecoveryContainer([string]$OriginalId,[string]$CandidateId,[string]$ContainerName,[string]$FailedName) {
    if ($ContainerName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,100}$' -or $FailedName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,100}$') { throw 'RECOVERY_ROLLBACK_FAILED' }
    $original = Read-RecoveryContainer $OriginalId
    $candidate = Read-RecoveryContainer $CandidateId
    if ((Read-InstallProperty (Read-InstallProperty $candidate 'State') 'Running') -eq $true) {
        & docker stop --time 10 $CandidateId 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_ROLLBACK_FAILED' }
    }
    & docker rename $CandidateId $FailedName 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_ROLLBACK_FAILED' }
    if ((Read-InstallProperty $original 'Name') -ne ('/' + $ContainerName)) {
        & docker rename $OriginalId $ContainerName 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_ROLLBACK_FAILED' }
    }
    & docker start $OriginalId 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_ROLLBACK_FAILED' }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $current = Read-RecoveryContainer $OriginalId
        if ((Read-InstallProperty $current 'Name') -ne ('/' + $ContainerName)) { throw 'RECOVERY_ROLLBACK_FAILED' }
        if ((Read-InstallProperty (Read-InstallProperty $current 'State') 'Running') -ne $true) { throw 'RECOVERY_ROLLBACK_FAILED' }
        $health = Read-InstallProperty (Read-InstallProperty (Read-InstallProperty $current 'State') 'Health') 'Status'
        if ($health -eq 'healthy') { return $true }
        if ($health -ne 'starting' -or $watch.Elapsed.TotalSeconds -gt 60) { throw 'RECOVERY_ROLLBACK_FAILED' }
        Start-Sleep -Seconds 2
    }
}
