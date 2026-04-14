# Testing Drift MC Plugin API Integration

## Overview
The Drift MC plugin communicates with a Python/FastAPI backend. This skill covers how to verify plugin command implementations via API calls without needing an in-game Minecraft client.

## Backend URL
- Default: `http://35.201.132.58:8000`
- Health check: `GET /story/levels` should return 200

## Devin Secrets Needed
- None for API testing (backend is unauthenticated)
- SSH access to cloud VM needed for deployment (not currently saved)

## Common API Patterns

### URL Pattern for /story/load
The backend endpoint is `POST /story/load/{player_id}/{level_id}` — level_id is a **URL path segment**, NOT a JSON body field.

```bash
# CORRECT
curl -X POST http://35.201.132.58:8000/story/load/PlayerName/level_name -d '{}'

# WRONG (returns 404)
curl -X POST http://35.201.132.58:8000/story/load/PlayerName -d '{"level_id": "level_name"}'
```

All plugin commands should follow this pattern. Reference implementations: `LevelCommand.java`, `DriftLoadCommand.java`, `CreateLevelCommand.java`.

### State Response Structure
The `/story/state/{player_id}` endpoint wraps data in a `state` sub-object:

```json
{
  "status": "ok",
  "state": {
    "player_current_level": "accept_gem_v1",
    "total_levels": 195,
    "levels": [...],
    "players": [...]
  }
}
```

**Key gotcha:** The field is `player_current_level` inside `state`, NOT `current_level` or `level_id` at the root. The `StoryManager.syncState()` method correctly unwraps via `root.getAsJsonObject("state")` — new commands should follow this pattern.

### Load Response Structure  
The `/story/load` endpoint returns `bootstrap_patch`, NOT `world_patch`:

```json
{
  "status": "ok",
  "msg": "accept_gem_v1 loaded",
  "bootstrap_patch": {"variables": {...}, "mc": {...}}
}
```

Commands should check `bootstrap_patch` first, then fall back to `world_patch`. See `CreateLevelCommand.java` and `DriftLoadCommand.java` for the correct pattern.

## Testing Flow (Replay/Easy Commands)

1. **Load a level:** `POST /story/load/{player}/{level_id}` with `{}`
2. **Verify state:** `POST /story/state/{player}` — check `state.player_current_level` is set
3. **Reload (replay):** `POST /story/load/{player}/{level_id}` with `{}` — verify `bootstrap_patch` returned
4. **Verify old pattern fails:** `POST /story/load/{player}` with level_id in body — should 404

## Compilation
```bash
cd drift-system_4.8/plugin && mvn -q package -DskipTests
```

Note: pom.xml requires explicit `maven-compiler-plugin:3.11.0` because the project uses `maven.compiler.release=17` which isn't supported by the default plugin version bundled with Maven 3.6.x.

## Limitations
- Bukkit-specific behavior (BossBar, particles, world patch rendering, scheduler tasks) cannot be tested via API — requires an in-game MC client
- The dev VM does not have a Minecraft client (no GPU for rendering)
- `/easy` command currently behaves identically to `/replay` — backend has no difficulty reduction mechanism
