package com.driftmc.scene;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.logging.Level;

import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Particle;
import org.bukkit.boss.BarColor;
import org.bukkit.boss.BarStyle;
import org.bukkit.boss.BossBar;
import org.bukkit.entity.Entity;
import org.bukkit.entity.LivingEntity;
import org.bukkit.entity.Player;
import org.bukkit.metadata.FixedMetadataValue;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitRunnable;

import com.driftmc.cinematic.CinematicController;
import com.driftmc.npc.NPCManager;
import com.driftmc.story.LevelIds;
import com.driftmc.tutorial.TutorialManager;
import com.driftmc.tutorial.TutorialState;
import com.driftmc.tutorial.TutorialStateMachine;
import com.driftmc.world.WorldPatchExecutor;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.serializer.plain.PlainTextComponentSerializer;

/**
 * Wrapper over {@link WorldPatchExecutor} that inspects scene metadata before
 * executing patches.
 */
public final class SceneAwareWorldPatchExecutor extends WorldPatchExecutor {

    private final SceneLoader sceneLoader;
    private final NPCManager npcManager;
    private Map<String, Object> currentRawOperations;
    private TutorialStateMachine tutorialStateMachine;
    private TutorialManager tutorialManager;

    // ── DifficultyAmplifier: BossBar + trigger zone particle state ──
    private final Map<UUID, BossBar> difficultyBossBars = new HashMap<>();
    private final Map<UUID, BukkitRunnable> triggerZoneParticleTasks = new HashMap<>();

    private static final String TUTORIAL_LEVEL_ID = "flagship_tutorial";
    private static final String PRIMARY_LEVEL_ID = "flagship_03";

    public SceneAwareWorldPatchExecutor(JavaPlugin plugin, NPCManager npcManager) {
        super(plugin);
        this.npcManager = npcManager;
        this.sceneLoader = new SceneLoader(plugin, this, npcManager);
    }

    @Override
    public void execute(Player player, Map<String, Object> patch) {
        if (player != null && patch != null && !patch.isEmpty()) {
            inspectObject(player, patch);
            Object mcObj = patch.get("mc");
            if (mcObj instanceof Map) {
                inspectObject(player, (Map<?, ?>) mcObj);
            } else if (mcObj instanceof List) {
                List<?> list = (List<?>) mcObj;
                for (Object entry : list) {
                    if (entry instanceof Map) {
                        inspectObject(player, (Map<?, ?>) entry);
                    }
                }
            }
        }
        super.execute(player, patch);
    }

    public void attachTutorialStateMachine(TutorialStateMachine stateMachine) {
        this.tutorialStateMachine = stateMachine;
    }

    public void attachTutorialManager(TutorialManager manager) {
        this.tutorialManager = manager;
    }

    @Override
    public void shutdown() {
        super.shutdown();
        sceneLoader.shutdown();
        // Clean up all difficulty BossBars and particle tasks
        for (BossBar bar : difficultyBossBars.values()) {
            bar.removeAll();
        }
        difficultyBossBars.clear();
        for (BukkitRunnable task : triggerZoneParticleTasks.values()) {
            try {
                task.cancel();
            } catch (IllegalStateException ignored) {
                // already cancelled
            }
        }
        triggerZoneParticleTasks.clear();
    }

    @Override
    public void ensureFeaturedNpc(Player player, Map<String, Object> metadata, Map<String, Object> operationsView) {
        if (currentRawOperations == null || metadata == null || operationsView == null || operationsView.isEmpty()) {
            return;
        }
        maybeInjectFeaturedNpc(metadata, operationsView, currentRawOperations);
    }

    private void maybeInjectFeaturedNpc(Map<String, Object> metadata,
            Map<String, Object> operationsView,
            Map<String, Object> rawOperations) {
        String featuredNpc = cleanString(metadata.get("featured_npc"));
        if (featuredNpc.isEmpty()) {
            return;
        }

        List<Map<String, Object>> skinDefinitions = extractNpcSkinDefinitions(metadata);
        if (skinDefinitions.isEmpty()) {
            skinDefinitions = extractNpcSkinDefinitions(operationsView);
        }
        if (skinDefinitions.isEmpty()) {
            return;
        }

        Map<String, Object> skinDefinition = findSkinDefinition(skinDefinitions, featuredNpc);
        if (skinDefinition == null) {
            return;
        }

        if (hasExistingSpawn(rawOperations, featuredNpc)) {
            ensureNpcTriggerEvents(metadata, operationsView, rawOperations);
            return;
        }

        Map<String, Object> spawnDirective = buildSpawnDirective(metadata, skinDefinition, featuredNpc);
        appendSpawn(rawOperations, spawnDirective);
        appendSpawn(operationsView, new LinkedHashMap<>(spawnDirective));
        ensureNpcTriggerEvents(metadata, operationsView, rawOperations);
    }

