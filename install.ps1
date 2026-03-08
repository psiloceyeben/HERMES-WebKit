# HERMES WEBKIT -- installer
# Run from PowerShell: .\install.ps1
#
# Requirements: Windows 10/11 (PowerShell 5.1+ and OpenSSH Client built in)
# No additional software needed.
#
# What this does:
#   1. Asks for Hetzner key, Anthropic key, domain, and your vessel idea
#   2. Creates a Hetzner VPS and deploys HERMES WEBKIT
#   3. Writes your vessel identity from your answers
#   4. Optionally sets up SSL for your domain
#   5. Prints your live URL and SSH connection command

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

function Ask-Optional($prompt) {
    Write-Host "  $prompt" -ForegroundColor Gray
    $val = Read-Host "  >"
    return $val.Trim()
}

function Write-Step($msg) { Write-Host "  $msg" -ForegroundColor White }
function Write-Done($msg) { Write-Host "  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Divider   { Write-Host ""; Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray; Write-Host "" }

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
    if ($LASTEXITCODE -ne 0) { throw "SSH command failed: $cmd" }
}

function SSH-NoCheck($cmd) {
    # Like SSH but ignores exit code (for optional steps)
    & ssh @script:SSHOpts "root@$script:IP" $cmd 2>&1 | Out-Null
}

