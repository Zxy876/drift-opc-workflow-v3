package com.driftmc.intent2;

import java.io.IOException;
import java.lang.reflect.Type;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Level;

import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.entity.Player;
import org.bukkit.plugin.Plugin;
import org.bukkit.scheduler.BukkitTask;

import com.driftmc.asyncaiflow.AsyncAIFlowClient;
import com.driftmc.backend.BackendClient;
import com.driftmc.hud.QuestLogHud;
import com.driftmc.hud.dialogue.ChoicePanel;
import com.driftmc.hud.dialogue.DialoguePanel;
import com.driftmc.scene.SceneAwareWorldPatchExecutor;
import com.driftmc.story.LevelIds;
import com.driftmc.tutorial.TutorialManager;
import com.driftmc.tutorial.TutorialState;
import com.driftmc.world.PayloadExecutorV1;
import com.driftmc.world.WorldPatchExecutor;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonSyntaxException;
import com.google.gson.reflect.TypeToken;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.Response;

public class IntentDispatcher2 {

    private final Plugin plugin;
    private final BackendClient backend;
    private final WorldPatchExecutor world;
    private final PayloadExecutorV1 payloadExecutor;
    private TutorialManager tutorialManager;
    private QuestLogHud questLogHud;
    private DialoguePanel dialoguePanel;
    private ChoicePanel choicePanel;
    private final Set<UUID> tutorialReentryWarned = ConcurrentHashMap.newKeySet();

    /** AsyncAIFlow 客户端（高难度 issue 分流） */
    private AsyncAIFlowClient asyncAIFlowClient;
    /** 触发向 AsyncAIFlow 分流的最低难度（默认3） */
    private int asyncAIFlowDifficultyThreshold = 3;
    /** Drift 专案的仓库上下文，透传给 planner */
    private String driftRepoContext = "drift-system";

    private static final Gson GSON = new Gson();
    private static final Type MAP_TYPE = new TypeToken<Map<String, Object>>() {
    }.getType();
    private static final String PRIMARY_LEVEL_ID = "flagship_03";

    public IntentDispatcher2(Plugin plugin, BackendClient backend, WorldPatchExecutor world, PayloadExecutorV1 payloadExecutor) {
        this.plugin = plugin;
        this.backend = backend;
        this.world = world;
        this.payloadExecutor = payloadExecutor;
    }

    public IntentDispatcher2(Plugin plugin, BackendClient backend, SceneAwareWorldPatchExecutor world,
            PayloadExecutorV1 payloadExecutor) {
        this(plugin, backend, (WorldPatchExecutor) world, payloadExecutor);
    }

    /** 注入 AsyncAIFlowClient（可选；未注入时高难度 issue 仍走现有 Drift 路径） */
    public void setAsyncAIFlowClient(AsyncAIFlowClient client) {
        this.asyncAIFlowClient = client;
    }

    public void setAsyncAIFlowDifficultyThreshold(int threshold) {
        this.asyncAIFlowDifficultyThreshold = Math.max(1, Math.min(5, threshold));
    }

    public void setDriftRepoContext(String repoContext) {
        this.driftRepoContext = repoContext;
    }

    public void setTutorialManager(TutorialManager manager) {
        this.tutorialManager = manager;
    }

    public void setQuestLogHud(QuestLogHud questLogHud) {
        this.questLogHud = questLogHud;
    }

    public void setDialoguePanel(DialoguePanel dialoguePanel) {
        this.dialoguePanel = dialoguePanel;
    }

    public void setChoicePanel(ChoicePanel choicePanel) {
        this.choicePanel = choicePanel;
    }

    private boolean ensureUnlocked(Player player, TutorialState required, String message) {
        if (tutorialManager == null || tutorialManager.isTutorialComplete(player)) {
            return true;
        }
        return tutorialManager.ensureUnlocked(player, required, message);
    }

    private void syncTutorialState(Player player, Map<String, Object> patch) {
        if (tutorialManager == null || patch == null || patch.isEmpty()) {
            return;
        }
        tutorialManager.syncWorldPatch(player, patch);
    }

    // ============================================================
    // 主入口
    // ============================================================
    public void dispatch(Player p, IntentResponse2 intent) {

        switch (intent.type) {

            case SHOW_MINIMAP:
                showMinimap(p, intent);
                break;

            case GOTO_LEVEL:
            case GOTO_NEXT_LEVEL:
                gotoLevelAndLoad(p, intent);
                break;

            case SET_DAY:
            case SET_NIGHT:
            case SET_WEATHER:
            case TELEPORT:
            case SPAWN_ENTITY:
            case BUILD_STRUCTURE:
                runWorldCommand(p, intent);
                break;

            case CREATE_STORY:
                // ── Phase 4: Scene-Type driven routing ──────────────────────
                // CONTENT  → 直接走 Drift（World-First 原则，默认路径）
                // RULE     → AsyncAIFlow（生成系统级规则）
                // SIMULATION → AsyncAIFlow（长期状态建模）
                // 未注入 asyncAIFlowClient 时，RULE/SIMULATION 降级为 CONTENT
                routeCreateStoryBySceneType(p, intent);
                break;

            case SAY_ONLY:
            case STORY_CONTINUE:
            case UNKNOWN:
                pushToStoryEngine(p, intent.rawText);
                break;

            default:
                break;
        }
    }

