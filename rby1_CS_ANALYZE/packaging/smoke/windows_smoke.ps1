param(
    [Parameter(Mandatory = $true)][string]$PackageDir,
    [Parameter(Mandatory = $true)][string]$Fixtures
)

$ErrorActionPreference = "Stop"

$launcher = Join-Path $PackageDir "rby1-cs-analyzer-v4.exe"
$cli = Join-Path $PackageDir "rby1-cs-analyzer-v4-cli.exe"
if (-not (Test-Path $launcher) -or -not (Test-Path $cli)) {
    throw "Package entrypoints are missing from $PackageDir"
}
if (-not (Test-Path $Fixtures)) {
    throw "Representative fixtures are missing: $Fixtures"
}

$runtimeDir = Join-Path ([IO.Path]::GetTempPath()) ("rby1-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $runtimeDir | Out-Null
$env:RBY1_CS_ANALYZER_V4_DATA_ROOT = $runtimeDir
$env:http_proxy = "http://127.0.0.1:9"
$env:https_proxy = "http://127.0.0.1:9"
$env:no_proxy = "127.0.0.1,localhost,::1"

$server = $null
try {
    $selfTest = Start-Process -FilePath $launcher -ArgumentList "--self-test" -Wait -PassThru
    if ($selfTest.ExitCode -ne 0) {
        throw "Launcher self-test failed with exit code $($selfTest.ExitCode)"
    }

    & $cli validate-corpus $Fixtures --json --fail-on-crash
    if ($LASTEXITCODE -ne 0) {
        throw "CLI corpus smoke failed with exit code $LASTEXITCODE"
    }

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()

    $server = Start-Process -FilePath $launcher -ArgumentList @(
        "--no-open-browser", "--port", "$port"
    ) -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($server.HasExited) {
            throw "Launcher exited before serving the UI: $($server.ExitCode)"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -NoProxy `
                -Uri "http://127.0.0.1:$port/" -TimeoutSec 1
            if ($response.StatusCode -eq 200 -and $response.Content -match "RB-Y1 CS") {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $ready) {
        throw "Launcher did not serve the local UI"
    }

    $assets = @(
        "/models/rby1a/urdf/model_v1.2.urdf",
        "/models/rby1a/urdf/meshes/base.glb",
        "/models/rby1m/urdf/model_v1.2.urdf",
        "/models/rby1m/urdf/model_v1.3.urdf",
        "/models/rby1m/urdf/meshes/LINK_11_WY.dae",
        "/models/rby1m/urdf/meshes/base.glb"
    )
    foreach ($asset in $assets) {
        $response = Invoke-WebRequest -UseBasicParsing -NoProxy `
            -Uri "http://127.0.0.1:$port$asset" -TimeoutSec 5
        if ($response.StatusCode -ne 200 -or $response.RawContentLength -le 0) {
            throw "Packaged model asset is missing or empty: $asset"
        }
    }
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
        $server.WaitForExit()
    }
    Remove-Item -Recurse -Force $runtimeDir -ErrorAction SilentlyContinue
}

Write-Host "PASS: Windows offline onedir smoke"
