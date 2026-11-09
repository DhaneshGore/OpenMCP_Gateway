# OpenMCP Gateway — Animated Video Script (Full Walkthrough)

**Total duration:** ~8–10 minutes
**Audience:** Non-technical
**Style:** Animated slides + AI avatar narrator
**Recommended tools:** Synthesia / HeyGen / InVideo / Lumen5

---

## HOW TO USE THIS SCRIPT

1. Open Synthesia, HeyGen, InVideo, or Lumen5
2. Create a new video project
3. For each scene: paste the **NARRATOR** text as the spoken script
4. Use the **ON SCREEN** notes to set slide text, background, and animation
5. Each scene is ~45 seconds — total ~11 scenes = ~8 min

---

## Scene 1 — The Problem (0:00–0:45)

**ON SCREEN:**
Animation of a person surrounded by floating AI tools (search, calendar, billing, docs, notifications). Person looks overwhelmed. Tools are disconnected, floating separately.

**NARRATOR:**
> "Imagine you have an AI assistant — like ChatGPT or Claude — and you want it to actually do things for you. Search your database. Send a notification. Pull up a customer invoice. Each of those abilities is built as a separate tool, running on your own server. But how does your AI connect to all of them safely? How do you make sure only the right people can use them? How do you stop one broken tool from crashing everything else? That's exactly the problem OpenMCP Gateway solves."

---

## Scene 2 — What is an MCP Server? (0:45–1:30)

**ON SCREEN:**
Restaurant animation. Customer (labelled "AI") sits at table. Waiter (labelled "server.py") takes order. Kitchen (labelled "Your Database / API") prepares the food. Waiter brings result back.

**NARRATOR:**
> "Think of an MCP server like a waiter in a restaurant. The customer — your AI — doesn't go into the kitchen directly. It tells the waiter what it wants. The waiter goes to the kitchen, gets the result, and brings it back. Your server.py file is that waiter. It knows how to talk to your database, your API, your files — and it speaks a standard language that any AI can understand. That standard language is called MCP — the Model Context Protocol."

---

## Scene 3 — What is the Gateway? (1:30–2:15)

**ON SCREEN:**
Hotel lobby animation. Multiple guests (labelled "AI Client 1", "AI Client 2") arrive at the front door. Front desk (labelled "OpenMCP Gateway") checks ID badges. Desk staff directs each guest to a different room (labelled "billing", "search", "calendar").

**NARRATOR:**
> "Now imagine you have five waiters — one for billing, one for search, one for calendar, one for documents, one for notifications. You need a front desk. Someone who checks who's allowed in, directs them to the right waiter, and keeps a log of everything that happens. That front desk is the OpenMCP Gateway. It runs on your Linux server, sits in front of all your MCP servers, and gives you one single address your AI connects to — with security, routing, and monitoring all built in."

---

## Scene 4 — The Folder Structure (2:15–3:00)

**ON SCREEN:**
Animated file tree. Each file and folder appears one by one with a soft pop animation. Labels appear next to each file explaining what it is in plain English.

```
mcp-deploy/
├── .env               ← your secret keys (never share this)
├── Dockerfile         ← blueprint for the container
├── docker-compose.yml ← the start button
├── config/
│   └── openmcp.yaml   ← gateway settings and rules
└── servers/
    ├── billing/server.py       ← billing tools
    ├── search/server.py        ← search tools
    ├── docs/server.py          ← document tools
    ├── calendar/server.py      ← calendar tools
    └── notifications/server.py ← notification tools
```

**NARRATOR:**
> "On your Linux server, everything lives in one folder called mcp-deploy. Inside it: your five server files each in their own subfolder, a config file that tells the gateway how to run them, a dot-env file for your secret keys — things like API passwords that should never be shared — and two Docker files that package everything up and run it. Each piece has one job, and together they make the whole system work."

---

## Scene 5 — The Config File (3:00–3:45)

**ON SCREEN:**
Animated YAML config file. Each section highlights in a different colour as the narrator mentions it. Labels in plain English float beside each section: "Address", "Who can connect", "Your servers".

**NARRATOR:**
> "The config file is where you set the rules. First — the address: listen on port 8080, only accept requests from known addresses. Second — security: who is allowed to connect? You create a secret token, like a password, and only clients who send that token get through. Third — the servers: list each of your five MCP servers by name, tell the gateway where the file lives. That's it. No programming needed — just fill in the blanks and you're done."

---

## Scene 6 — Docker: The Container (3:45–4:30)

**ON SCREEN:**
Animation of a shipping container being packed. Items going in: gateway icon, 5 server.py files, Python logo, dependencies. Container sealed. Ship (labelled "Linux VM") with the container on deck. Ship sails.

**NARRATOR:**
> "Docker is like a shipping container for software. You pack everything inside — the gateway, all five of your servers, all their dependencies — seal it up, and ship it to your Linux VM. It runs exactly the same way every time, on any Linux machine, regardless of what else is installed. The Dockerfile is the packing list. The docker-compose file is the button that loads the container onto the ship and sets it sailing. One command — docker compose up — and everything starts."

---

## Scene 7 — Deploy Step by Step (4:30–6:00)

