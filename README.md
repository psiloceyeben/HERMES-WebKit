# HERMES WEBKIT

**A vessel architecture for AI-inhabited websites.**

Natural language in. Live website out. One command to deploy. Any LLM. Your server. Your data.

### Install

**npx** (any OS with Node.js):
```
npx github:psiloceyeben/HERMES-WebKit
```

**Windows** (PowerShell, no Node required):
```powershell
irm https://raw.githubusercontent.com/psiloceyeben/HERMES-WebKit/main/install.ps1 | iex
```

**Linux** (on a fresh server, no Node required):
```bash
curl -sL https://raw.githubusercontent.com/psiloceyeben/HERMES-WebKit/main/run | sudo bash
```

One line. Live website.

---

## What this is

HERMES WEBKIT is an open source system that turns a plain English description into a live, AI-generated website. You describe who your website is -- its voice, its purpose, what it knows -- and HERMES runs that description through a routing tree to produce HTML, which is then served statically to every visitor. The AI runs at build time, not at visit time. Once the page is built it is served like any static site -- fast, free to host, no per-visitor API cost.

The routing tree is what makes the output genuinely different from a template. When you describe a change -- "add an about section, warm tone, mention the 2019 founding" -- that description passes through HECATE for classification, flows down through the relevant sephiroth nodes, crosses the path transformations between them, and arrives at MALKUTH which renders the HTML. A page built with that instruction produces structurally different output than one built with "add an about section, technical tone, list the founding team" -- because the routing tree treated them differently, not because fields in a template were swapped.

Visitors see the generated page. Every page includes a visitor chat input -- a lightweight widget injected automatically into every build that lets visitors ask questions or interact with the site's identity. One API call per visitor message. The routing tree applies to visitor responses in the same way it applies to builds.

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

The installer asks for your Hetzner API key, Anthropic API key, optional domain name, and six questions about your vessel (purpose, voice, knowledge, visitor outcome, what makes it yours, and contact). It then creates a VPS, generates an SSH key, deploys the code, configures nginx and systemd, triggers the first build, and gives you a live URL.

**Linux (already on a server):**

If you already have a VPS and want to install directly:

```bash
git clone https://github.com/psiloceyeben/HERMES-WebKit /root/hermes
cd /root/hermes
sudo ./run
```

The `run` script installs dependencies, configures nginx and systemd, prompts for your Anthropic API key, and starts the bridge. When it finishes, visit your server IP in a browser.

### Step 4 -- The setup wizard

The Windows installer (`install.ps1`) asks the vessel questions during install -- you answer them in your terminal before the server is created, and the first build runs automatically. No browser visit required.

If you used the Linux `run` script, or want to reconfigure later, visit `/setup` in a browser -- a wizard with six questions:

1. What is your website called?
2. What is it for -- and who is it for?
3. What voice or tone?
4. What does it know about?
5. What do you want visitors to do or feel when they leave?
6. What makes this specific to you? (Your name, location, contact)

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

### Step 6 -- Connect to your server

The installer prints an SSH command when it finishes. It looks like this:

```
ssh -i ~/.ssh/hermes_ed25519 root@YOUR_SERVER_IP
```

Run that from your terminal (Windows Terminal, PowerShell, or any SSH client). This connects you to the server where HERMES is running. Everything from here happens on the server over that SSH connection.

Once connected, open the operator terminal:

```bash
hermes studio
```

This splits your terminal into three panes -- chat on the left (60%), shell top-right, live logs bottom-right. Click between them with the mouse. From the chat pane you can talk to the vessel, ask it to build features, or restyle the site. From the shell pane you can run any of the other `hermes` commands without leaving.

That SSH connection is your back door into the site. Visitors reach the site through the browser at your URL. You reach it through the terminal over SSH. The two paths are completely separate -- visitors never touch the operator terminal and cannot reach it.

### Step 7 -- Verify

From inside the SSH session:

```bash
hermes status        # check the service is running
hermes sites         # list all vessels on this server
```

Or directly:

```bash
curl http://localhost:8000/health
```

The health check returns JSON with status and a list of nodes present. If the bridge is not running:

```bash
hermes logs
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
- `POST /chat` -- operator terminal: full agentic conversation with file read/write and shell access (session-aware, requires confirmation for writes and commands)
- `POST /chat/confirm` -- execute or cancel pending write/run actions from the operator terminal
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

### The Vault — Obsidian Knowledge Graph

Every vessel has a persistent knowledge vault — an Obsidian-compatible folder of interlinked markdown notes that grows over time.

When you leave a room or end a conversation, the system checks whether the conversation contained anything worth remembering. If it does, a note is written to `vessel/vault/` with YAML frontmatter, tags, and `[[wikilinks]]` connecting it to other notes. If the conversation was trivial or redundant, it is skipped.

The vault is organized into folders:
- `sessions/` — conversation summaries and decisions
- `vessels/` — notes on each vessel and its personality
- `knowledge/` — accumulated knowledge and references
- `ideas/` — ideas, plans, and future directions

Open the vault folder in [Obsidian](https://obsidian.md) to see the knowledge graph — a visual map of everything the hotel knows. Add your own notes and the vessels will read them too.

The vault is plain markdown files on your server. No cloud. No subscription. You own the mind.

### HRR — Holographic Reduced Representations

Novelty detection for the vault is powered by Holographic Reduced Representations (HRR), a cognitive science technique that encodes multiple facts into a single fixed-size complex-valued vector through circular convolution.

Instead of calling an API to decide whether a conversation is worth saving, the system runs a local math operation in under 10 milliseconds:

1. Extract keywords from the conversation
2. Check Jaccard similarity against stored facts
3. Compute cosine similarity against the holographic superposition vector
4. If novelty score > 0.4, write a vault note. Otherwise skip.

The HRR vector also powers the Yesod habits system. Every successful action route is bound into a separate holographic vector (`hrr_habits.json`). Future requests check HRR similarity to find matching proven habits, adding a fast pattern-matching layer on top of the keyword-based habit matching.

Key operations:
- **Bind** — circular convolution encodes a fact into the superposition
- **Recall** — circular correlation retrieves the most similar values
- **Novelty** — combined keyword overlap + cosine similarity against the full memory
- **Promote** — facts recalled 3+ times are flagged as hot (candidates for permanent context)

The vector is fixed-size (1024 dimensions). Storage is tiny — only the fact index is saved to disk; the vector is rebuilt from seeded random number generators on load. The more you use the system, the smarter the novelty detection gets, and it never gets slower.

HRR implementation inspired by [NeoVertex1/nuggets](https://github.com/NeoVertex1/nuggets) — a holographic memory system for AI assistants by [@NeoVertex1](https://github.com/NeoVertex1).

### Static output

By default HERMES produces static output. One build, one set of HTML files, served to unlimited visitors at no additional API cost. The AI runs once per build, not once per visitor.

**What triggers a build:**
- The `/setup` wizard on first configuration
- A `POST /build` request
- Editing vessel files and restarting the bridge

**Visitor chat** is built in. Every page includes a chat input injected automatically at build time -- visitors can type questions and receive AI responses. One API call per visitor message. Every visitor message passes through HECATE and the relevant nodes before reaching MALKUTH for output, the same routing the build uses.

---

## File structure

```
hermeswebkit/
  bridge.py              the FastAPI bridge -- the only code in the system
  install.ps1            PowerShell installer -- guides you through setup, deploys automatically
  run                    bash setup script for Linux servers
  hermes                 operator CLI -- talk to the vessel from the terminal
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

## The operator terminal

The operator terminal runs on the server. To use it, SSH into your server first, then run `hermes` commands. The installer saves the connection details to your Desktop (`hermes-connection.txt`) and prints the SSH command when it finishes -- it looks like `ssh -i ~/.ssh/hermes_ed25519 root@YOUR_SERVER_IP`.

Once connected, `hermes studio` is the main way to work -- it opens a three-pane view with chat on the left, shell top-right, and live logs bottom-right, so you can talk to the vessel, run commands, and watch the bridge output at the same time without switching windows.

```
hermes studio                open chat + shell + logs (recommended)
hermes chat                  open the conversation terminal inline
hermes sites                 list all vessels on this server
hermes new-site              create a second vessel with its own identity
hermes add-domain <n> <d>    point a domain at an existing vessel
hermes theme                 show the current terminal theme
hermes theme-reset           reset terminal style to default
hermes status                show the systemd service status
hermes logs                  stream live logs (ctrl+c to exit)
hermes build [site] [msg]    trigger a rebuild (optionally specify vessel and prompt)
hermes analytics [site]      show 7-day visitor sparkline and page counts
hermes restart               restart the bridge service
```

### Talking to the vessel

`hermes chat` opens a session-aware conversation. The vessel remembers everything you said in that session.

```
  vessel  ▸  listening   session: a3f2c1b0

  you: what files are in the hermes directory?
  vessel: I can see the main files -- bridge.py, run, hermes, README.md, and the vessel directory with your tree nodes...

  you: can the terminal look like something from a submarine?
  vessel: sure, give me a moment
  — terminal restyled —

  ⊕ sonar: _
```