    // ============================================================
    // Phase 4: Scene-Type 驱动路由
    // ============================================================
    /**
     * World-First 原则路由：
     *   CONTENT    → createStory()（Drift 直连，默认）
     *   RULE       → submitToAsyncAIFlow()（仅当 client 可用）
     *   SIMULATION → submitToAsyncAIFlow()（仅当 client 可用）
     *   fallback   → createStory()（AsyncAIFlow 不可用时降级）
     */
    private void routeCreateStoryBySceneType(Player p, IntentResponse2 intent) {
        String sceneType = intent.sceneType != null ? intent.sceneType : "CONTENT";

        plugin.getLogger().info(
                "[routeCreateStory] player=" + p.getName()
                + " sceneType=" + sceneType
                + " difficulty=" + intent.difficulty
                + " asyncClientAvailable=" + (asyncAIFlowClient != null));

        if ("RULE".equals(sceneType) || "SIMULATION".equals(sceneType)) {
            if (asyncAIFlowClient != null) {
                p.sendMessage("§6⚛ [" + sceneType + "] 路由到 AsyncAIFlow 规则引擎...");
                submitToAsyncAIFlow(p, intent);
            } else {
                // AsyncAIFlow 未注入 → 降级为 Drift 直连，记录告警
                plugin.getLogger().warning(
                        "[routeCreateStory] AsyncAIFlowClient not injected, degrading "
                        + sceneType + " → CONTENT for player=" + p.getName());
                p.sendMessage("§e⚠ AsyncAIFlow 未配置，降级为本地 Drift 生成...");
                createStory(p, intent);
            }
        } else if ("CONTENT".equals(sceneType)
                   && intent.difficulty >= asyncAIFlowDifficultyThreshold
                   && asyncAIFlowClient != null) {
            // ── Phase 5: 高难度 CONTENT → drift_experience (Beam Search + Arc) ──
            plugin.getLogger().info(
                    "[routeCreateStory] high-difficulty CONTENT → drift_experience"
                    + " player=" + p.getName()
                    + " difficulty=" + intent.difficulty);
            p.sendMessage("§6⚛ 难度" + intent.difficulty + "★ — 路由到 Drift Experience (Beam Search + 叙事弧)...");
            submitToDriftExperience(p, intent);
        } else {
            // CONTENT 低难度或未知类型 → 直接走 Drift（World-First）
            createStory(p, intent);
        }
    }

    // ============================================================
    // Drift Experience: 高难度 CONTENT → drift_experience 工作流
    // ============================================================
    private void submitToDriftExperience(Player p, IntentResponse2 intent) {
        final Player fp = p;
        final String premise = intent.rawText != null ? intent.rawText.trim() : "";
        if (premise.isEmpty()) {
            createStory(fp, intent);
            return;
        }

        fp.sendMessage("§e⚙ 正在提交给 Drift Experience 工作流（Beam Search + 叙事弧）...");
        plugin.getLogger().info("[submitToDriftExperience] player=" + fp.getName()
                + " difficulty=" + intent.difficulty + " premise=" + premise);

        asyncAIFlowClient.submitDriftExperience(
                premise,
                intent.difficulty,
                fp.getName(),
                workflowId -> {
                    Bukkit.getScheduler().runTask((org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                        fp.sendMessage("§a✅ Drift Experience 工作流已启动！工作流 #" + workflowId);
                        fp.sendMessage("§7系统正在进行 Beam Search 多变体探索 + 叙事弧构建...");
                        fp.sendMessage("§7完成后世界将自动更新。");
                    });
                    plugin.getLogger().info("[submitToDriftExperience] workflowId=" + workflowId
                            + " player=" + fp.getName());
                    startProgressPolling(fp, workflowId);
                },
                errMsg -> {
                    plugin.getLogger().warning("[submitToDriftExperience] failed: " + errMsg
                            + " — fallback to standard AsyncAIFlow");
                    Bukkit.getScheduler().runTask((org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                        fp.sendMessage("§e⚠ Drift Experience 不可达，降级为标准 AsyncAIFlow...");
                        submitToAsyncAIFlow(fp, intent);
                    });
                }
        );
    }

    // ============================================================
    // AsyncAIFlow 高难度分流：提交到专案工作流
    // ============================================================
    private void submitToAsyncAIFlow(Player p, IntentResponse2 intent) {
        final Player fp = p;
        final String issue = intent.rawText != null ? intent.rawText.trim() : "";
        if (issue.isEmpty()) {
            createStory(fp, intent);
            return;
        }

        fp.sendMessage("§e⚙ 难度" + intent.difficulty + "★ — 正在提交给 AI 工作流处理...");
        plugin.getLogger().info("[submitToAsyncAIFlow] player=" + fp.getName()
                + " difficulty=" + intent.difficulty + " issue=" + issue);

        asyncAIFlowClient.submitIssue(
                issue,
                driftRepoContext,
                intent.difficulty,
                fp.getName(),
                workflowId -> {
                    Bukkit.getScheduler().runTask((org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                        fp.sendMessage("§a✅ AI 工作流已启动！工作流 #" + workflowId);
                        fp.sendMessage("§7系统正在分解任务并执行，完成后世界将自动刷新。");
                    });
                    plugin.getLogger().info("[submitToAsyncAIFlow] workflowId=" + workflowId
                            + " player=" + fp.getName());
                    // Start polling Drift progress status to detect drift_refresh completion
                    startProgressPolling(fp, workflowId);
                },
                errMsg -> {
                    plugin.getLogger().warning("[submitToAsyncAIFlow] failed: " + errMsg
                            + " — fallback to Drift direct");
                    Bukkit.getScheduler().runTask((org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                        fp.sendMessage("§e⚠ AI 工作流不可达，已降级本地处理...");
                        createStory(fp, intent);
                    });
                }
        );
    }

    // ============================================================
    // 轮询 Drift progress/status → 检测 drift_refresh 完成 → 应用 world_patch
    // ============================================================
    private static final int POLL_MAX_ATTEMPTS = 30;   // 30 × 10s = 5 min max
    private static final long POLL_PERIOD_TICKS = 200L; // 10 seconds in ticks (20 ticks/s)

    private JsonArray parseProgressEntries(String body) {
        com.google.gson.JsonElement root = JsonParser.parseString(body);
        if (root.isJsonArray()) {
            return root.getAsJsonArray();
        }
        if (!root.isJsonObject()) {
            return null;
        }
        JsonObject obj = root.getAsJsonObject();
        if (obj.has("entries") && obj.get("entries").isJsonArray()) {
            return obj.getAsJsonArray("entries");
        }
        if (obj.has("data") && obj.get("data").isJsonArray()) {
            return obj.getAsJsonArray("data");
        }
        return null;
    }

