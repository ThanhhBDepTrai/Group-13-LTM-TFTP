$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

pyinstaller --noconfirm --clean --onefile --windowed `
  --name TFTPProClient tftp_gui.py 2>$null

pyinstaller --noconfirm --clean --onefile --console `
  --name TFTPProServer tftp_server.py

$folder = "dist\TFTPPro-v1.0.0-windows-x64"
New-Item -ItemType Directory -Force $folder | Out-Null
Copy-Item dist\TFTPProServer.exe, README.md, LICENSE $folder -ErrorAction SilentlyContinue
Compress-Archive -Path "$folder\*" -DestinationPath "$folder.zip" -Force

Get-FileHash "$folder.zip" -Algorithm SHA256 | Format-List
