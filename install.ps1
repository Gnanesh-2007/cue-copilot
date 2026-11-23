# Cue Copilot - 1-Line Windows PowerShell Installer (Local Ollama Edition)
# Run with: irm https://raw.githubusercontent.com/Gnanesh-2007/cue-copilot/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/Gnanesh-2007/cue-copilot.git"
$InstallDir = Join-Path $env:USERPROFILE "CueCopilot"

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Ensure-Tool($Cmd, $WingetId, $Label) {
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Write-Host ">> Installing $Label via winget..." -ForegroundColor Cyan
        winget install --id $WingetId -e --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
    } else {
        Write-Host ">> $Label already installed." -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "   Cue Copilot (Local Ollama Edition) - Installer    " -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Ensure Git, Python, Node.js, and Ollama are installed
Ensure-Tool "git"    "Git.Git"                  "Git"
Ensure-Tool "python" "Python.Python.3.12"       "Python 3.12"
Ensure-Tool "node"   "OpenJS.NodeJS.LTS"        "Node.js LTS"

if (-not (Get-Command "ollama" -ErrorAction SilentlyContinue)) {
    Write-Host ">> Installing Ollama..." -ForegroundColor Cyan
    irm https://ollama.com/install.ps1 | iex
    Refresh-Path
} else {
    Write-Host ">> Ollama already installed." -ForegroundColor Green
}

# 2. Clone or update Cue Copilot in %USERPROFILE%\CueCopilot
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host ">> Updating existing CueCopilot in $InstallDir..." -ForegroundColor Cyan
    git -C $InstallDir pull --ff-only
} else {
    if (Test-Path $InstallDir) {
        Write-Host ">> Cleaning partial folder $InstallDir..." -ForegroundColor Yellow
        Remove-Item $InstallDir -Recurse -Force
    }
    Write-Host ">> Downloading CueCopilot to $InstallDir..." -ForegroundColor Cyan
    git clone $RepoUrl $InstallDir
}

# 3. Create settings.json from template if missing (never overwrites existing user settings)
$SettingsFile = Join-Path $InstallDir "settings.json"
$ExampleFile  = Join-Path $InstallDir "settings.example.json"
if (-not (Test-Path $SettingsFile) -and (Test-Path $ExampleFile)) {
    Copy-Item $ExampleFile $SettingsFile
}

# 4. Install Python dependencies
Write-Host ">> Installing Python packages (this takes ~1-2 mins)..." -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
python -m pip install --upgrade pyaudiowpatch faster-whisper customtkinter numpy scipy pillow rapidocr-onnxruntime pymupdf python-docx websockets requests pycaw

# 5. Install Electron Frosted-Glass UI runtime
$GlassDir = Join-Path $InstallDir "glass_ui"
Write-Host ">> Setting up Frosted-Glass UI (Electron)..." -ForegroundColor Cyan
Push-Location $GlassDir
npm install
$ElectronExe = Join-Path $GlassDir "node_modules\electron\dist\electron.exe"
if (-not (Test-Path $ElectronExe)) {
    Write-Host ">> Downloading Electron Windows binary..." -ForegroundColor Cyan
    node "node_modules\electron\install.js"
}
Pop-Location

# 6. Pull local Ollama model (qwen2.5:3b - fast on every laptop)
Write-Host ">> Pulling local Ollama model (qwen2.5:3b)..." -ForegroundColor Cyan
if (Get-Command "ollama" -ErrorAction SilentlyContinue) {
    ollama pull qwen2.5:3b
}

# 7. Create Desktop Shortcut
try {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $ShortcutPath = Join-Path $Desktop "Cue Copilot.lnk"
    $Wsh = New-Object -ComObject WScript.Shell
    $Sc = $Wsh.CreateShortcut($ShortcutPath)
    $Sc.TargetPath = Join-Path $InstallDir "CueCopilot.exe"
    $Sc.WorkingDirectory = $InstallDir
    $Sc.Description = "Cue Copilot - Local AI Meeting & Study Assistant"
    $Sc.Save()
    Write-Host ">> Created Desktop shortcut: Cue Copilot" -ForegroundColor Green
} catch {
    Write-Host ">> Note: Launch anytime from $InstallDir\CueCopilot.exe" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "   INSTALL COMPLETE!                                 " -ForegroundColor Green
Write-Host "   Double-click 'Cue Copilot' on your Desktop!       " -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
