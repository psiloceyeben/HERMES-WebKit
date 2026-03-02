# HERMES WEBKIT -- installer
# Run from PowerShell: .\install.ps1
#
# Requirements: Windows 10/11 (PowerShell and OpenSSH Client are built in)
# No additional software needed.
#
# What this does:
#   1. Asks four things: Hetzner key, Anthropic key, domain, your idea
#   2. Creates a Hetzner VPS and deploys HERMES WEBKIT
#   3. Writes your vessel from your answers
#   4. Optionally sets up SSL for your domain
#   Gives you a live URL at the end.

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# ── helpers ───────────────────────────────────────────────────────────────────

function Write-Header {
    Clear-Host
    $Host.UI.RawUI.BackgroundColor = "Black"
    $Host.UI.RawUI.ForegroundColor = "White"
    Clear-Host
    Write-Host ""
    Write-Host "  ##  ##  #####  ####   ##   ##  #####   ####" -ForegroundColor Cyan
    Write-Host "  ##  ##  ##     ##  #  ### ###  ##     ##" -ForegroundColor Cyan
    Write-Host "  ######  ####   ####   ## # ##  ####    ###" -ForegroundColor Cyan
    Write-Host "  ##  ##  ##     ## #   ##   ##  ##        ##" -ForegroundColor Cyan
    Write-Host "  ##  ##  #####  ##  #  ##   ##  #####  ####" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  WEBKIT" -ForegroundColor DarkCyan -NoNewline
    Write-Host "  a vessel architecture for AI-inhabited websites" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
}

function Ask-Required($prompt) {
    Write-Host "  $prompt" -ForegroundColor Gray
    $val = ""
    while (-not $val.Trim()) {
        $val = Read-Host "  >"
        if (-not $val.Trim()) { Write-Host "  (required)" -ForegroundColor DarkYellow }
    }
    return $val.Trim()
}

function Ask-Optional($prompt, $hint = "") {
    if ($hint) { Write-Host "  $hint" -ForegroundColor DarkGray }
    Write-Host "  $prompt" -ForegroundColor Gray
    $val = Read-Host "  >"
    return $val.Trim()
}

function Write-Step($msg) {
    Write-Host "  $msg" -ForegroundColor White
}

function Write-Done($msg) {
    Write-Host "  $msg" -ForegroundColor Green
}

function Write-Info($msg) {
    Write-Host "  $msg" -ForegroundColor DarkGray
}

function Write-Divider {
    Write-Host ""
    Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""
}

function Hetzner($method, $path, $body = $null) {
    $params = @{
        Uri     = "https://api.hetzner.cloud/v1$path"
        Method  = $method
        Headers = @{ Authorization = "Bearer $script:HetznerKey"; "Content-Type" = "application/json" }
    }
    if ($body) { $params.Body = ($body | ConvertTo-Json -Depth 10) }
    return Invoke-RestMethod @params
}

function SSH($cmd) {
    & ssh @script:SSHOpts "root@$script:IP" $cmd
}

function Run-SSH($cmd) {
    $result = & ssh @script:SSHOpts "root@$script:IP" $cmd 2>&1
    return $result
}

# ── intro ─────────────────────────────────────────────────────────────────────

Write-Header
Write-Host "  Four things and you are live." -ForegroundColor White
Write-Host ""
Write-Host "  A Hetzner API key, an Anthropic API key," -ForegroundColor DarkGray
Write-Host "  a domain if you have one, and your idea." -ForegroundColor DarkGray
Write-Host ""

# ── 1. Hetzner key ────────────────────────────────────────────────────────────

Write-Host "  1. HETZNER API KEY" -ForegroundColor White
Write-Info "     console.hetzner.com > Security > API Tokens > Generate (Read & Write)"
Write-Host ""
$HetznerKey = Ask-Required "Paste your Hetzner API key:"
Write-Host ""

# ── 2. Anthropic key ──────────────────────────────────────────────────────────

Write-Host "  2. ANTHROPIC API KEY" -ForegroundColor White
Write-Info "     console.anthropic.com > API Keys > Create key"
Write-Host ""
$AnthropicKey = Ask-Required "Paste your Anthropic API key:"
Write-Host ""

# ── 3. Domain ─────────────────────────────────────────────────────────────────

Write-Host "  3. DOMAIN" -ForegroundColor White
Write-Info "     Optional. Works on bare IP first -- add a domain any time."
Write-Info "     If you have one ready, enter it now (e.g. mysite.com)"
Write-Host ""
$Domain = Ask-Optional "Domain name (or press Enter to skip):"
Write-Host ""

# ── 4. Your idea ──────────────────────────────────────────────────────────────

Write-Divider
Write-Host "  4. YOUR WEBSITE" -ForegroundColor White
Write-Host ""
Write-Host "  Describe your website in plain English." -ForegroundColor Gray
Write-Host "  This writes your vessel -- you can change everything later." -ForegroundColor DarkGray
Write-Host ""

$VesselName      = Ask-Required "What is your website called?"
Write-Host ""