**ON SCREEN:**
Terminal window animation. Each command types itself out automatically. After each command a green checkmark appears. Progress bar at the top shows steps 1 through 6.

**NARRATOR:**
> "Here's the full deploy, step by step. On your Linux VM, open a terminal."

**[Step 1 on screen: git clone command]**
> "First, download the gateway code from GitHub with one command."

**[Step 2 on screen: mkdir command]**
> "Create your deployment folder and move into it."

**[Step 3 on screen: file copy commands]**
> "Copy in your server files and your secrets file."

**[Step 4 on screen: python3 hash command + output]**
> "Generate your security token. This command takes your chosen password and turns it into a secure hash you put in the config. No one can reverse it back to your password."

**[Step 5 on screen: docker compose build]**
> "Build the container. Docker reads the Dockerfile and packages everything up. This takes a minute the first time."

**[Step 6 on screen: docker compose up -d + green checkmarks]**
> "Start it. The dash-d flag means it runs in the background — your terminal is free and the gateway keeps running forever, even after you close the terminal."

---

## Scene 8 — Multiple Servers, One Address (6:00–6:45)

**ON SCREEN:**
Animation of 5 coloured pipes flowing from left to right. Each pipe labelled: billing (blue), search (green), docs (yellow), calendar (orange), notifications (red). All 5 pipes merge into one single tap on the right side, labelled "port 8080". AI icon connects to the single tap.

**NARRATOR:**
> "Here's the magic. All five servers are running inside that one container. Your AI connects to a single address — your server IP, port 8080. When it asks for a tool, the gateway automatically routes the call to the right server. Tools are labelled with the server name — billing__create_invoice, search__find_document, calendar__add_event. From the AI's point of view, it's talking to one place. Behind the scenes, five separate processes are each doing their job independently."

---

## Scene 9 — What Happens When the AI Makes a Call (6:45–7:30)

**ON SCREEN:**
Step-by-step flow diagram. Each step lights up in sequence with a pulse animation as narrator mentions it. Arrows animate between steps.

```
Step 1:  AI sends request → port 8080
             ↓
Step 2:  Gateway checks token  ✅ or ❌
             ↓
Step 3:  Gateway reads tool name → routes to correct server
             ↓
Step 4:  server.py receives call → runs your logic
             ↓
Step 5:  Result flows back up
             ↓
Step 6:  AI receives the answer
```

**NARRATOR:**
> "When your AI sends a request, the gateway first checks the security token. Wrong token — request blocked, no data exposed. Correct token — the gateway reads the tool name, strips the server prefix, and passes the call directly to the right server through a fast internal pipe. Your server.py does its work — hits your API, queries your database, reads your files. The result flows back through the gateway to your AI. The whole round trip typically takes milliseconds. And if one server crashes, the gateway restarts it automatically — the other four keep working without interruption."

---

## Scene 10 — Verifying It Works (7:30–8:00)

**ON SCREEN:**
Split screen. Left: terminal showing curl commands typing out. Right: browser showing green OK responses. Green tick marks appearing one by one.

**NARRATOR:**
> "How do you know everything is running correctly? Three quick checks. First — the health endpoint: open a browser or terminal and hit your server address at slash health. You should see a green OK response immediately. Second — the ready endpoint: this confirms all five of your servers started up successfully. Third — check the logs: docker compose logs shows you exactly what each server is doing in real time, with timestamps. If anything went wrong, the error message tells you which server failed and why, so you can fix it fast."

---

## Scene 11 — Outro & Summary (8:00–8:30)

**ON SCREEN:**
Clean slide with three large icons in a row:
- Shield icon → "Secure" — one token, one front door
- Plug icon → "Connected" — all your tools, one address
- Chart icon → "Observable" — health checks, logs, metrics

Fade to project name and URL.

**NARRATOR:**
> "To summarise: OpenMCP Gateway gives your AI a single, secure front door to all your tools. You write your server logic in Python. You describe your servers in a simple config file. Docker packages and runs everything on your Linux VM with one command. Your AI connects to one address, calls tools by name, and the gateway handles everything else — security, routing, automatic restarts, and monitoring. The full deployment guide with every file and command is linked in the description below. Thanks for watching."

---

**END OF SCRIPT**

---

## Slide Text Summary (for Synthesia / HeyGen slide mode)

| Scene | Slide Title | Key Bullet Points |
| --- | --- | --- |
| 1 | The Problem | AI needs tools · Tools are disconnected · How to connect them safely? |
| 2 | What is an MCP Server? | server.py = the waiter · AI = customer · Your DB/API = kitchen |
| 3 | What is the Gateway? | One front door for all servers · Security + routing + monitoring |
| 4 | Folder Structure | One folder, 5 server subfolders, config, secrets |
| 5 | The Config File | Address · Security token · List your servers |
| 6 | Docker | Pack it all up · Run anywhere · One command to start |
| 7 | Deploy Steps | Clone → Copy → Token → Build → Up → Done |
| 8 | One Address, 5 Servers | billing__tool · search__tool · All through port 8080 |
| 9 | Request Flow | Token check → Route → Run → Return |
| 10 | Verify It Works | /health → OK · /ready → OK · docker logs |
| 11 | Summary | Secure · Connected · Observable |
