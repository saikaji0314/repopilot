param([switch]$Build)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$desktopRoot=Join-Path $repoRoot 'desktop'
$nodeCommand=Get-Command node -ErrorAction Stop
if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot 'node_modules/electron/cli.js'))) {
    throw '请先在 desktop 目录运行 pnpm install --frozen-lockfile。'
}
Push-Location -LiteralPath $desktopRoot
try {
    if ($Build) {
        & $nodeCommand.Source 'node_modules/typescript/bin/tsc' '--noEmit'
        if ($LASTEXITCODE -ne 0) { throw 'TypeScript 检查未通过。' }
        & $nodeCommand.Source 'node_modules/vite/bin/vite.js' 'build'
        if ($LASTEXITCODE -ne 0) { throw '界面构建未通过。' }
    }
    if (-not (Test-Path -LiteralPath 'out/index.html')) { throw '请先运行此脚本的 -Build 选项。' }
    & $nodeCommand.Source 'scripts/launch.cjs'
    if ($LASTEXITCODE -ne 0) { throw "Electron 启动失败，退出码 $LASTEXITCODE。请见 desktop/README.md 的已知限制。" }
} finally { Pop-Location }
