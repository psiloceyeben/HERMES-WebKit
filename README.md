# HERMES WEBKIT

**A vessel architecture for AI-inhabited websites.**

Natural language in. Live website out. One command to deploy. Any LLM. Your server. Your data.

### Install

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/psiloceyeben/HERMES-WebKit/main/install.ps1 | iex
```

**Linux** (on a fresh server):
```bash
curl -sL https://raw.githubusercontent.com/psiloceyeben/HERMES-WebKit/main/run | sudo bash
```

One line. Four prompts. Live website.

---

## What this is

HERMES WEBKIT is an open source system that turns a plain English description into a live, AI-generated website. You describe who your website is -- its voice, its purpose, what it knows -- and HERMES runs that description through a routing tree to produce HTML, which is then served statically to every visitor. The AI runs at build time, not at visit time. Once the page is built it is served like any static site -- fast, free to host, no per-visitor API cost.

The routing tree is what makes the output genuinely different from a template. When you describe a change -- "add an about section, warm tone, mention the 2019 founding" -- that description passes through HECATE for classification, flows down through the relevant sephiroth nodes, crosses the path transformations between them, and arrives at MALKUTH which renders the HTML. A page built with that instruction produces structurally different output than one built with "add an about section, technical tone, list the founding team" -- because the routing tree treated them differently, not because fields in a template were swapped.

Visitors see the generated page. They do not interact with the AI by default. If you want visitors to be able to ask questions or interact with the site, that is an optional feature -- a chat interface that makes an API call per visitor message. It does not run unless you explicitly add it.

The default model provider is Anthropic (Sonnet for rendering, Haiku for classification). Other providers are supported -- set `HERMES_MODEL` and `HERMES_MODEL_HECATE` in `.env` to use OpenAI, or point them at a local Ollama endpoint for zero API cost. The bridge uses a single client initialisation that can be swapped to any provider with a compatible chat completions interface.

The vessel files are plain text. The configuration is plain text. The entire system can be understood, edited, and extended without writing code.

---

## Why this exists

The web was built for presences. Early personal pages were genuinely inhabited -- specific, weird, particular to the person who made them. Then platforms arrived with an offer: make it easy if you give us the address. Billions of people took that deal because the alternative required knowing too much.

HERMES removes the barrier without reinstating the platform. You get your own address, your own server, your own presence -- and the setup requires nothing more than describing what you want. The cost is a few dollars a month for a VPS and whatever API usage your builds generate. You own the server. You own the data. Nobody else has the keys.

The second reason is architectural. As AI becomes the interface layer for more and more of the web, the question of how that intelligence is structured -- what governs it, what gives it coherence, what prevents drift -- becomes urgent. HERMES answers that question with the Tree of Life as a routing architecture: a complete, historically tested specification for how intent becomes output through a sequence of constrained transformations. The 22 paths between the 10 sephiroth are not decoration. They are transformation rules. The system is coherent by construction, not by configuration.

---

## Getting started

Four things and you are live: a Hetzner API key, an Anthropic API key, a domain if you have one, and your idea.

### Step 1 -- Create a Hetzner account

Go to [hetzner.com](https://www.hetzner.com) and sign up for Hetzner Cloud. Verify your identity. This is your server provider -- the machine your website lives on.

Once you are in the Hetzner Cloud console:
1. Create a new **Project** (name it anything -- "hermes", "my websites", whatever)
2. Go to **Security** > **API Tokens**
3. Click **Generate API Token** with Read & Write permissions
4. Copy the token -- you will need it in the next step

This is a pay-as-you-go account. The server costs roughly 4 euros per month. Creating the server during install costs about 11 cents.

### Step 2 -- Get an Anthropic API key

Go to [console.anthropic.com](https://console.anthropic.com) and create an account if you do not have one. Navigate to **API Keys** and create a new key. Copy it.

This is the key that powers the AI. The default models are Sonnet (rendering) and Haiku (classification). You can also use:

| Provider | How to configure |
|----------|-----------------|
| **Anthropic** (default) | Set `ANTHROPIC_API_KEY` in `.env` |
| **OpenAI** | Set `HERMES_MODEL` and `HERMES_MODEL_HECATE` to OpenAI model names, update the client in `bridge.py` |
| **Ollama** (local) | Point `HERMES_MODEL` at your Ollama endpoint -- zero API cost per build |

Anthropic is the default and the installer asks for that key. Other providers work by changing the model configuration after install.

### Step 3 -- Run the installer

**Windows (PowerShell):**

No Python, no WSL, no additional software required. Windows 10 and 11 have everything built in.

```powershell
.\install.ps1
```

The installer prompts for your Hetzner API key, Anthropic API key, and optional domain name. Then it creates a VPS, generates an SSH key, deploys the code, configures nginx and systemd, and gives you a live URL.

**Linux (already on a server):**

If you already have a VPS and want to install directly:

```bash
git clone https://github.com/prometheus7/hermeswebkit /root/hermes
cd /root/hermes
sudo ./run
```

The `run` script installs dependencies, configures nginx and systemd, prompts for your Anthropic API key, and starts the bridge. When it finishes, visit your server IP in a browser.

### Step 4 -- The setup wizard

When the installer finishes, open the URL it gives you. On a fresh deploy with no VESSEL.md, visiting the site redirects you to `/setup` -- a browser-based wizard with eight questions:

1. What is your website called?
2. What is it for -- and who is it for?
3. What voice or tone?
4. What does it know about?
5. What do you want visitors to do or feel when they leave?
6. What makes this specific to you?
7. What should it never do or say?
8. Your name or contact (optional)

Click **Build vessel** and HERMES writes your VESSEL.md, runs it through the tree, and serves your site. The whole process takes under a minute.

### Step 5 -- Point a domain (optional)

If you have a domain, create an A record pointing to your server IP:

```
Type:  A
Host:  @
Value: YOUR_SERVER_IP
TTL:   300
```

Add the same for `www`. Once DNS propagates (a few minutes to a few hours), set up HTTPS:

```bash
apt install certbot python3-certbot-nginx -y
certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

