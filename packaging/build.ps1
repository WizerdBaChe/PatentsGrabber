# 打包成一個資料夾，裡面只有一個 PatentsGrabber.exe 要按。
#
#   powershell -File packaging\build.ps1              一般打包（含 gate 與實跑檢查）
#   powershell -File packaging\build.ps1 -Clean       先清掉 build\ 再打包
#   powershell -File packaging\build.ps1 -SkipGates   跳過 gate（不建議）
#   powershell -File packaging\build.ps1 -SkipSmoke   跳過打包後的實跑檢查（強烈不建議）
#
# 產出：
#   build\dist\PatentsGrabber\PatentsGrabber.exe     直接執行
#   release\PatentsGrabber-<版本>-win64.zip           發布用附件

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipGates,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Continue"   # 原生指令一律自己檢查 $LASTEXITCODE
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Note($t) { Write-Host "  $t" }
function Fail($t) { Write-Host "`nFAILED: $t" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 版本
# 版本只有一個來源：app.py 的 VERSION。壓縮檔名、release tag 都從這裡讀，
# 免得三個地方各寫一次然後對不起來。
$version = & python -c "import re,pathlib; print(re.search(r'VERSION = \""([^\""]+)\""', pathlib.Path('src/patentsgrabber/app.py').read_text(encoding='utf-8')).group(1))"
if ($LASTEXITCODE -ne 0 -or -not $version) { Fail "讀不到 src/patentsgrabber/app.py 的 VERSION" }
$version = $version.Trim()
Note "版本 $version"

# ---------------------------------------------------------------- 相依套件
Step "檢查相依套件"
$missing = @()
foreach ($mod in @("fastapi", "uvicorn", "httpx", "bs4", "lxml", "dotenv", "PIL", "pypdf", "PyInstaller")) {
    & python -c "import $mod" 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += $mod }
}
if ($missing.Count -gt 0) { Fail "缺少套件：$($missing -join ', ')。先跑 pip install -r requirements.txt pyinstaller" }
$pyiVersion = & python -c "import PyInstaller; print(PyInstaller.__version__)"
Note "PyInstaller $pyiVersion"

# ---------------------------------------------------------------- gate
if ($SkipGates) {
    Write-Host "`n(已跳過 gate)" -ForegroundColor Yellow
} else {
    Step "本機 gate（不連網、不花 OPS 配額）"
    & python tools\run_gates.py
    if ($LASTEXITCODE -ne 0) { Fail "gate 沒過（exit $LASTEXITCODE）。要照樣打包請加 -SkipGates" }
}

# ---------------------------------------------------------------- 清理
if ($Clean) {
    Step "清理舊的建置產物"
    foreach ($d in @("build")) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d; Note "已刪除 $d" }
    }
}

# ---------------------------------------------------------------- 打包
Step "PyInstaller（第一次大約 1-3 分鐘）"
# PyInstaller 把 INFO 寫到 stderr。讓它流回 PowerShell 會被包成 NativeCommandError，
# 看起來像壞了其實沒事，所以整份導到記錄檔，只在失敗時印出來。
$buildLog = Join-Path $root "build\pyinstaller.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "build") | Out-Null
$sw = [Diagnostics.Stopwatch]::StartNew()
& python -m PyInstaller --noconfirm --clean `
    --distpath build\dist --workpath build\work `
    packaging\patentsgrabber.spec *> $buildLog
$code = $LASTEXITCODE
$sw.Stop()

if ($code -ne 0) {
    Write-Host "`n--- PyInstaller 記錄（最後 40 行）---" -ForegroundColor Yellow
    Get-Content $buildLog -Tail 40 | ForEach-Object { Write-Host "  $_" }
    Fail "PyInstaller 失敗（exit $code）。完整記錄：$buildLog"
}

$dist = Join-Path $root "build\dist\PatentsGrabber"
$exe = Join-Path $dist "PatentsGrabber.exe"
if (-not (Test-Path $exe)) { Fail "PyInstaller 回報成功，卻找不到 $exe。記錄：$buildLog" }
Note ("完成，耗時 {0:N0} 秒" -f $sw.Elapsed.TotalSeconds)

# 資料檔有沒有真的進去，是「回報成功卻不能用」最常見的一種。
$page = Join-Path $dist "_internal\patentsgrabber\web\index.html"
if (-not (Test-Path $page)) { Fail "頁面沒有被打包進去（找不到 $page）" }
$sizeMB = ((Get-ChildItem -Recurse -File $dist | Measure-Object -Property Length -Sum).Sum) / 1MB
Note ("交付資料夾 {0:N0} MB" -f $sizeMB)

# ---------------------------------------------------------------- 實跑檢查
if ($SkipSmoke) {
    Write-Host "`n(已跳過實跑檢查 — 這一步才是在驗『打包出來的東西真的會動』)" -ForegroundColor Yellow
} else {
    Step "實跑檢查（啟動打包後的 exe 並操作它）"
    & python tools\check_release.py --exe $exe
    $smoke = $LASTEXITCODE
    if ($smoke -eq 2) { Fail "實跑檢查無法判定 — 不能當成通過" }
    if ($smoke -ne 0) { Fail "打包出來的程式沒通過實跑檢查。排除模組可能排過頭了（見 packaging\patentsgrabber.spec 的 EXCLUDES）" }
}

# ---------------------------------------------------------------- 壓縮
Step "壓縮成發布附件"
$releaseDir = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$zip = Join-Path $releaseDir "PatentsGrabber-$version-win64.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal
if (-not (Test-Path $zip)) { Fail "壓縮失敗" }
$zipMB = (Get-Item $zip).Length / 1MB
$sha = (Get-FileHash -Algorithm SHA256 $zip).Hash
Note ("{0}  {1:N0} MB" -f (Split-Path -Leaf $zip), $zipMB)
Note "SHA256  $sha"
# -Encoding utf8 in Windows PowerShell 5.1 means UTF-8 WITH a BOM, and a BOM in a
# checksum file ends up inside the first field of anything that reads it.
# WriteAllText with a plain UTF8Encoding($false) is the version that does not.
[System.IO.File]::WriteAllText("$zip.sha256", "$sha  $(Split-Path -Leaf $zip)`n",
                               (New-Object System.Text.UTF8Encoding($false)))

Step "完成"
Write-Host "  可直接執行：$exe" -ForegroundColor Green
Write-Host "  發布附件：  $zip" -ForegroundColor Green