    private void startProgressPolling(Player player, long workflowId) {
        final String playerName = player.getName();
        final AtomicInteger attempts = new AtomicInteger(0);
        final BukkitTask[] taskHolder = new BukkitTask[1];

        Runnable poller = () -> {
            int attempt = attempts.incrementAndGet();
            if (attempt > POLL_MAX_ATTEMPTS) {
                plugin.getLogger().warning("[progressPoll] timeout for player=" + playerName
                        + " workflowId=" + workflowId);
                if (taskHolder[0] != null) taskHolder[0].cancel();
                Bukkit.getScheduler().runTask((org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                    Player p = Bukkit.getPlayerExact(playerName);
                    if (p != null) {
                        p.sendMessage("§eAI 工作流已完成（5分钟超时），请通过 /spawn 刷新世界。");
                    }
                });
                return;
            }

            backend.getAsync("/story/progress/status/" + playerName, new okhttp3.Callback() {
                @Override
                public void onFailure(okhttp3.Call call, IOException e) {
                    plugin.getLogger().warning("[progressPoll] HTTP error attempt=" + attempt
                            + " player=" + playerName + " err=" + e.getMessage());
                }

                @Override
                public void onResponse(okhttp3.Call call, okhttp3.Response response) throws IOException {
                    try {
                        if (!response.isSuccessful()) return;
                        String body = response.body() != null ? response.body().string() : "";
                        if (body.isBlank()) return;

                    com.google.gson.JsonArray entries = parseProgressEntries(body);
                        if (entries == null) return;

                        for (com.google.gson.JsonElement el : entries) {
                            if (!el.isJsonObject()) continue;
                            com.google.gson.JsonObject entry = el.getAsJsonObject();
                        long entryWorkflowId = entry.has("workflow_id")
                            ? entry.get("workflow_id").getAsLong()
                            : -1L;
                            String stage = entry.has("stage") ? entry.get("stage").getAsString() : "";
                            String status = entry.has("status") ? entry.get("status").getAsString() : "";
                        if (entryWorkflowId != workflowId) continue;
                        if (!"drift_refresh".equals(stage) || !"SUCCEEDED".equals(status)) continue;

                            // Found! Cancel poller and apply world_patch
                            if (taskHolder[0] != null) taskHolder[0].cancel();

                            com.google.gson.JsonObject wp = entry.has("world_patch")
                                    && entry.get("world_patch").isJsonObject()
                                    ? entry.getAsJsonObject("world_patch")
                                    : null;

                            plugin.getLogger().info("[progressPoll] drift_refresh SUCCEEDED"
                                    + " player=" + playerName + " workflowId=" + workflowId
                                    + " hasWorldPatch=" + (wp != null));

                            Bukkit.getScheduler().runTask(
                                    (org.bukkit.plugin.java.JavaPlugin) plugin, () -> {
                                Player p = Bukkit.getPlayerExact(playerName);
                                if (p == null) return;
                                p.sendMessage("§a✨ AI 工作流执行完毕！世界已根据你的想法更新。");
                                if (wp != null && !wp.entrySet().isEmpty()) {
                                    try {
                                        // Convert JsonObject → Map<String, Object> for WorldPatchExecutor
                                        java.lang.reflect.Type mapType =
                                                new com.google.gson.reflect.TypeToken<
                                                        java.util.Map<String, Object>>(){}.getType();
                                        java.util.Map<String, Object> patchMap =
                                                new Gson().fromJson(wp, mapType);
                                        world.execute(p, patchMap);
                                    } catch (Exception ex) {
                                        plugin.getLogger().warning(
                                                "[progressPoll] world.execute failed: " + ex.getMessage());
                                    }
                                }
                            });
                            return;
                        }
                    } catch (Exception ex) {
                        plugin.getLogger().warning("[progressPoll] parse error: " + ex.getMessage());
                    }
                }
            });
        };

        // Schedule repeating async task; store reference so poller can cancel itself
        taskHolder[0] = Bukkit.getScheduler().runTaskTimerAsynchronously(
                (org.bukkit.plugin.java.JavaPlugin) plugin,
                poller,
                POLL_PERIOD_TICKS,   // initial delay
                POLL_PERIOD_TICKS    // period
        );
        plugin.getLogger().info("[progressPoll] started for player=" + playerName
                + " workflowId=" + workflowId);
    }