If you provided a domain during `install.ps1`, the installer handles certbot for you.

### Step 6 -- Verify

```bash
systemctl status hermes
systemctl status nginx
curl http://localhost:8000/health
```

The health check returns JSON with status and a list of nodes present. If the bridge is not running, check logs:

```bash
journalctl -u hermes -n 50 --no-pager
```

The most common issue is a missing or malformed API key in `.env`.

---

## The architecture

### The vessel

A HERMES website is defined by a handful of plain text files:

```
vessel/
  VESSEL.md     who this website is -- voice, purpose, identity, what it knows
  STATE.md      live memory -- what has been learned, what has changed
  HECATE.md     routing rules and the full 22-path transformation table
  WIZARD.md     theming for the setup wizard (atmosphere, voice, greeting)
  tree/
    KETER.md    purpose -- what is the request actually asking for
    CHOKMAH.md  insight -- what is novel or creative here
    BINAH.md    structure -- what shape should the response take
    CHESED.md   expansion -- what generous connection would help
    GEVURAH.md  constraint -- what must be refused or limited
    TIFERET.md  coherence -- hold the voice, maintain identity
    NETZACH.md  persistence -- what endures, the through-line
    HOD.md      precision -- the right word
    YESOD.md    memory -- what does the vessel know and remember
    MALKUTH.md  grounding -- render the final output
```

That is the complete definition of a website. To create a new site you write VESSEL.md. To change the personality you edit VESSEL.md. To give it new memory you edit STATE.md. Nothing else is required.

### The routing tree

When a build is triggered, the description of what the page should be passes through HECATE first. HECATE is the threshold classifier -- it reads the build instruction, consults the vessel context, and returns a routing path: an ordered list of sephiroth nodes the instruction must traverse before reaching MALKUTH, along with the Hebrew letter path and transformation quality for each crossing between consecutive nodes.

