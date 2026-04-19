<div align="center">

# Drift — AI-Driven Narrative Game Engine for Minecraft

**Type a story. Watch it become a playable world. Let AI evolve it until it's perfectly balanced.**

[![Java 21](https://img.shields.io/badge/Java-21-orange?logo=openjdk)](https://openjdk.org/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Spring Boot 3.3](https://img.shields.io/badge/Spring%20Boot-3.3-6DB33F?logo=springboot)](https://spring.io/projects/spring-boot)
[![Minecraft 1.20](https://img.shields.io/badge/Minecraft-1.20-62B47A?logo=minecraft)](https://minecraft.net/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Features](#features) · [Architecture](#architecture) · [Quick Start](#quick-start) · [Experience Panel](#experience-panel) · [API Reference](#api-reference) · [Contributing](#contributing)

</div>

---

## The Problem

Building game levels is slow. Playtesting is slower. Balancing difficulty? Even slower. Most indie teams spend **80% of dev time** on iteration loops that could be automated.

## The Solution

Drift is a **full-stack AI game engine** that closes the loop between *writing*, *building*, *testing*, and *balancing* — all inside Minecraft:

```
"A forgotten temple, gems scattered in shadows, guards patrol"
         |  natural language
   +-------------------+
   |  Drift Backend     | -> Parses text into structured world_patch
   |  (FastAPI)         | -> Runs Monte Carlo simulation (200 runs)
   +--------+----------+ -> Predicts win rate, difficulty, failure modes
            |
   +-------------------+
   |  MC Plugin         | -> Builds platforms, spawns NPCs, places triggers
   |  (Bukkit 1.20)     | -> Wires up quest runtime + rule events
   +--------+----------+
            |
   +-------------------+
   |  RL Agent          | -> Bot plays level 200x with 3 skill profiles
   |  (StrategyBot)     | -> Measures Flow Zone convergence [60%-80%]
   +--------+----------+
            |
   +-------------------+
   |  AsyncAIFlow       | -> LLM redesigns level based on bot feedback
   |  (Spring Boot)     | -> Beam search explores N parallel variants
   +-------------------+ -> Repeats until difficulty converges
```

**One sentence in, a balanced playable level out.** No game designer needed.

---

## Features

### Natural Language to Playable World
Type a story premise in Chinese or English. Drift parses it into a structured `world_patch` — platforms, NPCs, triggers, weather, particles, sound — and builds it inside Minecraft in seconds.

### Monte Carlo Difficulty Simulation
Before any player touches the level, Drift runs **200 Monte Carlo simulations** against the level's rule set. It predicts win rate, average completion steps, failure reasons, and auto-classifies difficulty (Easy / Medium / Hard / Extreme).

### AI Bot Playtesting (StrategyBot)
A rule-based bot with **3 skill profiles** (beginner / average / expert) plays the level through a TCP bridge to the Minecraft server. The evolution system iterates until the `average` player's win rate hits the **Flow Zone [60%–80%]** for 3 consecutive generations.

### Beam Search Level Evolution
AsyncAIFlow orchestrates **multi-path beam search**: `beam_width` parallel level variants compete each generation. LLM (GPT-4 / DeepSeek) proposes entirely new design directions — not patches — based on weakness analysis.

### Cinematic Visual System
A data-driven **5-tier Difficulty Amplifier** (D1 to D5) scales visual complexity automatically:

| Level | Label | Visual Effects |
|-------|-------|---------------|
| D1 | Easy | Minimal — clean and simple |
| D2 | Normal | Particles + sound effects |
| D3 | Hard | BossBar + trigger zone particles + decorations |
| D4 | Epic | Beacons + weather shifts + large particle bursts |
| D5 | Legend | Thunder + night + cinematic fades — like a movie |

### Experience Control Panel
A self-contained **2476-line HTML panel** with dark hacker aesthetic. Design levels, preview simulations, publish to Minecraft, and monitor evolution — all from your browser.

### GitHub Projects Integration
Every created level automatically syncs to a **GitHub Projects V2** board as a draft item with custom fields (Status / Difficulty / Source / LevelID). Track the full lifecycle: `Created -> Testing -> In Flow Zone -> Done`.

### Closed-Loop Story Engine
Full narrative runtime with:
- **QuestRuntime** — rule-event-driven task system (collect, proximity, interact, NPC talk, timer)
- **Scene Evolution** — incremental scene patches based on player actions
- **Memory System** — inventory and flags carry across levels, enabling conditional story branches
- **Beat Progression** — multi-beat story arcs with keyword triggers and choice panels
- **NPC Intent Engine** — DeepSeek-powered natural language to multi-intent dispatch

---

## Architecture

```
+----------------------------------------------------------------------+
|                         Drift System                                  |
|                                                                       |
|  +--------------+    +--------------+    +----------------------+    |
|  | Experience   |    | Demo         |    | Drift Backend        |    |
|  | Panel (HTML) |--->| Dashboard    |    | (FastAPI :8000)      |    |
|  | :8000/panel  |    | (HTML)       |    |                      |    |
|  +--------------+    +--------------+    | +- StoryEngine       |    |
|                                          | +- QuestRuntime      |    |
|                                          | +- SimulationEngine  |    |
|                                          | +- DifficultyAmp     |    |
|                                          | +- ExperienceRuntime |    |
|                                          | +- SceneGenerator    |    |
|                                          | +- NPC / AI Intent   |    |
|                                          +----------+-----------+    |
|                                                     |                |
|  +--------------+    +--------------+    +----------v-----------+    |
|  | StrategyBot  |    | MC Server    |    | MC Plugin (Bukkit)   |    |
|  | (Node.js)    |<-->| :25565       |<-->| WorldPatchExecutor   |    |
|  | TCP :9999    |    |              |    | IntentDispatcher     |    |
|  +------+-------+    +--------------+    | RuleEventBridge      |    |
|         |                                | QuestLog / MiniMap   |    |
|         |                                +----------------------+    |
|  +------v-------+                                                    |
|  | MetaAgent    |    +------------------------------------------+    |
|  | (Evolution   |--->| AsyncAIFlow (Spring Boot :8080)          |    |
|  |  Controller) |    | Redis queue -> Worker pool -> DAG engine |    |
|  +--------------+    |                                          |    |
|                      | Workers: plan / arc / experiment / code  |    |
|                      |          deploy / review / test / refresh|    |
|                      +------------------------------------------+    |
+----------------------------------------------------------------------+
```

### Tech Stack

| Layer | Technology | Lines |
|-------|-----------|-------|
| **Drift Backend** | Python 3.11, FastAPI, SQLite, DeepSeek API | ~15,000 |
| **AsyncAIFlow** | Java 21, Spring Boot 3.3, Redis, MySQL, MyBatis Plus | ~13,000 |
| **MC Plugin** | Java, Bukkit/Spigot 1.20 API | ~8,000 |
| **RL Agent** | Node.js (Mineflayer), Python (MetaAgent) | ~3,000 |
| **Python Workers** | 16 specialized workers (arc, experiment, plan, code, ...) | ~4,500 |
| **Frontend Panels** | Vanilla HTML/CSS/JS (zero dependencies) | ~3,400 |

---

## Quick Start

### Prerequisites

- Java 21+ and Maven
- Python 3.11+ and pip
- Node.js 18+ and npm
- Redis (for AsyncAIFlow queue)
- Minecraft Server 1.20 (Paper/Spigot)

### 1. Start Drift Backend

```bash
cd drift-system_4.8/backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/panel/drift-experience-panel.html` — you now have the Experience Panel.

### 2. Start AsyncAIFlow

```bash
cd AsyncAIFlow_4.8
docker compose up -d redis    # start Redis
mvn spring-boot:run           # start workflow engine
```

### 3. Install MC Plugin

```bash
cd drift-system_4.8/plugin
mvn package -DskipTests
cp target/DriftSystem-*.jar /path/to/mc-server/plugins/
# restart MC server
```

### 4. Create Your First Level

**Option A — Experience Panel (recommended):**
1. Open `http://localhost:8000/panel/drift-experience-panel.html`
2. Type a story premise: `"A moonlit ice crystal palace, collect 5 star dusts to win"`
3. Set difficulty to 3, click **Quick Publish**
4. Level appears in Minecraft instantly

**Option B — API:**
```bash
curl -X POST http://localhost:8000/story/inject \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Ice Palace",
    "text": "A moonlit ice palace. Collect 5 star dust fragments to win.",
    "difficulty": 3,
    "player_id": "demo"
  }'
```

**Option C — In-game chat:**
```
Just type in Minecraft chat:
"Create a gem temple with patrolling guards"
```

### 5. Run Evolution (Optional)

```bash
cd drift-rl-agent
npm install && pip install -r requirements.txt
python meta/run_evolution.py --level <level_id> --difficulty 3
```

The bot plays the level, the designer redesigns it, and difficulty converges to the Flow Zone automatically.

---

## Experience Panel

The control panel is a single self-contained HTML file — no build step, no npm, no framework.

**6 panels in a 3x2 grid:**

| Panel | Function |
|-------|----------|
| **Create** | Story premise input + difficulty selector + Quick/Premium publish |
| **Simulation** | Monte Carlo results — win rate, avg steps, failure analysis, insights |
| **Runtime** | Live game state — triggers fired, player status, event timeline |
| **Evolution** | Start/stop evolution, view generation history, Flow Zone convergence |
| **Debug** | Raw world_patch inspector, diff viewer, rule evaluation trace |
| **Workflow** | AsyncAIFlow pipeline monitor — step-by-step progress with timing |

### Publish Modes

| Mode | Speed | How It Works |
|------|-------|-------------|
| **Quick Publish** | ~2s | Direct inject, MC plugin builds immediately |
| **Premium Publish** | ~60s | Full AI workflow: plan, arc, experiment, beam search, best variant |

---

## API Reference

### Drift Backend (:8000)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/story/inject` | POST | Create level from text (primary creation endpoint) |
| `/story/load/{player}/{level}` | POST | Load level for player with bootstrap patch |
| `/story/advance/{player}` | POST | Advance story with player input |
| `/story/refresh` | POST | AI workflow completion callback |
| `/story/levels` | GET | List all available levels |
| `/story/difficulty/{player}` | GET | Get player's current difficulty |
| `/story/auto-advance/{player}` | POST | Auto-advance based on NPC observability |
| `/world/story/rule-event` | POST | Process rule event (collect/interact/proximity/talk) |
| `/world/apply` | POST | Apply world patch to game state |
| `/evolution/start` | POST | Start evolution session for a level |
| `/evolution/status/{session}` | GET | Check evolution progress |
| `/github/project/status` | GET | GitHub Projects integration status |
| `/minimap/png/{player}` | GET | Render minimap as PNG |

### AsyncAIFlow (:8080)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/workflow/create` | POST | Create a workflow |
| `/action/create` | POST | Submit an action with DAG dependencies |
| `/action/{id}` | GET | Poll action result |
| `/action/poll` | GET | Worker polls for available work |
| `/action/result` | POST | Worker submits completed result |
| `/planner/execute` | POST | Premium Publish — full AI pipeline |

### In-Game Commands

| Command | Description |
|---------|-------------|
| `/level <id>` | Load a level |
| `/levels` | List available levels |
| `/talk <text>` | Natural language input to intent dispatch |
| `/drift status` | Show current story state |
| `/minimap` | Display minimap as in-game item |
| `/questlog` | View active quest objectives |
| `/easy` | Request difficulty reduction |
| `/replay` | Replay current level |

---

## Evolution System

The dual-loop evolution system automatically balances level difficulty:

```
+--- Inner Loop (per generation) ----------------------------+
|                                                             |
|  StrategyBot plays level x 3 skill profiles                 |
|       |                                                     |
|  EvalBridge computes per-skill completion rates              |
|       |                                                     |
|  MetaAgent checks Flow Zone [60%-80%] on "average" profile  |
|       |                                                     |
|  If not converged -> DesignerAgent (LLM) redesigns level    |
|       |                                                     |
|  New level published -> next generation                     |
|                                                             |
+-- Stops when Flow Zone streak >= 3 consecutive generations -+
```

### Skill Profiles

| Profile | Reaction Ticks | Uses `/easy` | Purpose |
|---------|---------------|-------------|---------|
| `beginner` | 10 (slow) | 30% chance | Simulates new players |
| `average` | 5 (normal) | 10% chance | **Flow Zone baseline** |
| `expert` | 2 (fast) | Never | Simulates speedrunners |

### Evolution Visualization

```bash
python meta/visualize_evolution.py          # Generate charts
node viewer/viewer_server.js                # Bot POV at :3007
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | LLM API key (GPT-4 / DeepSeek) |
| `DRIFT_URL` | `http://localhost:8000` | Drift backend URL |
| `ASYNCAIFLOW_URL` | `http://localhost:8080` | AsyncAIFlow URL |
| `GITHUB_PROJECTS_ENABLED` | `false` | Enable GitHub Projects sync |
| `GITHUB_TOKEN` | — | GitHub PAT (project scope) |
| `GITHUB_PROJECT_ID` | — | Project ID (`PVT_xxxxx`) |

### Key Config Files

| File | Purpose |
|------|---------|
| `drift-rl-agent/configs/drift_servers.yaml` | Server addresses, bot username, LLM model |
| `drift-rl-agent/configs/skill_profiles.yaml` | 3-tier skill parameters |
| `drift-rl-agent/configs/evolution_params.yaml` | Flow Zone bounds, generation count |
| `deploy/docker-compose.cloud.yml` | Cloud deployment (Redis + MySQL + services) |
| `deploy/systemd/` | Systemd service units for production |

---

## Repository Structure

```
drift-opc-workflow-v3/
|
+-- drift-system_4.8/               # Core: narrative engine + MC plugin
|   +-- backend/                    #   FastAPI backend (Python)
|   |   +-- app/api/                #     REST endpoints
|   |   +-- app/core/               #     Engine modules:
|   |   |   +-- story/              #       StoryEngine, StoryGraph, story_loader
|   |   |   +-- runtime/            #       ExperienceRuntime, SimulationEngine, DifficultyAmplifier
|   |   |   +-- ai/                 #       DeepSeek agent, intent engine, NLP
|   |   |   +-- quest/              #       QuestRuntime - rule-event task system
|   |   |   +-- npc/                #       NPC behavior engine
|   |   |   +-- world/              #       SceneGenerator, MiniMap, triggers
|   |   +-- requirements.txt
|   +-- plugin/                     #   Minecraft Bukkit plugin (Java)
|   |   +-- src/.../driftmc/        #     WorldPatchExecutor, IntentDispatcher, commands
|   +-- content/                    #   Level JSON files (flagship + generated)
|   +-- docs/                       #   System documentation
|
+-- AsyncAIFlow_4.8/                # Async workflow orchestrator
|   +-- src/                        #   Spring Boot backend (Java)
|   +-- python-workers/             #   16 specialized Python workers
|   |   +-- drift_arc_worker/       #     State graph generation
|   |   +-- drift_experiment_worker/#     Beam search experiments
|   |   +-- drift_plan_worker/      #     AI planning
|   |   +-- drift_code_worker/      #     Code generation
|   |   +-- drift_review_worker/    #     Quality review
|   |   +-- drift_test_worker/      #     Automated testing
|   |   +-- ...                     #     12 more workers
|   +-- scripts/                    #   Demo and launch scripts
|   +-- docs/                       #   Architecture docs
|
+-- drift-rl-agent/                 # AI player + designer evolution
|   +-- player/                     #   StrategyBot (Node.js) + skill profiles
|   +-- designer/                   #   DesignerAgent (LLM) + batch generation
|   +-- meta/                       #   MetaAgent + evolution controller
|   +-- viewer/                     #   Bot POV viewer + dashboard
|   +-- tests/                      #   Smoke tests (no MC needed)
|
+-- drift-experience-panel.html     # Level design control panel
+-- drift-demo-dashboard.html       # Workflow monitoring dashboard
+-- deploy/                         # Docker Compose + systemd configs
```

---

## Testing

### Smoke Tests (No Minecraft Required)

```bash
cd drift-rl-agent
python tests/smoke_test.py
# 24 tests, no MC server needed, no torch dependency
```

### Backend Self-Test

```bash
cd drift-system_4.8/backend
python drift_backend_selftest.py
```

### Full Integration Test

1. Start MC server + plugin
2. Start Drift backend + AsyncAIFlow
3. Open Experience Panel
4. Create level, verify in-game, run evolution, check convergence

---

## Contributing

Contributions are welcome! Areas where help is especially appreciated:

- **New trigger types** — add event types beyond collect/proximity/interact/npc_talk/timer
- **Better simulation models** — improve Monte Carlo accuracy with learned player models
- **More worker types** — extend AsyncAIFlow with new AI capabilities
- **Bedrock Edition support** — port the plugin to Bedrock/GeyserMC
- **i18n** — translate the panel and in-game text to more languages

### Development Setup

```bash
# Backend
cd drift-system_4.8/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Plugin
cd drift-system_4.8/plugin
mvn package

# AsyncAIFlow
cd AsyncAIFlow_4.8
docker compose up -d redis
mvn spring-boot:run -Dspring-boot.run.profiles=local

# RL Agent
cd drift-rl-agent
npm install && pip install -r requirements.txt
python tests/smoke_test.py
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](drift-system_4.8/LICENSE) file for details.

---

<div align="center">

**If this project inspires you, consider giving it a star!**

*Built with FastAPI, Spring Boot, Mineflayer, and a lot of Monte Carlo simulations.*

</div>