    // ============================================================
    // 创建剧情 (CREATE_STORY) — 现有直连 Drift 路径
    // ============================================================
    private void createStory(Player p, IntentResponse2 intent) {
        final Player fp = p;
        String rawTextForLog = intent.rawText != null ? intent.rawText : "<null>";

        boolean createStoryUnlocked = ensureUnlocked(fp, TutorialState.CREATE_STORY, "请继续当前教学提示后再创造剧情。");
        plugin.getLogger().log(Level.INFO,
            "[DEBUG] CREATE_STORY gate={0} player={1} rawText={2}",
            new Object[]{createStoryUnlocked ? "UNLOCKED" : "LOCKED", fp.getName(), rawTextForLog});

        if (!createStoryUnlocked) {
            return;
        }

        // 从 rawText 中提取标题、正文和 scene_theme
        String rawText = intent.rawText != null ? intent.rawText.trim() : "";
        if (rawText.isEmpty()) {
            rawText = "新剧情";
        }

        SceneSpec sceneSpec = extractSceneSpecFromRawText(rawText);

        String sceneTheme = normalizeSceneTheme(intent.sceneTheme);
        if (sceneTheme == null) {
            sceneTheme = sceneSpec.sceneTheme;
        }

        String sceneHint = normalizeSceneHint(intent.sceneHint);
        if (sceneHint == null) {
            sceneHint = sceneSpec.sceneHint;
        }

        String titleSource = sceneTheme != null ? sceneTheme : rawText;
        String title = titleSource.length() > 12 ? titleSource.substring(0, 12) : titleSource;
        if (title == null || title.isBlank()) {
            title = "玩家剧情";
        }

        final String finalSceneTheme = sceneTheme;
        final String finalSceneHint = sceneHint;

        Map<String, Object> body = new HashMap<>();
        Location playerLocation = fp.getLocation();
        Map<String, Object> playerPosition = new HashMap<>();
        playerPosition.put("world", playerLocation.getWorld() != null ? playerLocation.getWorld().getName() : "world");
        playerPosition.put("x", playerLocation.getX());
        playerPosition.put("y", playerLocation.getY());
        playerPosition.put("z", playerLocation.getZ());

        body.put("level_id", "flagship_custom_" + System.currentTimeMillis());
        body.put("title", title);
        body.put("text", rawText);
        body.put("player_id", fp.getName());
        body.put("anchor", "player");
        body.put("player_position", playerPosition);
        if (finalSceneTheme != null) {
            body.put("scene_theme", finalSceneTheme);
        }
        if (finalSceneHint != null) {
            body.put("scene_hint", finalSceneHint);
        }

        plugin.getLogger().log(Level.INFO,
            "[DEBUG] CREATE_STORY scene_theme={0} scene_hint={1} player={2} pos=({3},{4},{5})",
            new Object[]{
                finalSceneTheme != null ? finalSceneTheme : "<none>",
                finalSceneHint != null ? finalSceneHint : "<none>",
                fp.getName(),
                String.format(Locale.ROOT, "%.1f", playerLocation.getX()),
                String.format(Locale.ROOT, "%.1f", playerLocation.getY()),
                String.format(Locale.ROOT, "%.1f", playerLocation.getZ()),
            });

        String jsonBody = GSON.toJson(body);

        if (finalSceneTheme != null && finalSceneHint != null) {
            fp.sendMessage("§e✨ 正在根据主题「" + finalSceneTheme + "」在「" + finalSceneHint + "」生成场景...");
        } else if (finalSceneTheme != null) {
            fp.sendMessage("§e✨ 正在根据主题「" + finalSceneTheme + "」生成场景...");
        } else if (finalSceneHint != null) {
            fp.sendMessage("§e✨ 正在根据地点「" + finalSceneHint + "」生成场景...");
        } else {
            fp.sendMessage("§e✨ 正在创建新剧情...");
        }

        backend.postJsonAsync("/story/inject", jsonBody, new Callback() {

            @Override
            public void onFailure(Call call, IOException e) {
                Bukkit.getScheduler().runTask(plugin,
                        () -> fp.sendMessage("§c[创建剧情失败][网络错误] " + e.getMessage()));
            }

            @Override
            public void onResponse(Call call, Response resp) throws IOException {
                try (resp) {
                    String respStr = resp.body() != null ? resp.body().string() : "{}";
                    final int statusCode = resp.code();
                    final boolean success = resp.isSuccessful();
                    final JsonObject root = parseJsonObjectSafely(respStr);

                    Bukkit.getScheduler().runTask(plugin, () -> {
                        if (!success) {
                            fp.sendMessage("§c创建失败: " + formatHttpError(statusCode, root, respStr));
                            return;
                        }

                        if (isPayloadV1(root)) {
                            boolean accepted = payloadExecutor != null && payloadExecutor.enqueue(fp, root);
                            if (accepted) {
                                fp.sendMessage("§a✅ 剧情创建成功（payload v1）");
                                String buildId = root.has("build_id") ? root.get("build_id").getAsString() : "unknown";
                                int commandsCount = root.has("commands") && root.get("commands").isJsonArray()
                                        ? root.getAsJsonArray("commands").size()
                                        : 0;
                                fp.sendMessage("§7build_id: " + buildId + "  commands=" + commandsCount);
                            } else {
                                fp.sendMessage("§c创建成功但执行器拒绝 payload，请用 /taskdebug 排查。");
                            }
                            return;
                        }

                        if (root.has("status") && "ok".equals(root.get("status").getAsString())) {
                            String levelId = root.has("level_id") ? root.get("level_id").getAsString() : "未知";
                            fp.sendMessage("§a✅ 剧情创建成功！");
                            fp.sendMessage("§7关卡ID: " + levelId);

                            // 立即加载新创建的关卡（这样会应用场景和NPC）
                            loadLevelForPlayer(fp, levelId, intent, finalSceneTheme, finalSceneHint);
                        } else {
                            String msg = extractErrorMessage(root, respStr);
                            fp.sendMessage("§c创建失败: " + msg);
                        }
                    });
                }
            }
        });
    }