Write-Host "  What is it for -- and who is it for?" -ForegroundColor Gray
$VesselPurpose   = Read-Host "  >"
Write-Host ""

Write-Host "  What voice or tone? (e.g. warm, direct, formal, poetic, technical)" -ForegroundColor Gray
$VesselVoice     = Read-Host "  >"
Write-Host ""

Write-Host "  What does it know about? Your expertise, story, or offerings:" -ForegroundColor Gray
$VesselKnowledge = Read-Host "  >"
Write-Host ""

Write-Host "  What do you want visitors to do or feel when they leave?" -ForegroundColor Gray
$VesselGoal      = Read-Host "  >"
Write-Host ""

Write-Host "  What makes this specific to you?" -ForegroundColor Gray
$VesselCharacter = Read-Host "  >"
Write-Host ""

Write-Host "  What should it never do or say?" -ForegroundColor Gray
$VesselLimits    = Read-Host "  >"
Write-Host ""

Write-Host "  Your name or contact (optional -- press Enter to skip):" -ForegroundColor Gray
$VesselContact   = Read-Host "  >"
Write-Host ""

Write-Done "Got it."

# ── SSH key ───────────────────────────────────────────────────────────────────

Write-Divider
Write-Step "Setting up SSH key..."

$SSHDir     = "$env:USERPROFILE\.ssh"
$SSHKeyPath = "$SSHDir\hermes_ed25519"
if (-not (Test-Path $SSHDir)) { New-Item -ItemType Directory -Path $SSHDir | Out-Null }

if (-not (Test-Path $SSHKeyPath)) {
    & ssh-keygen -t ed25519 -f $SSHKeyPath -N '""' -q
    Write-Done "SSH key created."
} else {
    Write-Info "Existing SSH key found."
}

$PubKey  = Get-Content "$SSHKeyPath.pub" -Raw
$SSHOpts = @("-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30", "-o", "LogLevel=ERROR", "-i", $SSHKeyPath)
$script:SSHOpts = $SSHOpts

# Upload key to Hetzner
$KeyName = "hermes-$(Get-Date -Format 'yyyyMMdd-HHmm')"
try {
    $keyResp = Hetzner "POST" "/ssh_keys" @{ name = $KeyName; public_key = $PubKey.Trim() }
    $HetznerKeyId = $keyResp.ssh_key.id
    Write-Done "SSH key registered with Hetzner."
} catch {
    # Key may already exist -- find it by public key
    $existing = (Hetzner "GET" "/ssh_keys").ssh_keys | Where-Object { $_.public_key.Trim() -eq $PubKey.Trim() }
    if ($existing) {
        $HetznerKeyId = $existing[0].id
        Write-Info "Using existing Hetzner SSH key."
    } else {
        Write-Host "  Could not register SSH key: $_" -ForegroundColor Red
        exit 1
    }
}

# ── Create server ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Step "Creating server..."
Write-Info "  Location: Helsinki (hel1) -- change in install.ps1 if preferred"

$ServerName = "hermes-$(Get-Random -Maximum 99999)"
$serverSpec = @{
    name        = $ServerName
    server_type = "cx22"
    image       = "ubuntu-22.04"
    location    = "hel1"
    ssh_keys    = @($HetznerKeyId)
}

$serverResp = Hetzner "POST" "/servers" $serverSpec
$ServerId   = $serverResp.server.id
$IP         = $serverResp.server.public_net.ipv4.ip
$script:IP  = $IP

Write-Done "Server created: $IP"
Write-Step "Waiting for boot..."

$attempts = 0
do {
    Start-Sleep -Seconds 5
    $status = (Hetzner "GET" "/servers/$ServerId").server.status
    $attempts++
    if ($attempts % 4 -eq 0) { Write-Info "  Status: $status" }
} while ($status -ne "running" -and $attempts -lt 30)

if ($status -ne "running") {
    Write-Host "  Server did not come up in time. Check Hetzner console." -ForegroundColor Red
    exit 1
}

Start-Sleep -Seconds 20   # Give SSH daemon time to start
Write-Done "Server is up."

# ── Deploy ────────────────────────────────────────────────────────────────────

Write-Divider
Write-Step "Deploying HERMES WEBKIT..."

# Install system deps
$bootstrap = @"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip nginx certbot python3-certbot-nginx git
pip3 install -q --break-system-packages fastapi uvicorn anthropic python-dotenv 2>/dev/null || pip3 install -q fastapi uvicorn anthropic python-dotenv
"@
SSH $bootstrap
Write-Info "  System packages installed."

# Clone repo
SSH "git clone https://github.com/psiloceyeben/HERMES-WebKit.git /root/hermes 2>&1 || (cd /root/hermes && git pull)"
Write-Info "  Code deployed."

# Write .env
$BuildToken = [System.Guid]::NewGuid().ToString("N")
$envContent = "ANTHROPIC_API_KEY=$AnthropicKey`nBUILD_TOKEN=$BuildToken"
SSH "printf '%s\n' '$envContent' > /root/hermes/.env && chmod 600 /root/hermes/.env"
Write-Info "  Environment configured."