    private List<Map<String, Object>> extractNpcSkinDefinitions(Map<String, Object> source) {
        if (source == null || source.isEmpty()) {
            return Collections.emptyList();
        }
        Object raw = source.get("npc_skins");
        if (!(raw instanceof List<?> list) || list.isEmpty()) {
            return Collections.emptyList();
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object entry : list) {
            if (entry instanceof Map<?, ?> map) {
                result.add(filterStringKeys(map));
            }
        }
        return result;
    }

    private Map<String, Object> findSkinDefinition(List<Map<String, Object>> skins, String npcName) {
        if (skins == null) {
            return null;
        }
        String target = normalize(npcName);
        for (Map<String, Object> skin : skins) {
            String id = cleanString(skin.get("id"));
            if (!id.isEmpty() && normalize(id).equals(target)) {
                return skin;
            }
            String name = cleanString(skin.get("name"));
            if (!name.isEmpty() && normalize(name).equals(target)) {
                return skin;
            }
        }
        return null;
    }

    private Map<String, Object> buildSpawnDirective(Map<String, Object> metadata,
            Map<String, Object> skinDefinition,
            String npcName) {
        Map<String, Object> spawn = new LinkedHashMap<>();
        String type = cleanString(skinDefinition.get("type"));
        if (type.isEmpty()) {
            type = "player_like";
        }
        spawn.put("type", type);
        spawn.put("name", npcName);
        String npcId = cleanString(skinDefinition.get("id"));
        if (!npcId.isEmpty()) {
            spawn.put("id", npcId);
        }
        spawn.put("_auto_featured", Boolean.TRUE);

        Map<String, Object> offset = resolveOffset(metadata, skinDefinition);
        spawn.put("offset", offset);
        return spawn;
    }

    private Map<String, Object> resolveOffset(Map<String, Object> metadata, Map<String, Object> skinDefinition) {
        Map<String, Object> offset = new LinkedHashMap<>();

        Map<String, Object> providedOffset = castToMap(skinDefinition.get("offset"));
        if (providedOffset != null && !providedOffset.isEmpty()) {
            offset.put("dx", asDoubleOrDefault(providedOffset.get("dx"), 1.5));
            offset.put("dy", asDoubleOrDefault(providedOffset.get("dy"), 0.0));
            offset.put("dz", asDoubleOrDefault(providedOffset.get("dz"), 0.5));
            return offset;
        }

        Double dx = null;
        Double dy = null;
        Double dz = null;

        Map<String, Object> teleport = castToMap(metadata.get("teleport"));
        Double x = asNullableDouble(skinDefinition.get("x"));
        Double y = asNullableDouble(skinDefinition.get("y"));
        Double z = asNullableDouble(skinDefinition.get("z"));
        if (teleport != null && x != null && y != null && z != null) {
            Double tx = asNullableDouble(teleport.get("x"));
            Double ty = asNullableDouble(teleport.get("y"));
            Double tz = asNullableDouble(teleport.get("z"));
            if (tx != null && ty != null && tz != null) {
                dx = x - tx;
                dy = y - ty;
                dz = z - tz;
            }
        }

        if (dx == null || dy == null || dz == null) {
            if (dx == null) {
                dx = asNullableDouble(skinDefinition.get("dx"));
            }
            if (dy == null) {
                dy = asNullableDouble(skinDefinition.get("dy"));
            }
            if (dz == null) {
                dz = asNullableDouble(skinDefinition.get("dz"));
            }
        }

        if (dx == null) {
            dx = x;
        }
        if (dy == null) {
            dy = y;
        }
        if (dz == null) {
            dz = z;
        }

        if (dx == null) {
            dx = 1.5;
        }
        if (dy == null) {
            dy = 0.0;
        }
        if (dz == null) {
            dz = 0.5;
        }

        offset.put("dx", dx);
        offset.put("dy", dy);
        offset.put("dz", dz);
        return offset;
    }

