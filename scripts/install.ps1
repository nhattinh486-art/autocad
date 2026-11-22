# AutoCAD AI Swarm & CAD-MCP Automatic Installer (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$GeminiDir = Join-Path $env:USERPROFILE ".gemini"
$CadMcpDir = Join-Path $GeminiDir "cad-mcp"
$ConfigDir = Join-Path $GeminiDir "config"
$McpConfigPath = Join-Path $ConfigDir "mcp_config.json"
$SkillTargetDir = Join-Path $GeminiDir "antigravity\skills\autocad-swarm"

Write-Host "=== 1. Checking prerequisites (Git & Python) ===" -ForegroundColor Cyan
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git not found. Please install Git from https://git-scm.com/download/win"
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Please install Python >= 3.10 from https://www.python.org/downloads/"
}

Write-Host "=== 2. Installing CAD-MCP into $CadMcpDir ===" -ForegroundColor Cyan
if (-not (Test-Path $CadMcpDir)) {
    git clone https://github.com/daobataotie/CAD-MCP.git $CadMcpDir
} else {
    Write-Host "CAD-MCP directory already exists, skipping clone." -ForegroundColor Yellow
}

$VenvDir = Join-Path $CadMcpDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ServerPy = Join-Path $CadMcpDir "src\server.py"

if (-not (Test-Path $VenvPython)) {
    python -m venv $VenvDir
}

Write-Host "Installing Python dependencies (mcp, pywin32, pydantic, Pillow)..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $CadMcpDir "requirements.txt")

Write-Host "=== 3. Configuring Antigravity MCP ($McpConfigPath) ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$config = @{}
if (Test-Path $McpConfigPath) {
    Copy-Item $McpConfigPath "$McpConfigPath.bak" -Force
    $raw = Get-Content $McpConfigPath -Raw -Encoding UTF8
    if ($raw.Trim()) {
        $parsed = $raw | ConvertFrom-Json
        foreach ($p in $parsed.PSObject.Properties) {
            $config[$p.Name] = $p.Value
        }
    }
}

if (-not $config.ContainsKey("mcpServers") -or $null -eq $config["mcpServers"]) {
    $config["mcpServers"] = @{}
} elseif ($config["mcpServers"] -is [PSCustomObject]) {
    $ht = @{}
    foreach ($p in $config["mcpServers"].PSObject.Properties) {
        $ht[$p.Name] = $p.Value
    }
    $config["mcpServers"] = $ht
}

$config["mcpServers"]["autocad"] = @{
    command = $VenvPython
    args    = @($ServerPy)
    env     = @{
        PYTHONIOENCODING = "utf-8"
    }
}

$config | ConvertTo-Json -Depth 10 | Set-Content -Path $McpConfigPath -Encoding UTF8

Write-Host "=== 4. Installing Custom Skill 'autocad-swarm' ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $SkillTargetDir | Out-Null
Copy-Item (Join-Path $RepoRoot "skills\autocad-swarm\SKILL.md") (Join-Path $SkillTargetDir "SKILL.md") -Force

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Open AutoCAD with a blank drawing."
Write-Host "  2. Restart Antigravity to load the 'autocad' MCP server and 'autocad-swarm' skill."
Write-Host "  3. Test in chat: 'Kiem tra ket noi AutoCAD bang list_layers'"
