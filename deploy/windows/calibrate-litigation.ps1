. (Join-Path $PSScriptRoot 'common.ps1')

$root = Get-ProjectRoot
$python = Get-WorkerPython
$edge = Get-EdgePath
$profileDir = Get-WorkerProfileDir
$logDir = Get-WorkerLogDir

if (-not (Test-Path -LiteralPath (Join-Path $profileDir 'Default'))) {
    throw '未找到专用 Edge 登录会话，请先运行 login.ps1。'
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$output = Join-Path $logDir ("litigation-calibration-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
Push-Location $root
try {
    & $python -u -m enterprise_sentinel.company_profile.calibrate `
        --user-data-dir $profileDir `
        --profile-directory 'Default' `
        --browser-path $edge `
        --output-json $output
    if ($LASTEXITCODE -ne 0) { throw "校准异常退出，代码：$LASTEXITCODE" }
} finally {
    Pop-Location
}