    private boolean hasExistingSpawn(Map<String, Object> operations, String npcName) {
        if (operations == null || npcName == null) {
            return false;
        }
        String target = normalize(npcName);
        return containsNpcWithName(operations.get("spawn"), target)
                || containsNpcWithName(operations.get("spawn_multi"), target);
    }

    private boolean containsNpcWithName(Object candidate, String normalizedTarget) {
        if (candidate == null) {
            return false;
        }
        if (candidate instanceof Map<?, ?> map) {
            Map<String, Object> cast = filterStringKeys(map);
            if (Boolean.TRUE.equals(cast.get("_auto_featured"))) {
                return true;
            }
            String name = cleanString(cast.get("name"));
            if (!name.isEmpty() && normalize(name).equals(normalizedTarget)) {
                return true;
            }
            String id = cleanString(cast.get("id"));
            if (!id.isEmpty() && normalize(id).equals(normalizedTarget)) {
                return true;
            }
            return false;
        }
        if (candidate instanceof List<?> list) {
            for (Object element : list) {
                if (containsNpcWithName(element, normalizedTarget)) {
                    return true;
                }
            }
        }
        return false;
    }

    private void appendSpawn(Map<String, Object> target, Map<String, Object> spawnDirective) {
        if (target == null) {
            return;
        }
        if (appendToSlot(target, "spawn_multi", spawnDirective)) {
            return;
        }
        if (appendToSlot(target, "spawn", spawnDirective)) {
            return;
        }
        target.put("spawn", spawnDirective);
    }

    private boolean appendToSlot(Map<String, Object> target, String key, Map<String, Object> spawnDirective) {
        Object existing = target.get(key);
        if (existing == null) {
            return false;
        }
        List<Object> list;
        if (existing instanceof List<?> existingList) {
            list = new ArrayList<>(existingList);
        } else if (existing instanceof Map<?, ?> existingMap) {
            list = new ArrayList<>();
            list.add(existingMap);
        } else {
            list = new ArrayList<>();
            list.add(existing);
        }
        list.add(spawnDirective);
        target.put(key, list);
        return true;
    }

    private void ensureNpcTriggerEvents(Map<String, Object> metadata,
            Map<String, Object> operationsView,
            Map<String, Object> rawOperations) {
        if (rawOperations.containsKey("npc_trigger_events")) {
            return;
        }
        List<Map<String, Object>> triggers = extractTriggerDefinitions(metadata);
        if (triggers.isEmpty()) {
            return;
        }
        rawOperations.put("npc_trigger_events", copyTriggerList(triggers));
        operationsView.put("npc_trigger_events", triggers);
    }

    private List<Map<String, Object>> extractTriggerDefinitions(Map<String, Object> source) {
        if (source == null || source.isEmpty()) {
            return Collections.emptyList();
        }
        Object raw = source.get("npc_triggers");
        if (!(raw instanceof List<?> list) || list.isEmpty()) {
            return Collections.emptyList();
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object entry : list) {
            if (entry instanceof Map<?, ?> map) {
                result.add(filterStringKeys(map));
            }
        }
        return result;
    }

    private List<Map<String, Object>> copyTriggerList(List<Map<String, Object>> source) {
        List<Map<String, Object>> copy = new ArrayList<>(source.size());
        for (Map<String, Object> entry : source) {
            copy.add(new LinkedHashMap<>(entry));
        }
        return copy;
    }

    private Map<String, Object> castToMap(Object value) {
        if (!(value instanceof Map<?, ?> map)) {
            return null;
        }
        return filterStringKeys(map);
    }

    private Double asNullableDouble(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        if (value instanceof String str) {
            try {
                String trimmed = str.trim();
                if (!trimmed.isEmpty()) {
                    return Double.parseDouble(trimmed);
                }
            } catch (NumberFormatException ignored) {
                // ignore malformed numeric strings
            }
        }
        return null;
    }

    private double asDoubleOrDefault(Object value, double fallback) {
        Double converted = asNullableDouble(value);
        return converted != null ? converted : fallback;
    }

    private String cleanString(Object value) {
        return value == null ? "" : value.toString().trim();
    }