function Write-FileToServer($localContent, $remotePath) {
    # Base64-encode to safely transfer content with any special characters
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($localContent)
    $b64   = [Convert]::ToBase64String($bytes)
    SSH "echo '$b64' | base64 -d > $remotePath"
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
$script:HetznerKey = $HetznerKey
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

# ── 4. Your vessel ────────────────────────────────────────────────────────────

Write-Divider
Write-Host "  4. YOUR WEBSITE" -ForegroundColor White
Write-Host ""
Write-Host "  Six questions. Describe your website in plain English." -ForegroundColor Gray
Write-Host "  You can change everything later from the operator terminal." -ForegroundColor DarkGray
Write-Host ""

$VesselName    = Ask-Required "What is this website called?"
Write-Host ""
$VesselPurpose = Ask-Optional "Who is it for, and what does it offer them?"
Write-Host ""
$VesselVoice   = Ask-Optional "How should it sound?  (e.g. warm, sharp, poetic, direct)"
Write-Host ""
$VesselKnows   = Ask-Optional "What should it know and talk about?"
Write-Host ""
$VesselGoal    = Ask-Optional "What do you want visitors to do or feel when done?"
Write-Host ""
$VesselSpecific = Ask-Optional "What makes this unmistakably yours?"
Write-Host ""
$VesselContact = Ask-Optional "How can visitors reach you?  (email, link, or skip)"
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

$PubKey  = (Get-Content "$SSHKeyPath.pub" -Raw).Trim()
$SSHOpts = @("-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
             "-o", "LogLevel=ERROR", "-i", $SSHKeyPath)
$script:SSHOpts = $SSHOpts

# Upload key to Hetzner
$KeyName = "hermes-$(Get-Date -Format 'yyyyMMdd-HHmm')"
try {
    $keyResp      = Hetzner "POST" "/ssh_keys" @{ name = $KeyName; public_key = $PubKey }
    $HetznerKeyId = $keyResp.ssh_key.id
    Write-Done "SSH key registered with Hetzner."
} catch {
    $existing = (Hetzner "GET" "/ssh_keys").ssh_keys | Where-Object { $_.public_key.Trim() -eq $PubKey }
    if ($existing) {
        $HetznerKeyId = $existing[0].id
        Write-Info "Using existing Hetzner SSH key."
    } else {
        Write-Host "  Could not register SSH key: $_" -ForegroundColor Red; exit 1
    }
}

# ── Create server ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Step "Creating server..."
Write-Info "  CX22 in Helsinki (hel1) -- ~4 EUR/month"

$ServerName = "hermes-$(Get-Random -Maximum 99999)"
$serverResp = Hetzner "POST" "/servers" @{
    name        = $ServerName
    server_type = "cx22"
    image       = "ubuntu-24.04"
    location    = "hel1"
    ssh_keys    = @($HetznerKeyId)
}
$ServerId      = $serverResp.server.id
$IP            = $serverResp.server.public_net.ipv4.ip
$script:IP     = $IP

Write-Done "Server created: $IP"
Write-Step "Waiting for boot..."

$attempts = 0
do {
    Start-Sleep -Seconds 5
    $status   = (Hetzner "GET" "/servers/$ServerId").server.status
    $attempts++
    if ($attempts % 4 -eq 0) { Write-Info "  Status: $status" }
} while ($status -ne "running" -and $attempts -lt 30)

if ($status -ne "running") {
    Write-Host "  Server did not come up in time. Check Hetzner console." -ForegroundColor Red; exit 1
}

Start-Sleep -Seconds 20   # let SSH daemon start
Write-Done "Server is up."

# ── Deploy ────────────────────────────────────────────────────────────────────

Write-Divider
Write-Step "Deploying HERMES WEBKIT..."

# System packages
SSH @"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip nginx ufw git
pip3 install -q --break-system-packages fastapi uvicorn anthropic python-dotenv 2>/dev/null || pip3 install -q fastapi uvicorn anthropic python-dotenv
"@
Write-Info "  System packages installed."

# Firewall
SSH "ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable"
Write-Info "  Firewall configured."

# Clone repo
SSH "git clone https://github.com/psiloceyeben/HERMES-WebKit.git /root/hermes 2>&1 | tail -1"
Write-Info "  Code deployed."

# hermes CLI
SSH "cp /root/hermes/hermes /usr/local/bin/hermes && chmod +x /usr/local/bin/hermes"
Write-Info "  hermes CLI installed."

# ── Write .env ────────────────────────────────────────────────────────────────

$BuildToken = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
$envContent = @"
ANTHROPIC_API_KEY=$AnthropicKey
BUILD_TOKEN=$BuildToken
HERMES_MAX_TOKENS=8192
"@
Write-FileToServer $envContent "/root/hermes/.env"
SSH "chmod 600 /root/hermes/.env"
Write-Info "  Environment configured."

# ── Write VESSEL.md ───────────────────────────────────────────────────────────

Write-Step "Writing vessel..."

$vesselLines = @("# $VesselName", "")
if ($VesselPurpose.Length -gt 0)  { $vesselLines += "## Purpose";            $vesselLines += $VesselPurpose;  $vesselLines += "" }
if ($VesselVoice.Length -gt 0)    { $vesselLines += "## Voice and tone";     $vesselLines += $VesselVoice;    $vesselLines += "" }
if ($VesselKnows.Length -gt 0)    { $vesselLines += "## Knowledge";          $vesselLines += $VesselKnows;    $vesselLines += "" }
if ($VesselGoal.Length -gt 0)     { $vesselLines += "## Visitor outcome";    $vesselLines += $VesselGoal;     $vesselLines += "" }
if ($VesselSpecific.Length -gt 0) { $vesselLines += "## What makes this yours"; $vesselLines += $VesselSpecific; $vesselLines += "" }
if ($VesselContact.Length -gt 0)  { $vesselLines += "## Contact";            $vesselLines += $VesselContact;  $vesselLines += "" }

$VesselContent = ($vesselLines -join "`n")
SSH "mkdir -p /root/hermes/vessel/tree"
Write-FileToServer $VesselContent "/root/hermes/vessel/VESSEL.md"
Write-Done "Vessel written."

# STATE.md
$today        = Get-Date -Format "yyyy-MM-dd"
$stateContent = "# STATE`n`nLaunched: $today`nStatus: live`n`n## Memory`nNothing recorded yet.`n`n## Heartbeat"
Write-FileToServer $stateContent "/root/hermes/vessel/STATE.md"
Write-Info "  State initialised."

# ── nginx ─────────────────────────────────────────────────────────────────────

Write-Step "Configuring nginx..."

$nginxConf = @'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    server_tokens off;

    root /root/hermes/static;
    index landing.html;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self';" always;
    add_header Permissions-Policy "geolocation=(), camera=(), microphone=()" always;

    location ~* \.(json|env|py|log|md|sh|txt)$ {
        return 404;
    }

    location ~ ^/(chat|chat/confirm|chat/clear) {
        allow 127.0.0.1;
        allow ::1;
        deny all;
    }

    location /ask {
        limit_req zone=ask burst=5 nodelay;
        limit_req_status 429;
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host            $host;
        proxy_set_header   X-Real-IP       $remote_addr;
        proxy_read_timeout 30s;
        proxy_hide_header  X-Powered-By;
    }

    location /build {
        limit_req zone=build burst=2 nodelay;
        limit_req_status 429;
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host            $host;
        proxy_set_header   X-Real-IP       $remote_addr;
        proxy_read_timeout 120s;
        proxy_hide_header  X-Powered-By;
    }

    location ~ ^/(health|setup|agent|agents|analytics) {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host            $host;
        proxy_set_header   X-Real-IP       $remote_addr;
        proxy_read_timeout 120s;
        proxy_hide_header  X-Powered-By;
    }

    location / {
        try_files $uri $uri/ $uri/index.html @bridge;
    }

    location @bridge {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host            $host;
        proxy_read_timeout 120s;
        proxy_hide_header  X-Powered-By;
    }
}
'@