# ── Write VESSEL.md ──────────────────────────────────────────────────────────

Write-Step "Writing vessel..."

$vesselLines = @("# $VesselName", "")
if ($VesselPurpose.Trim())   { $vesselLines += "## Purpose";   $vesselLines += $VesselPurpose.Trim();   $vesselLines += "" }
if ($VesselVoice.Trim())     { $vesselLines += "## Voice";     $vesselLines += $VesselVoice.Trim();     $vesselLines += "" }
if ($VesselKnowledge.Trim()) { $vesselLines += "## Knowledge"; $vesselLines += $VesselKnowledge.Trim(); $vesselLines += "" }
if ($VesselGoal.Trim())      { $vesselLines += "## Goal";      $vesselLines += $VesselGoal.Trim();      $vesselLines += "" }
if ($VesselCharacter.Trim()) { $vesselLines += "## Character"; $vesselLines += $VesselCharacter.Trim(); $vesselLines += "" }
if ($VesselLimits.Trim())    { $vesselLines += "## Limits";    $vesselLines += $VesselLimits.Trim();    $vesselLines += "" }
if ($VesselContact.Trim())   { $vesselLines += "## Contact";   $vesselLines += $VesselContact.Trim();   $vesselLines += "" }

$vesselContent = ($vesselLines -join "`n")
SSH "mkdir -p /root/hermes/vessel/tree && cat > /root/hermes/vessel/VESSEL.md << 'VESSELEOF'`n$vesselContent`nVESSELEOF"
Write-Done "Vessel written."

# ── STATE.md ─────────────────────────────────────────────────────────────────

$today = Get-Date -Format "yyyy-MM-dd"
$stateContent = "# STATE`n`nLaunched: $today`nStatus: live`n`n## Memory`nNothing recorded yet.`n`n## Heartbeat"
SSH "cat > /root/hermes/vessel/STATE.md << 'STEOF'`n$stateContent`nSTEOF"
Write-Info "  State initialised."

# ── systemd service ──────────────────────────────────────────────────────────

$service = @"
[Unit]
Description=HERMES bridge
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/hermes
EnvironmentFile=/root/hermes/.env
ExecStart=/usr/bin/python3 /root/hermes/bridge.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"@
SSH "cat > /etc/systemd/system/hermes.service << 'SVCEOF'`n$service`nSVCEOF"
Write-Info "  Service configured."

# nginx
$nginxConf = @"
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    root /root/hermes/static;
    index index.html;
    location ~ ^/(health|build|setup|chat|agent|agents) {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host            \$host;
        proxy_set_header   X-Real-IP       \$remote_addr;
        proxy_read_timeout 120s;
    }
    location / {
        try_files \$uri \$uri/ \$uri/index.html @bridge;
    }
    location @bridge {
        proxy_pass         http://127.0.0.1:8000;
        proxy_read_timeout 120s;
    }
}
"@
SSH "cat > /etc/nginx/sites-enabled/default << 'NGEOF'`n$nginxConf`nNGEOF"

# Start everything
SSH "systemctl daemon-reload && systemctl enable --now hermes && systemctl restart nginx"
Write-Done "Deployed and running."

# ── DNS + SSL ─────────────────────────────────────────────────────────────────

Write-Divider

if ($Domain) {
    Write-Host "  DNS SETUP (manual step)" -ForegroundColor White
    Write-Host ""
    Write-Host "  In your domain registrar's DNS settings, create an A record:" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    Type:  A" -ForegroundColor White
    Write-Host "    Host:  @" -ForegroundColor White
    Write-Host "    Value: $IP" -ForegroundColor White
    Write-Host "    TTL:   300 (or lowest available)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Then press Enter here to continue with SSL setup." -ForegroundColor DarkGray
    Write-Host "  (DNS can take 2-60 minutes to propagate -- wait until the domain resolves)" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "  Press Enter when DNS is pointing to $IP"

    Write-Step "Setting up SSL..."
    SSH "certbot --nginx -d $Domain --non-interactive --agree-tos -m hostmaster@$Domain --redirect"
    Write-Done "SSL active. Auto-renewal configured."

    Write-Divider
    Write-Host "  Your vessel is live." -ForegroundColor White
    Write-Host ""
    Write-Host "  Site:  https://$Domain" -ForegroundColor Green

} else {
    Write-Host "  Your vessel is live." -ForegroundColor White
    Write-Host ""
    Write-Host "  Site:  http://$IP" -ForegroundColor Green
    Write-Host ""
    Write-Info "  To add a domain later:"
    Write-Info "    1. Point an A record to $IP"
    Write-Info "    2. SSH into the server and run:"
    Write-Info "       certbot --nginx -d yourdomain.com"
}

Write-Host ""
Write-Divider
Write-Host "  Everything is running. Your website will build on first visit." -ForegroundColor DarkGray
Write-Host ""