    private String normalize(String value) {
        return cleanString(value).toLowerCase(Locale.ROOT);
    }

    @SuppressWarnings("unchecked")
    private void inspectObject(Player player, Object candidate) {
        if (!(candidate instanceof Map<?, ?> rawCandidate)) {
            return;
        }

        Map<String, Object> operations = filterStringKeys(rawCandidate);
        if (operations.isEmpty()) {
            return;
        }

        Map<String, Object> rawOperations = (Map<String, Object>) rawCandidate;

        boolean sceneHandled = false;
        boolean tutorialExited = hasExitedTutorial(player);

        Object cleanup = operations.get("_scene_cleanup");
        if (cleanup instanceof Map<?, ?> cleanupMap) {
            Map<String, Object> cleanupMetadata = filterStringKeys(cleanupMap);
            if (tutorialExited && isTutorialScene(cleanupMetadata)) {
                JavaPlugin plugin = getPlugin();
                if (plugin != null) {
                    String playerName = player != null ? player.getName() : "<unknown>";
                    plugin.getLogger().log(Level.FINE,
                            "[SceneGate] ignore tutorial cleanup for completed player={0}",
                            playerName);
                }
            } else {
                sceneLoader.handleSceneCleanup(player, cleanupMetadata);
            }
        }

        Object scene = operations.get("_scene");
        Map<String, Object> sceneMetadata = null;
        if (scene instanceof Map<?, ?> sceneMap) {
            sceneMetadata = filterStringKeys(sceneMap);
        }

        if (sceneMetadata != null) {
            String levelId = cleanString(sceneMetadata.get("level_id"));
            if (tutorialExited && LevelIds.isFlagshipTutorial(levelId)) {
                sceneMetadata.put("level_id", PRIMARY_LEVEL_ID);
                sceneMetadata.put("scene_id", PRIMARY_LEVEL_ID);
                levelId = PRIMARY_LEVEL_ID;
            }

            if (tutorialExited && isTutorialScene(sceneMetadata)) {
                JavaPlugin plugin = getPlugin();
                if (plugin != null) {
                    String playerName = player != null ? player.getName() : "<unknown>";
                    plugin.getLogger().log(Level.INFO,
                            "[SceneGate] suppress tutorial scene after completion for player={0}",
                            playerName);
                }
                sceneHandled = true;
            } else {
                String previousLevelId = sceneLoader.getActiveSceneId(player);
                boolean sameScene = !levelId.isEmpty() && previousLevelId != null
                        && previousLevelId.equalsIgnoreCase(levelId);
                boolean shouldApplyScene = levelId.isEmpty() || !sceneLoader.isPlayerInScene(player, levelId);
                if (shouldApplyScene) {
                    JavaPlugin plugin = getPlugin();
                    if (plugin != null) {
                        String playerName = player != null ? player.getName() : "<unknown>";
                        String fromId = previousLevelId == null || previousLevelId.isBlank() ? "<none>"
                                : previousLevelId;
                        String toId = levelId.isEmpty() ? "<unknown>" : levelId;
                        plugin.getLogger().log(Level.INFO,
                                "[SceneGate] transition scene; from={0} to={1} player={2}",
                                new Object[] { fromId, toId, playerName });
                    }

                    Map<String, Object> previousRawOps = currentRawOperations;
                    currentRawOperations = rawOperations;
                    try {
                        sceneLoader.handleScenePatch(player, sceneMetadata, operations);
                    } finally {
                        currentRawOperations = previousRawOps;
                    }
                    sceneHandled = true;
                } else if (sameScene) {
                    JavaPlugin plugin = getPlugin();
                    if (plugin != null) {
                        String playerName = player != null ? player.getName() : "<unknown>";
                        plugin.getLogger().log(Level.INFO,
                                "[SceneGate] skip scene patch; same level_id={0} player={1}",
                                new Object[] { previousLevelId, playerName });
                    }
                }
            }
        }

        Object cinematic = operations.get("_cinematic");
        if (cinematic instanceof Map<?, ?> cinematicMap) {
            if (!sceneHandled) {
                sceneLoader.handleCinematic(player, filterStringKeys(cinematicMap));
            }
        }

        Object emotion = operations.get("npc_emotion");
        if (emotion instanceof Map<?, ?> emotionMap) {
            npcManager.applyEmotionPatch(player, filterStringKeys(emotionMap));
        } else if (emotion instanceof List<?> emotionList) {
            for (Object entry : emotionList) {
                if (entry instanceof Map<?, ?> entryMap) {
                    npcManager.applyEmotionPatch(player, filterStringKeys(entryMap));
                }
            }
        }

        // ── DifficultyAmplifier: BossBar handling ──
        Object bossbarObj = operations.get("bossbar");
        if (bossbarObj instanceof Map<?, ?> bossbarMap) {
            handleDifficultyBossBar(player, filterStringKeys(bossbarMap));
        }

        // ── DifficultyAmplifier: trigger zone particle borders ──
        Object triggerZonesObj = operations.get("trigger_zones");
        if (triggerZonesObj instanceof List<?> tzList) {
            handleTriggerZoneParticles(player, tzList, operations);
        }
    }