Write-FileToServer $nginxConf "/etc/nginx/sites-available/hermes"
SSH "if ! grep -q limit_req_zone /etc/nginx/nginx.conf; then python3 -c \"c=open('/etc/nginx/nginx.conf').read(); open('/etc/nginx/nginx.conf','w').write(c.replace('http {', 'http {\\n    limit_req_zone \$binary_remote_addr zone=ask:10m rate=10r/m;\\n    limit_req_zone \$binary_remote_addr zone=build:10m rate=1r/m;', 1)) if 'limit_req_zone' not in c else None\"; fi"
SSH "ln -sf /etc/nginx/sites-available/hermes /etc/nginx/sites-enabled/hermes && rm -f /etc/nginx/sites-enabled/default && nginx -t && systemctl reload nginx"
Write-Info "  nginx configured (hardened)."

# ── systemd service ───────────────────────────────────────────────────────────

Write-Step "Installing service..."

$serviceConf = @"
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

Write-FileToServer $serviceConf "/etc/systemd/system/hermes.service"
SSH "systemctl daemon-reload && systemctl enable --now hermes"
Write-Info "  Service installed and running."

# ── Wait for bridge and trigger first build ───────────────────────────────────

Write-Step "Building your site..."
Start-Sleep -Seconds 5

$buildResult = SSH "curl -s -X POST http://127.0.0.1:8000/build -H 'X-Build-Token: $BuildToken' -d 'render the site homepage for the first time'"
Write-Done "Site built."

# ── DNS + SSL ─────────────────────────────────────────────────────────────────

Write-Divider

if ($Domain) {
    Write-Host "  DNS SETUP" -ForegroundColor White
    Write-Host ""
    Write-Host "  In your domain registrar's DNS settings, add an A record:" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    Type:  A" -ForegroundColor White
    Write-Host "    Host:  @" -ForegroundColor White
    Write-Host "    Value: $IP" -ForegroundColor White
    Write-Host "    TTL:   300 (or lowest available)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Also add the same for www (Type: A, Host: www, Value: $IP)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  DNS can take 2-60 minutes to propagate." -ForegroundColor DarkGray
    Write-Host "  Press Enter when the domain resolves to $IP" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "  > waiting for you"

    Write-Step "Setting up SSL..."
    SSH "apt-get install -y -qq certbot python3-certbot-nginx && certbot --nginx -d $Domain -d www.$Domain --non-interactive --agree-tos -m admin@$Domain --redirect"
    Write-Done "SSL active."

    $LiveURL = "https://$Domain"
} else {
    $LiveURL = "http://$IP"
}

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Divider
Write-Host "  Your vessel is live." -ForegroundColor Green
Write-Host ""
Write-Host "  Site:    $LiveURL" -ForegroundColor Cyan
Write-Host ""
Write-Divider
Write-Host "  To work on your site, SSH in and open the operator terminal:" -ForegroundColor White
Write-Host ""
Write-Host "    ssh -i $SSHKeyPath root@$IP" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Then:" -ForegroundColor DarkGray
Write-Host "    hermes studio       open chat + shell side by side" -ForegroundColor DarkGray
Write-Host "    hermes sites        list all vessels on this server" -ForegroundColor DarkGray
Write-Host "    hermes build        rebuild the site" -ForegroundColor DarkGray
Write-Host "    hermes new-site     add another website" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  From the chat pane you can talk to your vessel, ask it to build" -ForegroundColor DarkGray
Write-Host "  features, restyle the site, or add pages -- in plain English." -ForegroundColor DarkGray
Write-Divider

# Save connection info to desktop
$connInfo = @"
HERMES WEBKIT — connection info
================================
Site:      $LiveURL
Server IP: $IP
SSH:       ssh -i $SSHKeyPath root@$IP
Build token: $BuildToken

Commands (after SSH):
  hermes studio      open operator terminal
  hermes build       rebuild the site
  hermes new-site    add another website
  hermes sites       list all vessels
"@

$desktopPath = "$env:USERPROFILE\Desktop\hermes-connection.txt"
$connInfo | Out-File -FilePath $desktopPath -Encoding UTF8
Write-Host "  Connection info saved to: $desktopPath" -ForegroundColor DarkGray
Write-Host ""