A simple build instruction routes through TIFERET -> MALKUTH. One involving technical detail routes through BINAH -> HOD -> MALKUTH. One that touches constraint or editorial limits routes through GEVURAH -> TIFERET -> MALKUTH. The routing is determined by the content of the instruction and the rules in HECATE.md, which you can edit in plain English. MALKUTH always comes last. It grounds the response -- renders the final HTML, which is written to disk and served statically until the next build.

### The 22 paths

The nodes are the *what* of the routing. The paths are the *how*.

Each of the 22 connections between sephiroth has a Hebrew letter name and a transformation quality describing how the signal changes as it crosses from one node to the next. These qualities are instructions to the render model about how to transform output as it passes through the tree.

| Path | Connects | Transformation quality |
|------|----------|----------------------|
| ALEPH | KETER -> CHOKMAH | the first breath -- undivided attention opens into raw knowing |
| BETH | KETER -> BINAH | intent entering form for the first time -- the vessel begins to take shape |
| GIMEL | KETER -> TIFERET | the long crossing -- what is hidden in purpose becomes the heart of the response |
| DALETH | CHOKMAH -> BINAH | two knowings become one understanding -- flash becomes structure |
| HEH | CHOKMAH -> TIFERET | insight lands -- the flash becomes present and usable |
| VAV | CHOKMAH -> CHESED | wisdom opens into abundance -- knowing becomes giving |
| ZAYIN | BINAH -> TIFERET | structure softens into heart -- the container becomes the content |
| CHETH | BINAH -> GEVURAH | form identifies what must be cut -- understanding becomes discernment |
| TETH | CHESED -> GEVURAH | generosity meets precision -- abundance finds its exact limit |
| YOD | CHESED -> TIFERET | abundance finds its centre -- fullness becomes beauty |
| KAPH | CHESED -> NETZACH | expansion into feeling -- what was given becomes what is felt |
| LAMED | GEVURAH -> TIFERET | judgment becomes beauty -- severity restores proportion |
| MEM | GEVURAH -> HOD | severity becomes exactness -- the cut reveals the precise word |
| NUN | TIFERET -> NETZACH | beauty becomes feeling -- the integrated whole opens into warmth |
| SAMEKH | TIFERET -> YESOD | heart anchors into memory -- what is true now joins what has always been true |
| AYIN | TIFERET -> HOD | beauty becomes precise language -- the right thing finds the right words |
| PEH | NETZACH -> HOD | feeling becomes form -- the emotional current takes exact expression |
| TZADDI | NETZACH -> YESOD | desire streams into continuity -- what is felt connects to what is known |
| QOPH | NETZACH -> MALKUTH | feeling enters the world -- the emotional truth becomes real presence |
| RESH | HOD -> YESOD | precision enters memory -- the exact word joins the accumulated pattern |
| SHIN | HOD -> MALKUTH | exactness becomes presence -- precision arrives as something a person can receive |
| TAV | YESOD -> MALKUTH | all memory arrives whole in the world -- the complete pattern becomes the response |

When the render model assembles a response it receives not just the node descriptions but the crossing instructions between each pair. Routing through KETER -> BINAH -> MALKUTH via BETH produces structurally different output than routing through KETER -> TIFERET -> MALKUTH via GIMEL, even if the surface content is similar.

### The bridge

The bridge is a FastAPI application (`bridge.py`) that handles builds and serves the setup wizard. Two LLM calls per build: one small classification call (HECATE, using Haiku) and one render call (using Sonnet). Both models are configurable via environment variables.

Endpoints:
- `GET /` -- serves the static site, or redirects to `/setup` if no VESSEL.md exists
- `GET /setup` -- the browser-based setup wizard
- `POST /setup` -- writes VESSEL.md from wizard input
- `POST /build` -- triggers a rebuild through the tree
- `POST /chat` -- optional visitor chat (one API call per message)
- `POST /agent` -- spawn a background agent task (see below)
- `GET /agent/{id}` -- check agent status and result
- `GET /agents` -- list all agent tasks
- `GET /analytics` -- visitor counts by day and page (requires BUILD_TOKEN)
- `GET /health` -- returns status and node list