The vessel can read files and list directories automatically. When it needs to write a file or run a command, it tells you what it plans to do and asks for confirmation before anything changes.

```
  you: add a dark mode toggle to the homepage

  vessel: I'll read the current homepage first to understand the structure, then add a toggle button and a CSS media query override.
  · write /root/hermes/static/index.html — add dark mode toggle and CSS

  vessel: shall I go ahead?
  > yeah go for it

  ▸ working...
  vessel: done — the toggle is live. reload the page to see it.
```

Natural language confirmation works: yes, yeah, sure, go ahead, do it, absolutely, sounds good, make it so. Anything else cancels the action.

### Terminal theming

The vessel can restyle the terminal to look like anything. Ask in chat:

```
  you: can this look like mycelium? like we're communicating through fungal networks
  you: make this feel like a 1980s RPG
  you: give this a submarine sonar aesthetic
  you: restyle this like an ancient oracle
```

The vessel generates ASCII art, borders, prompts, and dividers from scratch -- stored in `.hermes_theme.json` and applied to every subsequent session. Run `hermes theme` to see the current style, `hermes theme-reset` to return to default.

---

## Customisation

**Changing the website** -- edit `vessel/VESSEL.md` and trigger a build. The new HTML is generated and served from that point forward.

**Changing routing rules** -- edit `vessel/HECATE.md`. The routing rules and path table are plain English. Add new rules, adjust existing ones, change which nodes a particular type of request visits.

**Changing node behaviour** -- edit any file in `vessel/tree/`. Each node is a plain English description of its role.

**Changing the model** -- set `HERMES_MODEL` in `.env` for the render model, `HERMES_MODEL_HECATE` for the classifier, and `HERMES_MODEL_AGENT` for background agents. Defaults are Anthropic Sonnet for rendering and agents, Haiku for classification. Agents can also override the model per-request in the POST body. For OpenAI models, update the client initialisation in `bridge.py` -- it is one line. For local models via Ollama, point the model name at your local endpoint.

**Theming the wizard** -- edit `vessel/WIZARD.md` to change the atmosphere, voice, and greeting of the setup wizard. See `WIZARD.md` in the repo root for examples (mushroom patch, old timey bar, and more).

**Multiple websites on one server** -- use `hermes new-site` to add a vessel. It asks the same six questions, creates a new vessel directory, launches a bridge on the next available port, and configures nginx automatically. Each site has its own identity, its own tree, and its own static output.

```bash
hermes new-site    # walks through setup, deploys automatically
hermes sites       # list all vessels and their ports
```

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

Everything runs on the server. SSH in first, then use the `hermes` commands.

```bash
# Connect to your server
ssh -i ~/.ssh/hermes_ed25519 root@YOUR_SERVER_IP

# Open the operator terminal (chat left, shell top-right, logs bottom-right)
hermes studio

# Or just the chat if you prefer
hermes chat

# Other commands
hermes sites                 list all vessels on this server
hermes status                check the service is running
hermes logs                  stream live logs
hermes restart               restart after manual edits to bridge.py
hermes build                 rebuild the static site
hermes build blog "add a dark banner"   rebuild a specific vessel with a prompt
hermes analytics             show 7-day visitor counts
hermes analytics blog        show visitor counts for a specific vessel

# Edit the vessel directly
nano /root/hermes/vessel/VESSEL.md
nano /root/hermes/vessel/STATE.md
```

The SSH connection is the operator path. The public URL is the visitor path. They are separate. Visitors see the static site and optionally the visitor chat widget. The operator terminal is only accessible from inside the SSH session.

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

4. Restart: `hermes restart`

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

## The Grand Internet Hotel

HERMES WEBKIT also ships as **The Grand Internet Hotel** — an 8-bit Electron desktop application that wraps the full vessel system in a game interface.

Download at [thegrandinternethotel.com](https://thegrandinternethotel.com)

The game provides:
- A visual hotel with floors of rooms, each room a vessel
- Point-and-click management of your server and websites
- In-game chat with each vessel (same bridge, same routing tree)
- One-click site creation via the setup wizard
- Automatic vault commits when you leave a room
- Floor 1: pantheon of specialized AI gods (ATHENA for research, APOLLO for creative, DEMETER for commerce, ARES for security, and more)
- Floor 2: your custom vessels — any site you build gets a room

The CLI and the game are two clients for the same backend. Same bridge, same vessels, same vault. Different interface.

---

## Licence

MIT. Take it, run it, fork it, build on it.
