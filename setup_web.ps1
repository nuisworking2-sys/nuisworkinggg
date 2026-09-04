$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Set-Location .\server
if (!(Test-Path .\.venv)) {
  py -3 -m venv .venv
}
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (!(Test-Path .\.env)) {
  Copy-Item .\.env.example .\.env
  Write-Host "server/.env 파일을 열어서 API 키를 넣어주세요." -ForegroundColor Yellow
}
Write-Host "설치 완료. 이제 상위 폴더의 start_web.bat 를 실행하세요." -ForegroundColor Green