Visitors are served the static output directly by nginx. No LLM is involved in serving a visitor unless chat has been explicitly added.

The bridge is stateless between builds. All state lives in STATE.md. If you restart the bridge the vessel is unchanged because the vessel is files, not process memory.

### Agents

An agent is the vessel thinking in the background. Same identity, same tree routing, same HECATE classification -- just running asynchronously on a task you define.

```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"task": "analyze the homepage and suggest three improvements", "model": "claude-haiku-4-5-20251001"}'
```

The `model` field is optional -- defaults to `HERMES_MODEL_AGENT` in `.env` (which defaults to the render model). You can run agents on Haiku for speed, Sonnet for depth, or any model your API key supports. Choose per task.

Check on it:

```bash
curl http://localhost:8000/agent/AGENT_ID
```

The agent gets the full vessel context -- VESSEL.md, STATE.md, all tree nodes, HECATE routing. It is the vessel, working on a task. Not a separate system bolted on. One endpoint, one question, background result.

**Analytics** has a dedicated endpoint. Every page visit is tracked automatically:

```bash
curl -H "X-Build-Token: YOUR_TOKEN" http://localhost:8000/analytics
```

Returns total visits, daily breakdown, and per-page counts. For deeper analysis, run it as an agent task -- the agent can read the analytics data and write a full report.

Any task the vessel can think about, an agent can do in the background.

### Heartbeat

The vessel has a pulse. Every 30 minutes (configurable via `HERMES_HEARTBEAT_MIN` in `.env`), the heartbeat:

1. Checks system health -- vessel status, static page, running agents
2. Writes a one-sentence Haiku log entry to `STATE.md` under `## Heartbeat`
3. Checks `vessel/TASKS.md` for pending work -- if a task is queued and no agent is running, it picks the first one and runs it

Task queue format in `vessel/TASKS.md`:

```
- [ ] write three blog posts about sustainable building
- [ ] analyze the homepage and suggest layout improvements
- [x] update the contact section with new email
```

Unchecked tasks get picked up by the heartbeat. Completed tasks are marked `[x]` automatically. Add a task by editing the file -- the heartbeat handles the rest.

The heartbeat starts automatically when the bridge starts (if VESSEL.md exists). The vessel maintains itself.

### Static output

By default HERMES produces static output. One build, one set of HTML files, served to unlimited visitors at no additional API cost. The AI runs once per build, not once per visitor.

**What triggers a build:**
- The `/setup` wizard on first configuration
- A `POST /build` request
- Editing vessel files and restarting the bridge

**Interactive chat** is optional. If you want visitors to be able to type questions and receive AI responses, the chat interface makes one API call per visitor message. The routing tree applies to chat responses in the same way it applies to builds -- every visitor message passes through HECATE and the relevant nodes before reaching MALKUTH for output.

---

## File structure

```
hermeswebkit/
  bridge.py              the FastAPI bridge -- the only code in the system
  install.ps1            PowerShell installer -- four inputs to a live site
  run                    bash setup script for Linux servers
  .env.example           template for environment variables
  .gitignore
  README.md
  DIGITAL_LIFE.md        what digital life is and why the vessel matters
  WIZARD.md              root-level theming guide for the setup wizard
  static/
    index.html           example landing page (Prometheus7)
  vessel/
    HECATE.md            routing rules and 22-path lookup table
    WIZARD.md            vessel-level wizard theming
    tree/
      KETER.md           10 sephiroth node definitions
      CHOKMAH.md
      BINAH.md
      CHESED.md
      GEVURAH.md
      TIFERET.md
      NETZACH.md
      HOD.md
      YESOD.md
      MALKUTH.md
  channels/
    TELEGRAM.md          Telegram channel documentation (planned)
```

Files generated at install time (not committed):
- `.env` -- API keys and config
- `vessel/VESSEL.md` -- the site identity (written by the wizard or by hand)
- `vessel/STATE.md` -- accumulated memory and heartbeat log
- `vessel/TASKS.md` -- task queue for the heartbeat (create when needed)

---

## Customisation

