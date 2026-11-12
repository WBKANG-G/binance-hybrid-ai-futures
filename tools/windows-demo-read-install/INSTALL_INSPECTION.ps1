function Read-InstallProperty([object]$Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}
function Convert-InstallInspection([string[]]$InspectOutput, [int]$InspectExitCode) {
    if ($InspectExitCode -ne 0) { throw 'CONTAINER_INSPECT_COMMAND_FAILED' }
    # PS 5.1 preserves a JSON array as one pipeline object; PS 7 may enumerate it.
    # Assign first, then normalize the actual value; @() can add another array layer.
    try { $parsed = ConvertFrom-Json -InputObject ($InspectOutput -join "`n") }
    catch { throw 'CONTAINER_INSPECT_JSON_INVALID' }
    if ($null -eq $parsed) { throw 'CONTAINER_INSPECT_SHAPE_INVALID' }
    if ($parsed -is [System.Array]) {
        if ($parsed.Length -ne 1) { throw 'CONTAINER_INSPECT_SHAPE_INVALID' }
        $details = $parsed.GetValue(0)
    } else {
        $details = $parsed
    }
    if ($null -eq $details -or $details -isnot [pscustomobject]) { throw 'CONTAINER_INSPECT_SHAPE_INVALID' }
    if ($null -eq $details.PSObject.Properties['Name']) { throw 'CONTAINER_INSPECT_SHAPE_INVALID' }
    if ((Read-InstallProperty $details 'Name') -ne '/hybrid-windows-api-1') { throw 'CONTAINER_IDENTITY_MISMATCH' }
    return $details
}
function Get-SafeInstallInspection([object]$Details) {
    $state = Read-InstallProperty $Details 'State'
    $health = Read-InstallProperty $state 'Health'
    $labels = Read-InstallProperty (Read-InstallProperty $Details 'Config') 'Labels'
    $directory = Read-InstallProperty $labels 'com.docker.compose.project.working_dir'
    $rawFiles = Read-InstallProperty $labels 'com.docker.compose.project.config_files'
    $files = @()
    if ($rawFiles -is [string] -and -not [string]::IsNullOrWhiteSpace($rawFiles)) {
        $files = @($rawFiles -split ',')
    }
    $directoryAvailable = $false
    if ($directory -is [string] -and -not [string]::IsNullOrWhiteSpace($directory)) {
        $directoryAvailable = [bool](Test-Path -LiteralPath $directory -PathType Container -ErrorAction SilentlyContinue)
    }
    $availableFiles = 0
    foreach ($file in $files) {
        if (-not [string]::IsNullOrWhiteSpace($file) -and (Test-Path -LiteralPath $file -PathType Leaf -ErrorAction SilentlyContinue)) { $availableFiles += 1 }
    }
    $status = Read-InstallProperty $health 'Status'
    if (@('healthy','starting','unhealthy') -notcontains $status) { $status = 'NOT_REPORTED' }
    return [pscustomobject]@{
        inspection = 'READ_ONLY_CONTAINER_METADATA'
        container_running = ((Read-InstallProperty $state 'Running') -eq $true)
        health_status = $status
        project_label_matches = ((Read-InstallProperty $labels 'com.docker.compose.project') -eq 'hybrid-windows')
        service_label_matches = ((Read-InstallProperty $labels 'com.docker.compose.service') -eq 'api')
        compose_working_directory_available = $directoryAvailable
        compose_config_files_total = $files.Count
        compose_config_files_available = $availableFiles
        api_replacement_started = $false
        database_changed_by_inspection = $false
        credentials_accessed = $false
    }
}
function Get-InstallComposeContext([object]$Details) {
    $state = Read-InstallProperty $Details 'State'
    if ((Read-InstallProperty $state 'Running') -ne $true) { throw 'API_CONTAINER_NOT_RUNNING' }
    $health = Read-InstallProperty $state 'Health'
    if ($null -eq $health) { throw 'API_HEALTHCHECK_MISSING' }
    $healthStatus = Read-InstallProperty $health 'Status'
    if ($healthStatus -eq 'starting') { throw 'API_CONTAINER_HEALTH_STARTING' }
    if ($healthStatus -ne 'healthy') { throw 'API_CONTAINER_NOT_HEALTHY' }
    $labels = Read-InstallProperty (Read-InstallProperty $Details 'Config') 'Labels'
    if ($null -eq $labels) { throw 'COMPOSE_LABELS_MISSING' }
    $project = Read-InstallProperty $labels 'com.docker.compose.project'
    $service = Read-InstallProperty $labels 'com.docker.compose.service'
    if ($project -ne 'hybrid-windows') { throw 'COMPOSE_PROJECT_LABEL_MISMATCH' }
    if ($service -ne 'api') { throw 'COMPOSE_SERVICE_LABEL_MISMATCH' }
    $directory = Read-InstallProperty $labels 'com.docker.compose.project.working_dir'
    if ($directory -isnot [string] -or [string]::IsNullOrWhiteSpace($directory)) { throw 'COMPOSE_WORKING_DIRECTORY_LABEL_MISSING' }
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw 'COMPOSE_WORKING_DIRECTORY_NOT_FOUND' }
    $rawFiles = Read-InstallProperty $labels 'com.docker.compose.project.config_files'
    if ($rawFiles -isnot [string] -or [string]::IsNullOrWhiteSpace($rawFiles)) { throw 'COMPOSE_CONFIG_FILES_LABEL_MISSING' }
    $files = @($rawFiles -split ',')
    foreach ($file in $files) {
        if ([string]::IsNullOrWhiteSpace($file)) { throw 'COMPOSE_CONFIG_FILES_INVALID' }
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'COMPOSE_CONFIG_FILE_NOT_FOUND' }
    }
    $baseId = Read-InstallProperty $Details 'Image'
    if ($baseId -isnot [string] -or $baseId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'BASE_IMAGE_ID_INVALID' }
    # These paths are used internally only. Never print this object or raw inspect JSON.
    return [pscustomobject]@{Project=$project; Service=$service; Directory=$directory; Files=$files; BaseId=$baseId}
}