    private Map<String, Object> filterStringKeys(Map<?, ?> source) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : source.entrySet()) {
            Object key = entry.getKey();
            if (key instanceof String keyStr) {
                result.put(keyStr, entry.getValue());
            }
        }
        return result;
    }

    public void attachCinematicController(CinematicController controller) {
        this.sceneLoader.setCinematicController(controller);
    }

    public SceneLoader getSceneLoader() {
        return sceneLoader;
    }

    private boolean hasExitedTutorial(Player player) {
        if (player == null) {
            return false;
        }
        if (tutorialManager != null) {
            return tutorialManager.hasExitedTutorial(player);
        }
        if (tutorialStateMachine == null) {
            return false;
        }
        return tutorialStateMachine.getState(player) == TutorialState.COMPLETE;
    }

    private boolean isTutorialScene(Map<String, Object> metadata) {
        if (metadata == null || metadata.isEmpty()) {
            return false;
        }
        String levelId = cleanString(metadata.get("level_id"));
        if (levelId.isEmpty()) {
            levelId = cleanString(metadata.get("scene_id"));
        }
        return !levelId.isEmpty() && levelId.equalsIgnoreCase(TUTORIAL_LEVEL_ID);
    }

    // ── DifficultyAmplifier: BossBar management ──────────────────────────────

    /**
     * Create or update a BossBar based on the difficulty amplifier's bossbar config.
     * Config keys: title, color, style, progress, track_event, max_count.
     */
    private void handleDifficultyBossBar(Player player, Map<String, Object> config) {
        if (player == null || config == null || config.isEmpty()) {
            return;
        }

        UUID uuid = player.getUniqueId();
        String title = cleanString(config.get("title"));
        if (title.isEmpty()) {
            title = "§e关卡进度";
        }

        BarColor color = parseBossBarColor(cleanString(config.get("color")));
        BarStyle style = parseBossBarStyle(cleanString(config.get("style")));
        double progress = asDoubleOrDefault(config.get("progress"), 0.0);
        progress = Math.max(0.0, Math.min(1.0, progress));

        // Remove old bar if present
        BossBar oldBar = difficultyBossBars.remove(uuid);
        if (oldBar != null) {
            oldBar.removePlayer(player);
        }

        BossBar bar = Bukkit.createBossBar(title, color, style);
        bar.setProgress(progress);
        bar.addPlayer(player);
        bar.setVisible(true);
        difficultyBossBars.put(uuid, bar);

        JavaPlugin plugin = getPlugin();
        if (plugin != null) {
            plugin.getLogger().log(Level.FINE,
                    "[DifficultyAmplifier] BossBar created for player={0} title={1}",
                    new Object[] { player.getName(), title });
        }
    }

    private BarColor parseBossBarColor(String colorName) {
        if (colorName.isEmpty()) {
            return BarColor.YELLOW;
        }
        try {
            return BarColor.valueOf(colorName.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return BarColor.YELLOW;
        }
    }

    private BarStyle parseBossBarStyle(String styleName) {
        if (styleName.isEmpty()) {
            return BarStyle.SOLID;
        }
        try {
            return BarStyle.valueOf(styleName.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return BarStyle.SOLID;
        }
    }

    /**
     * Update an existing BossBar's progress (called when beat advances).
     */
    public void updateDifficultyBossBar(Player player, String title, double progress) {
        if (player == null) {
            return;
        }
        UUID uuid = player.getUniqueId();
        BossBar bar = difficultyBossBars.get(uuid);
        if (bar == null) {
            return;
        }
        if (title != null && !title.isEmpty()) {
            bar.setTitle(title);
        }
        bar.setProgress(Math.max(0.0, Math.min(1.0, progress)));
    }

    /**
     * Remove a player's difficulty BossBar (called on level exit).
     */
    public void removeDifficultyBossBar(Player player) {
        if (player == null) {
            return;
        }
        BossBar bar = difficultyBossBars.remove(player.getUniqueId());
        if (bar != null) {
            bar.removePlayer(player);
        }
    }

    // ── DifficultyAmplifier: trigger zone particle visualization ─────────────

    /**
     * Spawn particle borders around trigger zones that have particle_border config.
     * This creates a repeating task that renders particle outlines.
     */
    private void handleTriggerZoneParticles(Player player, List<?> triggerZones,
            Map<String, Object> operations) {
        if (player == null || triggerZones == null || triggerZones.isEmpty()) {
            return;
        }

        JavaPlugin plugin = getPlugin();
        if (plugin == null) {
            return;
        }

        // Collect trigger zones that have particle_border config
        List<Map<String, Object>> particleZones = new ArrayList<>();
        for (Object tzObj : triggerZones) {
            if (!(tzObj instanceof Map<?, ?> tzMap)) {
                continue;
            }
            Map<String, Object> tz = filterStringKeys(tzMap);
            Object borderObj = tz.get("particle_border");
            if (borderObj instanceof Map<?, ?> borderMap) {
                Map<String, Object> combined = new LinkedHashMap<>(tz);
                combined.put("_border_config", filterStringKeys(borderMap));
                particleZones.add(combined);
            }
        }

        if (particleZones.isEmpty()) {
            return;
        }

        // Cancel any existing particle task for this player
        UUID uuid = player.getUniqueId();
        BukkitRunnable oldTask = triggerZoneParticleTasks.remove(uuid);
        if (oldTask != null) {
            try {
                oldTask.cancel();
            } catch (IllegalStateException ignored) {
                // already cancelled
            }
        }

        // Determine base position from teleport or player location
        double baseX = player.getLocation().getX();
        double baseY = player.getLocation().getY();
        double baseZ = player.getLocation().getZ();

        Map<String, Object> teleport = null;
        Object tpObj = operations.get("teleport");
        if (tpObj instanceof Map<?, ?> tpMap) {
            teleport = filterStringKeys(tpMap);
            Double tx = asNullableDouble(teleport.get("x"));
            Double ty = asNullableDouble(teleport.get("y"));
            Double tz = asNullableDouble(teleport.get("z"));
            if (tx != null) baseX = tx;
            if (ty != null) baseY = ty;
            if (tz != null) baseZ = tz;
        }

        final double fx = baseX;
        final double fy = baseY;
        final double fz = baseZ;

        // Create repeating task to render particles
        BukkitRunnable task = new BukkitRunnable() {
            private int ticks = 0;

            @Override
            public void run() {
                if (!player.isOnline()) {
                    this.cancel();
                    triggerZoneParticleTasks.remove(uuid);
                    return;
                }
                // Run for 60 seconds max, then stop
                ticks++;
                if (ticks > 1200) {
                    this.cancel();
                    triggerZoneParticleTasks.remove(uuid);
                    return;
                }

                for (Map<String, Object> zone : particleZones) {
                    renderTriggerZoneParticles(player, zone, fx, fy, fz);
                }
            }
        };

        task.runTaskTimer(plugin, 10L, 10L); // every 0.5 seconds
        triggerZoneParticleTasks.put(uuid, task);

        plugin.getLogger().log(Level.FINE,
                "[DifficultyAmplifier] trigger zone particles started for player={0} zones={1}",
                new Object[] { player.getName(), particleZones.size() });
    }

    /**
     * Render particle borders for a single trigger zone.
     */
    private void renderTriggerZoneParticles(Player player, Map<String, Object> zone,
            double baseX, double baseY, double baseZ) {
        @SuppressWarnings("unchecked")
        Map<String, Object> borderConfig = (Map<String, Object>) zone.get("_border_config");
        if (borderConfig == null) {
            return;
        }

        String particleType = cleanString(borderConfig.get("type"));
        int count = (int) asDoubleOrDefault(borderConfig.get("count"), 20);
        int height = (int) asDoubleOrDefault(borderConfig.get("height"), 3);

        Particle particle;
        try {
            particle = Particle.valueOf(particleType.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            particle = Particle.END_ROD;
        }

        // Get trigger zone position (offset from base)
        double dx = asDoubleOrDefault(zone.get("dx"), asDoubleOrDefault(zone.get("x"), 0));
        double dy = asDoubleOrDefault(zone.get("dy"), asDoubleOrDefault(zone.get("y"), 0));
        double dz = asDoubleOrDefault(zone.get("dz"), asDoubleOrDefault(zone.get("z"), 0));
        double radius = asDoubleOrDefault(zone.get("radius"), 3.0);

        double cx = baseX + dx;
        double cy = baseY + dy;
        double cz = baseZ + dz;

        // Render a circle of particles at each height level
        int pointsPerCircle = Math.max(8, count / Math.max(1, height));
        for (int h = 0; h < height; h++) {
            for (int i = 0; i < pointsPerCircle; i++) {
                double angle = 2 * Math.PI * i / pointsPerCircle;
                double px = cx + radius * Math.cos(angle);
                double pz = cz + radius * Math.sin(angle);
                double py = cy + h;

                Location loc = new Location(player.getWorld(), px, py, pz);
                player.spawnParticle(particle, loc, 1, 0, 0, 0, 0);
            }
        }

        // Beacon effect (vertical particle column)
        Object beaconObj = zone.get("beacon");
        if (beaconObj instanceof Map<?, ?> beaconMap) {
            Map<String, Object> beaconConfig = filterStringKeys(beaconMap);
            String beaconType = cleanString(beaconConfig.get("type"));
            int beaconCount = (int) asDoubleOrDefault(beaconConfig.get("count"), 10);
            int beaconHeight = (int) asDoubleOrDefault(beaconConfig.get("height"), 15);

            Particle beaconParticle;
            try {
                beaconParticle = Particle.valueOf(beaconType.toUpperCase(Locale.ROOT));
            } catch (IllegalArgumentException e) {
                beaconParticle = Particle.FLAME;
            }

            for (int h = 0; h < beaconHeight; h++) {
                Location loc = new Location(player.getWorld(), cx, cy + h, cz);
                player.spawnParticle(beaconParticle, loc, beaconCount / beaconHeight + 1, 0.1, 0, 0.1, 0);
            }
        }
    }

    /**
     * Stop trigger zone particle rendering for a player (called on level exit).
     */
    public void stopTriggerZoneParticles(Player player) {
        if (player == null) {
            return;
        }
        BukkitRunnable task = triggerZoneParticleTasks.remove(player.getUniqueId());
        if (task != null) {
            try {
                task.cancel();
            } catch (IllegalStateException ignored) {
                // already cancelled
            }
        }
    }

    /**
     * Clean up all difficulty-related state for a player.
     */
    public void cleanupDifficultyState(Player player) {
        removeDifficultyBossBar(player);
        stopTriggerZoneParticles(player);
    }

    @Override
    protected void afterSpawn(Player player, Map<String, Object> spawnSpec, Entity entity) {
        super.afterSpawn(player, spawnSpec, entity);
        if (spawnSpec == null || entity == null) {
            return;
        }
        Object npcIdRaw = spawnSpec.get("npc_id");
        Object npcNameRaw = spawnSpec.get("name");
        if (npcIdRaw == null && npcNameRaw == null) {
            return;
        }

        if (!(entity instanceof LivingEntity living)) {
            return;
        }

        String npcId = cleanString(npcIdRaw);
        String declaredName = cleanString(npcNameRaw);

        if (declaredName.isEmpty()) {
            Component customName = living.customName();
            if (customName != null) {
                declaredName = cleanString(PlainTextComponentSerializer.plainText().serialize(customName));
            }
        }

        if (declaredName.isEmpty() && !npcId.isEmpty()) {
            declaredName = npcId;
        }

        living.addScoreboardTag("drift:npc");
        if (!npcId.isEmpty()) {
            living.addScoreboardTag("npc_id:" + npcId);
            living.setMetadata("npc_id", new FixedMetadataValue(getPlugin(), npcId));
        }

        npcManager.registerAutoFeaturedNpc(living, declaredName);
    }
}
