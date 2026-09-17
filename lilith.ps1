<#! Launch the installed workspace without changing the user's project directory. !#>
$ErrorActionPreference = 'Stop'
$env:LILITH_PROVIDER_OVERRIDE = 'fabric'
foreach ($name in @('YGGDRASIL_FABRIC_TOKEN','MIMIR_EXPERIENTIAL_API_KEY')) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, 'Process'))) {
        $userValue = [Environment]::GetEnvironmentVariable($name, 'User')
        if (-not [string]::IsNullOrWhiteSpace($userValue)) { Set-Item -Path ("env:" + $name) -Value $userValue }
    }
}
$entry = Join-Path $PSScriptRoot '.venv\Scripts\lilith.exe'
$releaseRoot = if ($env:LILITH_RELEASES_DIR) { $env:LILITH_RELEASES_DIR } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.yggdrasil\lilith-releases' }
$pointer = Join-Path $releaseRoot 'active.yaml'
if (Test-Path -LiteralPath $pointer -PathType Leaf) {
    $active = Get-Content -LiteralPath $pointer | Where-Object { $_ -match '^active: [a-f0-9]{40}$' } | Select-Object -First 1
    if (-not $active) { throw 'El indicador de versión activa es inválido.' }
    $commitId = $active.Substring(8)
    $entry = Join-Path $releaseRoot "$commitId\.venv\Scripts\lilith.exe"
}
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    throw 'Primero instala desde el repositorio: uv sync --locked --all-packages --extra dev'
}
& $entry @args
exit $LASTEXITCODE