**Changing the website** -- edit `vessel/VESSEL.md` and trigger a build. The new HTML is generated and served from that point forward.

**Changing routing rules** -- edit `vessel/HECATE.md`. The routing rules and path table are plain English. Add new rules, adjust existing ones, change which nodes a particular type of request visits.

**Changing node behaviour** -- edit any file in `vessel/tree/`. Each node is a plain English description of its role.

**Changing the model** -- set `HERMES_MODEL` in `.env` for the render model, `HERMES_MODEL_HECATE` for the classifier, and `HERMES_MODEL_AGENT` for background agents. Defaults are Anthropic Sonnet for rendering and agents, Haiku for classification. Agents can also override the model per-request in the POST body. For OpenAI models, update the client initialisation in `bridge.py` -- it is one line. For local models via Ollama, point the model name at your local endpoint.

**Theming the wizard** -- edit `vessel/WIZARD.md` to change the atmosphere, voice, and greeting of the setup wizard. See `WIZARD.md` in the repo root for examples (mushroom patch, old timey bar, and more).

**Multiple websites on one server** -- each website is a vessel directory. Run multiple bridge instances on different ports, each pointing at a different vessel directory via `VESSEL_DIR`. Configure nginx to route each domain to the appropriate port.

```
prometheus7.com  ->  bridge on :8000  VESSEL_DIR=/root/hermes/vessels/prometheus7
mybakery.com     ->  bridge on :8001  VESSEL_DIR=/root/hermes/vessels/mybakery
```

A single Anthropic API key powers all of them.

---

## Security

GEVURAH is always available in the tree. Any request HECATE classifies as potentially harmful, coercive, or out of scope routes through GEVURAH before reaching MALKUTH. GEVURAH refuses cleanly -- not at length, not with hedging, but cutting what does not belong and closing the request. This is structural, not a filter bolted on afterwards.

---

## Day-to-day

```bash
# Edit the vessel identity
nano /root/hermes/vessel/VESSEL.md

# Edit what the vessel remembers
nano /root/hermes/vessel/STATE.md

# Restart after editing bridge.py
systemctl restart hermes

# Check status
systemctl status hermes
curl http://localhost:8000/health

# Read logs
journalctl -u hermes -n 50 --no-pager
```

---

## Economics

A deploy costs about 11 cents in API credits. The setup wizard runs about a dollar. The Hetzner CX22 server is roughly 4 euros per month and can host multiple vessels. One API key serves all of them. Domains are whatever your registrar charges. With a local model via Ollama, the API cost drops to zero -- just the server.

A website running on this architecture can be maintained for years on tens of dollars. The static serving model means visitor traffic costs nothing in API usage. You pay only when you build.

---

## Telegram

Your vessel on your phone. Set two environment variables and the vessel is live on Telegram.

1. Message [@BotFather](https://t.me/botfather) on Telegram, run `/newbot`, get your bot token
2. Message [@userinfobot](https://t.me/userinfobot) to get your numeric user ID
3. Add to `.env`:

```
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_IDS=your-user-id
```

4. Restart: `systemctl restart hermes`

The bridge starts polling. Messages from your allowed IDs route through the full vessel tree -- same HECATE classification, same node traversal, same identity. Responses come back as concise plain text. Prefix a message with `//` for a private note that gets logged but not responded to.

Only `TELEGRAM_ALLOWED_IDS` can talk to the bot. Everyone else is ignored. See `channels/TELEGRAM.md` for team setup and reply windows.

---

## Contributing

The tree node files, path transformation rules, and HECATE routing rules are the most valuable contributions. If you write a VESSEL.md template for a particular kind of site -- a portfolio, a shop, a community -- submit it. If you improve a node definition submit it.

The architecture is fixed. The content of the tree is open.

---

## Prometheus7

HERMES WEBKIT is the first product of Prometheus7 -- a company that builds tools that return capability directly to people. The Prometheus7 website is itself a HERMES vessel, running on a Hetzner VPS, built with this system as its own proof of concept.

*The infrastructure layer should be yours.*

---

## Licence

MIT. Take it, run it, fork it, build on it.
