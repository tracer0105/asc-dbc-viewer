# Build a portable one-file exe and zip it.
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
# NOTE: keep this file pure ASCII - PowerShell 5.1 reads .ps1 as ANSI without BOM.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "[1/4] running tests ..."
python tests\run_tests.py
if ($LASTEXITCODE -ne 0) { throw "tests failed, abort build" }

Write-Host "[2/4] pyinstaller ..."
python -m PyInstaller --noconfirm --clean --onefile --name "ASC交互曲线" `
  --add-data "$root\template.html;." `
  --collect-submodules cantools --hidden-import tkinter `
  --distpath dist --workpath build --specpath build app.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Write-Host "[3/4] staging release folder ..."
$pkg = Join-Path $root "release\ASC交互曲线_v1.0_免安装"
if (Test-Path $pkg) { Remove-Item $pkg -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pkg | Out-Null
Copy-Item "dist\ASC交互曲线.exe" $pkg
Copy-Item "README.md", "requirements.txt", "使用说明.txt" $pkg

Write-Host "[4/4] zip ..."
$zip = Join-Path $root "release\ASC交互曲线_v1.0_免安装.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$pkg\*" -DestinationPath $zip -CompressionLevel Optimal
Write-Host "done: $zip"