    // ============================================================
    // 为玩家加载关卡（应用场景和NPC）
    // ============================================================
    private void loadLevelForPlayer(Player p, String levelId, IntentResponse2 intent, String sceneTheme, String sceneHint) {
        final Player fp = p;
        final String canonicalLevel = LevelIds.canonicalizeOrDefault(
                enforceTutorialExitRedirect(fp, levelId, intent != null ? intent.rawText : null));
        final String normalizedSceneTheme = normalizeSceneTheme(sceneTheme);
        final String normalizedSceneHint = normalizeSceneHint(sceneHint);

        fp.sendMessage("§e🌍 正在加载关卡场景...");

        backend.postJsonAsync("/story/load/" + fp.getName() + "/" + canonicalLevel, "{}", new Callback() {

            @Override
            public void onFailure(Call call, IOException e) {
                plugin.getLogger().warning("[加载关卡] 失败: " + e.getMessage());
                Bukkit.getScheduler().runTask(plugin, () -> {
                    fp.sendMessage("§c[加载场景失败] " + e.getMessage());

                    // 失败时使用intent中的worldPatch作为备用
                    if (intent != null && intent.worldPatch != null) {
                        plugin.getLogger().info("[加载关卡] 使用备用worldPatch");
                        Map<String, Object> patch = GSON.fromJson(intent.worldPatch, MAP_TYPE);
                        syncTutorialState(fp, patch);
                        world.execute(fp, patch);
                        fp.sendMessage("§a✨ 场景已加载！（备用路径）");
                        String sceneReadyMessage = buildSceneReadyMessage(normalizedSceneTheme, normalizedSceneHint);
                        if (sceneReadyMessage != null) {
                            fp.sendMessage(sceneReadyMessage);
                        }
                    }
                });
            }

            @Override
            public void onResponse(Call call, Response resp) throws IOException {
                final String respStr = resp.body() != null ? resp.body().string() : "{}";
                plugin.getLogger().info("[加载关卡] 收到响应: " + respStr.substring(0, Math.min(200, respStr.length())));

                final JsonObject root = JsonParser.parseString(respStr).getAsJsonObject();

                final JsonObject patchObj = (root.has("bootstrap_patch") && root.get("bootstrap_patch").isJsonObject())
                        ? root.getAsJsonObject("bootstrap_patch")
                        : null;

                Bukkit.getScheduler().runTask(plugin, () -> {
                    if (patchObj != null && patchObj.size() > 0) {
                        plugin.getLogger().info("[加载关卡] 应用bootstrap_patch");
                        Map<String, Object> patch = GSON.fromJson(patchObj, MAP_TYPE);
                        syncTutorialState(fp, patch);
                        world.execute(fp, patch);
                        fp.sendMessage("§a✨ 场景已加载！");
                        String sceneReadyMessage = buildSceneReadyMessage(normalizedSceneTheme, normalizedSceneHint);
                        if (sceneReadyMessage != null) {
                            fp.sendMessage(sceneReadyMessage);
                        }
                    } else {
                        plugin.getLogger().warning("[加载关卡] bootstrap_patch为空");

                        // 如果后端没有返回patch，使用intent中的worldPatch
                        if (intent != null && intent.worldPatch != null) {
                            plugin.getLogger().info("[加载关卡] 使用intent的worldPatch");
                            Map<String, Object> patch = GSON.fromJson(intent.worldPatch, MAP_TYPE);
                            syncTutorialState(fp, patch);
                            world.execute(fp, patch);
                            fp.sendMessage("§a✨ 场景已加载！");
                            String sceneReadyMessage = buildSceneReadyMessage(normalizedSceneTheme, normalizedSceneHint);
                            if (sceneReadyMessage != null) {
                                fp.sendMessage(sceneReadyMessage);
                            }
                        } else {
                            plugin.getLogger().log(Level.WARNING,
                                    "[CREATE_STORY] no patch applied level={0} player={1} scene_theme={2} scene_hint={3}",
                                    new Object[]{canonicalLevel, fp.getName(),
                                            normalizedSceneTheme != null ? normalizedSceneTheme : "<none>",
                                            normalizedSceneHint != null ? normalizedSceneHint : "<none>"});
                            fp.sendMessage("§c⚠ 剧情已创建，但场景补丁未下发。请重试或用 /taskdebug 排查。");
                            fp.sendMessage("§7（场景数据为空）");
                        }
                    }

                    if (questLogHud != null) {
                        questLogHud.showQuestLog(fp, QuestLogHud.Trigger.LEVEL_ENTER);
                    }
                });
            }
        });
    }

    // ============================================================
    // 世界命令
    // ============================================================
    private void runWorldCommand(Player p, IntentResponse2 intent) {

        Map<String, Object> mc = new HashMap<>();

        switch (intent.type) {
            case SET_DAY:
                mc.put("time", "day");
                break;

            case SET_NIGHT:
                mc.put("time", "night");
                break;

            case SET_WEATHER:
                String raw = intent.rawText != null ? intent.rawText : "";
                String w = raw.contains("雨") ? "rain" : raw.contains("雷") ? "thunder" : "clear";
                mc.put("weather", w);
                break;

            case TELEPORT:
                Map<String, Object> t = new HashMap<>();
                t.put("mode", "relative");
                t.put("x", 0);
                t.put("y", 0);
                t.put("z", 3);
                mc.put("teleport", t);
                break;

            case SPAWN_ENTITY:
                Map<String, Object> s = new HashMap<>();
                s.put("type", "ARMOR_STAND");
                mc.put("spawn", s);
                break;

            case BUILD_STRUCTURE:
                Map<String, Object> b = new HashMap<>();
                b.put("shape", "platform");
                b.put("size", 4);
                mc.put("build", b);
                break;

            default:
                break;
        }

        Map<String, Object> body = new HashMap<>();
        body.put("mc", mc);

        world.execute(p, body);
    }

