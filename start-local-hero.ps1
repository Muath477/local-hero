# Local Hero - start the whole stack from the repo root (Windows).
#   Docker Desktop (MySQL) + Ollama + agents service + Next.js app
#   ./start-local-hero.ps1
$ErrorActionPreference = "Stop"
$app = $PSScriptRoot                       # the repo root
$agents = "$app\agents"                    # the only copy of the backend - see DEVELOPMENT.md
$envFile = "$app\.env"

# --- ports: the web app reads AGENTS_URL from .env, so the agents service must listen there ---
$agentsPort = 9099
if (Test-Path $envFile) {
  $line = Select-String -Path $envFile -Pattern '^\s*AGENTS_URL\s*=\s*"?https?://[^:/"]+:(\d+)' | Select-Object -First 1
  if ($line) { $agentsPort = [int]$line.Matches[0].Groups[1].Value }
}

# --- Ollama ---
try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null }
catch { Write-Error "Ollama is not running on :11434. Start Ollama first (ollama serve)."; exit 1 }
Write-Host "Ollama is up." -ForegroundColor Green

# --- MySQL (Docker). Docker Desktop must already be running (see DEVELOPMENT.md if it won't start). ---
& docker info 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
  $db = & docker ps -a --filter "name=mysql" --format "{{.Names}}" | Select-Object -First 1
  if ($db) { & docker start $db | Out-Null; Write-Host "MySQL container '$db' is up." -ForegroundColor Green }
  else { Write-Warning "No MySQL container found. Run 'docker compose up -d mysql' in $app." }
} else {
  Write-Warning "Docker is not running - open Docker Desktop and wait until it says 'Engine running', then start this script again. Sign-in and chat history need the database."
}

# --- 1. Agents service (router + agents + RAG + Tarjuman + council) ---
$py = "$agents\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python"; Write-Warning "agents\.venv not found - using the global python. Create it: cd agents; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }
Start-Process powershell -ArgumentList @(
  "-NoExit","-Command",
  "cd '$agents'; & '$py' -m uvicorn api.server:app --host 127.0.0.1 --port $agentsPort"
)
Write-Host "Agents  -> http://localhost:$agentsPort" -ForegroundColor Green

# --- 2. Next.js app on :3000 - NOT a free choice: GOOGLE_REDIRECT_URI in .env is hardcoded to
#        http://localhost:3000/api/auth/google/callback and must match Google Cloud Console exactly. ---
Start-Process powershell -ArgumentList @(
  "-NoExit","-Command",
  "cd '$app'; npm run dev -- -p 3000"
)
Write-Host "Local Hero -> http://localhost:3000  (first account = admin)" -ForegroundColor Cyan
