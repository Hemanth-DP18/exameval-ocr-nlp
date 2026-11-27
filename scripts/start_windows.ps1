$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "Docker Desktop is not installed or docker is not on PATH." -ForegroundColor Red
  Write-Host "Install Docker Desktop, enable its WSL 2 backend, start it, then rerun this script."
  exit 1
}
try { docker compose version | Out-Null } catch {
  Write-Host "Docker Compose is unavailable. Update Docker Desktop and try again." -ForegroundColor Red
  exit 1
}
if (-not (Test-Path ".env")) {
  Write-Host "First-time setup: create private local credentials."
  $password = Read-Host "Choose a private teacher password"
  if ([string]::IsNullOrWhiteSpace($password) -or $password.Length -lt 12) {
    Write-Host "Use a teacher password of at least 12 characters." -ForegroundColor Red
    exit 1
  }
  $secret = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
  @(
    "EXAMEVAL_ADMIN_PASSWORD=$password"
    "EXAMEVAL_TOKEN_SECRET=$secret"
  ) | Set-Content -Path ".env" -Encoding ascii
  Write-Host "Saved credentials in .env. Keep this file private."
}
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "ExamEval is starting at http://localhost:8000"
Start-Sleep -Seconds 3
Start-Process "http://localhost:8000"