    // ============================================================
    // 小地图展示 - 显示PNG图片
    // ============================================================
    private void showMinimap(Player p, IntentResponse2 intent) {

        if (!ensureUnlocked(p, TutorialState.VIEW_MAP, "完成小地图教学后即可使用该功能。")) {
            return;
        }

        JsonObject mm = intent.minimap;
        if (mm == null) {
            p.sendMessage("§c[小地图] 后端未返回 minimap 数据。");
            return;
        }

        // 显示当前关卡信息
        String cur = (mm.has("current_level") && !mm.get("current_level").isJsonNull())
                ? LevelIds.canonicalizeLevelId(mm.get("current_level").getAsString())
                : "未知";
        String nxt = (mm.has("recommended_next") && !mm.get("recommended_next").isJsonNull())
                ? LevelIds.canonicalizeLevelId(mm.get("recommended_next").getAsString())
                : "无";

        p.sendMessage("§b--- 心悦小地图 ---");
        p.sendMessage("当前关卡: §a" + cur);
        p.sendMessage("推荐下一关: §d" + nxt);

        // 异步获取PNG地图并给予玩家
        final Player fp = p;
        backend.getAsync("/minimap/give/" + p.getName(), new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                plugin.getLogger().warning("[小地图PNG] 获取失败: " + e.getMessage());
                Bukkit.getScheduler().runTask(plugin,
                        () -> fp.sendMessage("§c[小地图] PNG生成失败"));
            }

            @Override
            public void onResponse(Call call, Response response) throws IOException {
                if (!response.isSuccessful()) {
                    plugin.getLogger().warning("[小地图PNG] HTTP " + response.code());
                    Bukkit.getScheduler().runTask(plugin,
                            () -> fp.sendMessage("§c[小地图] 服务器返回错误"));
                    return;
                }

                String json = response.body().string();
                JsonObject obj = GSON.fromJson(json, JsonObject.class);

                // 后端返回的格式: { "status": "ok", "mc": { ... } }
                JsonObject mcPayload = null;
                if (obj.has("world_patch") && obj.get("world_patch").isJsonObject()) {
                    JsonObject worldPatch = obj.getAsJsonObject("world_patch");
                    if (worldPatch.has("mc") && worldPatch.get("mc").isJsonObject()) {
                        mcPayload = worldPatch.getAsJsonObject("mc");
                    }
                }
                if (mcPayload == null && obj.has("mc") && obj.get("mc").isJsonObject()) {
                    mcPayload = obj.getAsJsonObject("mc");
                }

                if (mcPayload != null) {
                    // 在主线程执行MC命令
                    JsonObject finalMcPayload = mcPayload;
                    Bukkit.getScheduler().runTask(plugin, () -> {
                        if (finalMcPayload.has("give_item") && !finalMcPayload.get("give_item").isJsonNull()
                                && finalMcPayload.has("map_image") && !finalMcPayload.get("map_image").isJsonNull()) {
                            String itemType = finalMcPayload.get("give_item").getAsString();
                            String base64Image = finalMcPayload.get("map_image").getAsString();
                            if ("filled_map".equalsIgnoreCase(itemType)) {
                                org.bukkit.Material mapMat = org.bukkit.Material.FILLED_MAP;
                                org.bukkit.inventory.ItemStack mapItem = new org.bukkit.inventory.ItemStack(mapMat);
                                org.bukkit.inventory.meta.MapMeta mapMeta = (org.bukkit.inventory.meta.MapMeta) mapItem
                                        .getItemMeta();
                                if (mapMeta != null) {
                                    mapMeta.displayName(net.kyori.adventure.text.Component.text("心悦小地图"));
                                    try {
                                        byte[] imgBytes = java.util.Base64.getDecoder().decode(base64Image);
                                        java.awt.image.BufferedImage img = javax.imageio.ImageIO
                                                .read(new java.io.ByteArrayInputStream(imgBytes));
                                        org.bukkit.map.MapView mapView = Bukkit.createMap(fp.getWorld());
                                        mapView.getRenderers().clear();
                                        mapView.addRenderer(new com.driftmc.minimap.PNGMapRenderer(img));
                                        mapMeta.setMapView(mapView);
                                    } catch (Exception e) {
                                        plugin.getLogger().warning("[小地图PNG] 渲染失败: " + e.getMessage());
                                    }
                                    mapItem.setItemMeta(mapMeta);
                                }
                                fp.getInventory().addItem(mapItem);
                            }
                        }
                        if (finalMcPayload.has("tell")) {
                            String msg = finalMcPayload.get("tell").getAsString();
                            fp.sendMessage("§e" + msg);
                        }
                    });
                }

                plugin.getLogger().info("[小地图PNG] 已发送给玩家: " + fp.getName());
            }
        });

        p.sendMessage("§b-------------------");
    }

    // ============================================================
    // —— 修复后的剧情推进代码（最关键） ——
    // ============================================================
    private void pushToStoryEngine(Player p, String text) {

        final Player fp = p;
        final String ftext = (text == null ? "" : text); // ← 彻底防止 null

        plugin.getLogger().info("[剧情推进] 玩家: " + fp.getName() + ", 文本: " + ftext);

        // 不再使用 Map.of() —— 改为 HashMap 全兼容安全版
        Map<String, Object> body = new HashMap<>();
        body.put("player_id", fp.getName());

        Map<String, Object> action = new HashMap<>();
        action.put("say", ftext);
        body.put("action", action);

        body.put("world_state", new HashMap<>()); // 空但安全的 map

        backend.postJsonAsync("/world/apply", GSON.toJson(body), new Callback() {

            @Override
            public void onFailure(Call call, IOException e) {
                plugin.getLogger().warning("[剧情推进] 请求失败: " + e.getMessage());
                Bukkit.getScheduler().runTask(plugin,
                        () -> fp.sendMessage("§c[剧情错误] " + e.getMessage()));
            }

            @Override
            public void onResponse(Call call, Response resp) throws IOException {

                final String respStr = resp.body() != null ? resp.body().string() : "{}";
                plugin.getLogger().info("[剧情推进] 收到响应: " + respStr.substring(0, Math.min(200, respStr.length())));

                final JsonObject root = JsonParser.parseString(respStr).getAsJsonObject();

                final JsonObject node = (root.has("story_node") && root.get("story_node").isJsonObject())
                        ? root.get("story_node").getAsJsonObject()
                        : null;

                final JsonObject payloadV1 = extractPayloadV1(root);

                final JsonObject wpatch = (root.has("world_patch") && root.get("world_patch").isJsonObject())
                        ? root.get("world_patch").getAsJsonObject()
                        : null;

                Bukkit.getScheduler().runTask(plugin, () -> {

                    if (node != null) {
                        String nodeType = node.has("type") ? node.get("type").getAsString() : "";
                        if ("npc_dialogue".equalsIgnoreCase(nodeType) && dialoguePanel != null) {
                            dialoguePanel.showDialogue(fp, node);
                        } else if ("story_choice".equalsIgnoreCase(nodeType) && choicePanel != null) {
                            choicePanel.presentChoiceNode(fp, node);
                        } else {
                            if (node.has("title")) {
                                String title = node.get("title").getAsString();
                                fp.sendMessage("§d【" + title + "】");
                                plugin.getLogger().info("[剧情推进] 显示标题: " + title);
                            }

                            if (node.has("text")) {
                                String storyText = node.get("text").getAsString();
                                fp.sendMessage("§f" + storyText);
                                plugin.getLogger()
                                        .info("[剧情推进] 显示文本: "
                                                + storyText.substring(0, Math.min(50, storyText.length())));
                            }
                        }
                    } else {
                        plugin.getLogger().warning("[剧情推进] story_node 为空");
                    }

                    boolean payloadHandled = false;
                    if (payloadV1 != null) {
                        payloadHandled = payloadExecutor != null && payloadExecutor.enqueue(fp, payloadV1);
                        if (!payloadHandled) {
                            plugin.getLogger().warning("[剧情推进] plugin_payload_v1 rejected");
                        } else {
                            plugin.getLogger().info("[剧情推进] plugin_payload_v1 accepted");
                        }
                    }

                    if (!payloadHandled && wpatch != null && wpatch.size() > 0) {
                        plugin.getLogger().info("[剧情推进] 执行世界patch");
                        Map<String, Object> patch = GSON.fromJson(wpatch, MAP_TYPE);
                        syncTutorialState(fp, patch);
                        world.execute(fp, patch);
                    }
                });
            }
        });
    }

    private JsonObject extractPayloadV1(JsonObject root) {
        if (root == null) {
            return null;
        }

        if (isPayloadV1(root)) {
            return root;
        }

        if (root.has("payload") && root.get("payload").isJsonObject()) {
            JsonObject payload = root.getAsJsonObject("payload");
            if (isPayloadV1(payload)) {
                return payload;
            }
        }

        if (root.has("world_patch") && root.get("world_patch").isJsonObject()) {
            JsonObject payload = root.getAsJsonObject("world_patch");
            if (isPayloadV1(payload)) {
                return payload;
            }
        }

        return null;
    }

    private boolean isPayloadV1(JsonObject obj) {
        return obj.has("version")
                && obj.get("version").isJsonPrimitive()
                && "plugin_payload_v1".equals(obj.get("version").getAsString());
    }

    // ============================================================
    // 跳关（传送 + 加载剧情）
    // ============================================================
    private void gotoLevelAndLoad(Player p, IntentResponse2 intent) {

        final Player fp = p;
        final String levelId = LevelIds.canonicalizeOrDefault(resolveRequestedLevel(fp, intent));
        final JsonObject minimap = intent.minimap;

        if (!ensureUnlocked(fp, TutorialState.JUMP_LEVEL, "完成关卡跳转教学后即可自由跳关。")) {
            return;
        }

        if (levelId == null) {
            p.sendMessage("§c跳关失败：没有 levelId");
            return;
        }

        if (minimap == null || !minimap.has("nodes")) {
            p.sendMessage("§c跳关失败：minimap 缺失");
            return;
        }

        JsonArray nodes = minimap.getAsJsonArray("nodes");

        int tx = 0, tz = 0;
        boolean found = false;

        for (int i = 0; i < nodes.size(); i++) {
            JsonObject n = nodes.get(i).getAsJsonObject();
            if (LevelIds.canonicalizeLevelId(n.get("level").getAsString()).equals(levelId)) {
                JsonObject pos = n.getAsJsonObject("pos");
                tx = pos.get("x").getAsInt();
                tz = pos.get("y").getAsInt();
                found = true;
                break;
            }
        }

        if (!found) {
            p.sendMessage("§c跳关失败：地图中不存在 " + levelId);
            return;
        }

        final int fx = tx;
        final int fy = 80;
        final int fz = tz;

        Bukkit.getScheduler().runTask(plugin, () -> fp.teleport(new Location(fp.getWorld(), fx, fy, fz)));

        backend.postJsonAsync("/story/load/" + fp.getName() + "/" + levelId,
                "{}",
                new Callback() {

                    @Override
                    public void onFailure(Call call, IOException e) {
                        Bukkit.getScheduler().runTask(plugin,
                                () -> fp.sendMessage("§c[剧情加载失败] " + e.getMessage()));
                    }

                    @Override
                    public void onResponse(Call call, Response resp) throws IOException {

                        final String respStr = resp.body() != null ? resp.body().string() : "{}";
                        final JsonObject root = JsonParser.parseString(respStr).getAsJsonObject();

                        final JsonObject patchObj = (root.has("bootstrap_patch")
                                && root.get("bootstrap_patch").isJsonObject())
                                        ? root.getAsJsonObject("bootstrap_patch")
                                        : null;

                        if (patchObj != null) {
                            final Map<String, Object> patch = GSON.fromJson(patchObj, MAP_TYPE);

                            Bukkit.getScheduler().runTask(plugin, () -> {
                                syncTutorialState(fp, patch);
                                world.execute(fp, patch);
                                if (questLogHud != null) {
                                    questLogHud.showQuestLog(fp, QuestLogHud.Trigger.LEVEL_ENTER);
                                }
                            });
                        } else if (questLogHud != null) {
                            Bukkit.getScheduler().runTask(plugin,
                                    () -> questLogHud.showQuestLog(fp, QuestLogHud.Trigger.LEVEL_ENTER));
                        }
                    }
                });
    }

    private String resolveRequestedLevel(Player player, IntentResponse2 intent) {
        if (intent == null) {
            return null;
        }
        return enforceTutorialExitRedirect(player, intent.levelId, intent.rawText);
    }

    private JsonObject parseJsonObjectSafely(String raw) {
        if (raw == null || raw.isBlank()) {
            return new JsonObject();
        }
        try {
            return JsonParser.parseString(raw).getAsJsonObject();
        } catch (IllegalStateException | JsonSyntaxException ex) {
            return new JsonObject();
        }
    }

    private String formatHttpError(int statusCode, JsonObject root, String raw) {
        String category;
        if (statusCode >= 500) {
            category = "服务端异常";
        } else if (statusCode >= 400) {
            category = "请求参数问题";
        } else if (statusCode >= 300) {
            category = "重定向异常";
        } else {
            category = "未知HTTP异常";
        }
        String detail = extractErrorMessage(root, raw);
        return category + " (HTTP " + statusCode + ")" + (detail == null || detail.isBlank() ? "" : " - " + detail);
    }

    private String extractErrorMessage(JsonObject root, String raw) {
        if (root != null) {
            if (root.has("detail") && root.get("detail").isJsonPrimitive()) {
                return root.get("detail").getAsString();
            }
            if (root.has("msg") && root.get("msg").isJsonPrimitive()) {
                return root.get("msg").getAsString();
            }
            if (root.has("error") && root.get("error").isJsonPrimitive()) {
                return root.get("error").getAsString();
            }
            if (root.has("story_node") && root.get("story_node").isJsonObject()) {
                JsonObject node = root.getAsJsonObject("story_node");
                if (node.has("text") && node.get("text").isJsonPrimitive()) {
                    return node.get("text").getAsString();
                }
            }
            if (root.has("status") && root.get("status").isJsonPrimitive()) {
                String status = root.get("status").getAsString();
                if (!"ok".equalsIgnoreCase(status)) {
                    return "status=" + status;
                }
            }
        }

        if (raw == null || raw.isBlank()) {
            return "未知错误";
        }
        String compact = raw.replace('\n', ' ').trim();
        if (compact.length() > 160) {
            return compact.substring(0, 160) + "...";
        }
        return compact;
    }

    private String normalizeSceneTheme(String rawTheme) {
        if (rawTheme == null) {
            return null;
        }
        String theme = rawTheme.trim();
        if (theme.isEmpty()) {
            return null;
        }

        theme = theme.replaceFirst("^[\\s:：,，\\-—\\\"'“”‘’]+", "");
        theme = theme.replaceFirst("[\\s。！!？?]+$", "").trim();

        if (theme.isEmpty()) {
            return null;
        }
        return theme;
    }

    private String normalizeSceneHint(String rawHint) {
        if (rawHint == null) {
            return null;
        }
        String hint = rawHint.trim();
        if (hint.isEmpty()) {
            return null;
        }

        hint = hint.replaceFirst("^[\\s:：,，\\-—\\\"'“”‘’]+", "");
        hint = hint.replaceFirst("[\\s。！!？?]+$", "").trim();

        String[] suffixes = {"里面", "里边", "附近", "周围", "一带", "这里", "那里", "场景", "里", "中"};
        for (String suffix : suffixes) {
            if (hint.endsWith(suffix) && hint.length() > suffix.length()) {
                hint = hint.substring(0, hint.length() - suffix.length()).trim();
                break;
            }
        }

        if (hint.startsWith("在") && hint.length() > 1) {
            hint = hint.substring(1).trim();
        }

        if (hint.isEmpty()) {
            return null;
        }
        return hint;
    }

    private String buildSceneReadyMessage(String sceneTheme, String sceneHint) {
        String normalizedTheme = normalizeSceneTheme(sceneTheme);
        String normalizedHint = normalizeSceneHint(sceneHint);

        if (normalizedTheme != null && normalizedHint != null) {
            return "§a系统：主题「" + normalizedTheme + "」在「" + normalizedHint + "」场景已生成。";
        }
        if (normalizedTheme != null) {
            return "§a系统：主题「" + normalizedTheme + "」场景已生成。";
        }
        if (normalizedHint != null) {
            return "§a系统：地点「" + normalizedHint + "」场景已生成。";
        }
        return null;
    }

    private SceneSpec extractSceneSpecFromRawText(String rawText) {
        String normalizedRaw = rawText == null ? "" : rawText.trim();
        if (normalizedRaw.isEmpty()) {
            return new SceneSpec(null, null);
        }

        String sceneHint = null;
        int tailHintIndex = normalizedRaw.lastIndexOf(" 在");
        if (tailHintIndex > 0 && tailHintIndex + 2 < normalizedRaw.length()) {
            String tailHint = normalizeSceneHint(normalizedRaw.substring(tailHintIndex + 2));
            if (tailHint != null) {
                sceneHint = tailHint;
                normalizedRaw = normalizedRaw.substring(0, tailHintIndex).trim();
            }
        }

        String[] prefixes = {
                "创建剧情",
                "创建关卡",
                "导入剧情",
                "写剧情",
                "写故事",
                "编故事",
                "创造剧情",
                "生成剧情"
        };

        for (String prefix : prefixes) {
            if (normalizedRaw.startsWith(prefix)) {
                String content = normalizedRaw.substring(prefix.length()).trim();
                int inlineHintIndex = content.lastIndexOf(" 在");
                if (inlineHintIndex > 0 && inlineHintIndex + 2 < content.length()) {
                    String inlineHint = normalizeSceneHint(content.substring(inlineHintIndex + 2));
                    if (inlineHint != null && sceneHint == null) {
                        sceneHint = inlineHint;
                        content = content.substring(0, inlineHintIndex).trim();
                    }
                }

                String candidate = normalizeSceneTheme(content);
                if (candidate != null) {
                    return new SceneSpec(candidate, sceneHint);
                }
                break;
            }
        }

        if (normalizedRaw.startsWith("我要") && normalizedRaw.endsWith("的场景")
                && normalizedRaw.length() > "我要".length() + "的场景".length()) {
            String middle = normalizedRaw.substring("我要".length(), normalizedRaw.length() - "的场景".length());
            if (middle.startsWith("一个")) {
                middle = middle.substring("一个".length());
            }
            String candidate = normalizeSceneTheme(middle);
            if (candidate != null) {
                return new SceneSpec(candidate, sceneHint);
            }
        }

        return new SceneSpec(null, sceneHint);
    }

    private static final class SceneSpec {
        private final String sceneTheme;
        private final String sceneHint;

        private SceneSpec(String sceneTheme, String sceneHint) {
            this.sceneTheme = sceneTheme;
            this.sceneHint = sceneHint;
        }
    }

    private String enforceTutorialExitRedirect(Player player, String requestedLevelId, String rawText) {
        String canonicalRequested = LevelIds.canonicalizeLevelId(requestedLevelId);
        if (tutorialManager == null || player == null) {
            return canonicalRequested;
        }

        if (!tutorialManager.hasExitedTutorial(player)) {
            return canonicalRequested;
        }

        boolean requestTargetsTutorial = LevelIds.isFlagshipTutorial(canonicalRequested);
        String normalizedRaw = rawText != null ? rawText.toLowerCase(Locale.ROOT) : "";
        if (!normalizedRaw.isBlank()) {
            requestTargetsTutorial = requestTargetsTutorial
                    || normalizedRaw.contains("第一关")
                    || normalizedRaw.contains("主线")
                    || normalizedRaw.contains("开始");
        }

        if (requestTargetsTutorial) {
            warnTutorialReentry(player);
            return PRIMARY_LEVEL_ID;
        }

        return canonicalRequested;
    }

    private void warnTutorialReentry(Player player) {
        if (player == null) {
            return;
        }
        UUID playerId = player.getUniqueId();
        if (tutorialReentryWarned.add(playerId)) {
            plugin.getLogger().warning("[LevelResolve] Blocked tutorial re-entry for " + player.getName());
        }
    }
}
