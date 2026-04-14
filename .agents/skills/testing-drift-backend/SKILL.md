# Testing Drift Backend API

## Overview
The Drift backend runs FastAPI at `http://35.201.132.58:8000` with story/experience endpoints under `/story/*`.

## Devin Secrets Needed
None — the backend API is unauthenticated.

## Key Endpoints for Testing
- `GET /story/levels` — List all available levels
- `GET /story/level/{level_id}` — Get level details (check difficulty in meta)
- `POST /story/load/{player_id}/{level_id}` — Load a level for a player
- `POST /story/inject` — Create a new level (see payload below)
- `POST /story/auto-advance/{player_id}` — Test auto-progression
- `GET /story/state/{player_id}` — Check player state

## Creating Test Levels with Specific Difficulty
Use `/story/inject` with a unique `level_id` (include timestamp to avoid conflicts):
```bash
curl -s -X POST http://35.201.132.58:8000/story/inject \
  -H "Content-Type: application/json" \
  -d '{
    "level_id": "test_d4_'$(date +%s)'",
    "title": "D4 Test Level",
    "text": "收集宝石通关",
    "player_id": "test_player",
    "difficulty": 4
  }'
```
**Required fields**: `level_id`, `title`, `text`
**Optional**: `player_id`, `difficulty` (1-5), `anchor`, `scene_theme`

## Testing Auto-Advance Progression
1. Inject a level with specific `difficulty` (1-5)
2. Load it for a test player via `/story/load/{player_id}/{level_id}`
3. Call `/story/auto-advance/{player_id}`
4. Verify response `action` matches expected strategy:
   - D1-D3: `action=auto`, `bootstrap_patch` present
   - D4: `action=prompt`, `bootstrap_patch=null`, message has `/advance` and `/replay`
   - D5: `action=warn`, `bootstrap_patch=null`, message has `/advance` and `/easy`
   - End: `action=end`, `next_level_id=null`

## Known Limitations
- **`action=end` hard to trigger**: `get_next_level_id()` falls back to the start level when the current level isn't in the story graph. Injected test levels are isolated from the graph, so they always get a fallback `next_level_id`. The "end" path only activates for the actual last level in a real story chain.
- **Java plugin untestable without MC**: The `RuleEventBridge.triggerAutoAdvance()` method requires a running Minecraft server with the plugin loaded. Can only be verified by code review.
- **Level IDs are normalized**: `_normalize_injected_level_id()` lowercases and strips special characters. Use simple alphanumeric + underscore IDs.
- **Inject is one-time**: If a `level_id` already exists, inject returns 400. Always use unique IDs (timestamps help).

## Other Services
- **AsyncAIFlow**: `http://35.201.132.58:8080` — Health check: `GET /health`
- **Experience Panel**: `http://35.201.132.58:8888/drift-experience-panel.html`
- **MC Server**: `35.201.132.58:25565` (requires MC client with GPU)
