package com.driftmc.commands;

import java.lang.reflect.Type;
import java.util.Map;
import java.util.UUID;

import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;

import com.driftmc.backend.BackendClient;
import com.driftmc.intent2.IntentDispatcher2;
import com.driftmc.intent2.IntentResponse2;
import com.driftmc.intent2.IntentRouter2;
import com.driftmc.intent2.IntentType2;
import com.driftmc.world.PayloadExecutorV1;
import com.driftmc.world.WorldPatchExecutor;
import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.reflect.TypeToken;

/**
 * /create <关卡描述文字>
 *
 * 玩家在 MC 聊天框输入一句话描述想要的关卡。
 * Phase 5: 先调用 Intent Engine 进行难度评分和 scene_type 分类，
 *          再通过 IntentDispatcher2 路由到合适的生成路径：
 *          - difficulty < 3: 直接 /story/inject（快速生成）
 *          - difficulty >= 3 + CONTENT: drift_experience（Beam Search + 叙事弧）
 *          - RULE/SIMULATION: AsyncAIFlow Pipeline（代码生成）
 *
 * 例：/create 限时60秒逃脱迷宫，被守卫发现则失败，到达出口则胜利
 */
public class CreateLevelCommand implements CommandExecutor {

    private static final Gson GSON = new Gson();

    private final JavaPlugin plugin;
    private final BackendClient backend;
    private final WorldPatchExecutor world;
    private final PayloadExecutorV1 payloadExecutor;

    /** Phase 5: IntentRouter2 + IntentDispatcher2 for smart routing */
    private IntentRouter2 intentRouter2;
    private IntentDispatcher2 intentDispatcher2;

    public CreateLevelCommand(
            JavaPlugin plugin,
            BackendClient backend,
            WorldPatchExecutor world,
            PayloadExecutorV1 payloadExecutor) {
        this.plugin = plugin;
        this.backend = backend;
        this.world = world;
        this.payloadExecutor = payloadExecutor;
    }

    /** Phase 5: 注入 Intent 路由组件，启用智能路由 */
    public void setIntentRouter2(IntentRouter2 router) {
        this.intentRouter2 = router;
    }

    public void setIntentDispatcher2(IntentDispatcher2 dispatcher) {
        this.intentDispatcher2 = dispatcher;
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(ChatColor.RED + "只有玩家可以创建关卡");
            return true;
        }

        if (args.length == 0) {
            player.sendMessage(ChatColor.GOLD + "用法: /create <关卡描述>");
            player.sendMessage(ChatColor.GRAY + "例: /create 限时60秒收集3块宝石，被守卫发现则失败");
            return true;
        }

        String text = String.join(" ", args);

        // Phase 5: 如果 IntentRouter2 + IntentDispatcher2 已注入，走智能路由
        if (intentRouter2 != null && intentDispatcher2 != null) {
            player.sendMessage(ChatColor.YELLOW + "🎨 正在分析关卡复杂度...");
            player.sendMessage(ChatColor.GRAY + "描述: " + text);
            routeViaIntentEngine(player, text);
            return true;
        }

        // Fallback: 无 Intent 组件时走原始直连路径
        String playerId = player.getName();
        String rawLevelId = "custom_" + playerId.toLowerCase() + "_" + System.currentTimeMillis();
        final String levelId = rawLevelId.length() > 48 ? rawLevelId.substring(0, 48) : rawLevelId;

        player.sendMessage(ChatColor.YELLOW + "🎨 正在生成关卡，请稍候...");
        player.sendMessage(ChatColor.GRAY + "描述: " + text);

        UUID playerUuid = player.getUniqueId();
        String injectBody = buildInjectBody(levelId, text, playerId);

        Bukkit.getScheduler().runTaskAsynchronously(plugin, () -> {
            try {
                String injectResp = backend.postJson("/story/inject", injectBody);
                JsonObject injectObj = JsonParser.parseString(injectResp).getAsJsonObject();

                String status = injectObj.has("status")
                        ? injectObj.get("status").getAsString() : "error";

                if (!"ok".equals(status)) {
                    String msg = injectObj.has("msg") ? injectObj.get("msg").getAsString() : "未知错误";
                    Bukkit.getScheduler().runTask(plugin, () -> {
                        Player p = Bukkit.getPlayer(playerUuid);
                        if (p != null) p.sendMessage(ChatColor.RED + "❌ 关卡创建失败: " + msg);
                    });
                    return;
                }

                final String actualLevelId = injectObj.has("level_id")
                        ? injectObj.get("level_id").getAsString() : levelId;

                if (injectObj.has("experience_spec_summary")) {
                    JsonObject summary = injectObj.getAsJsonObject("experience_spec_summary");
                    Bukkit.getScheduler().runTask(plugin, () -> {
                        Player p = Bukkit.getPlayer(playerUuid);
                        if (p == null) return;
                        p.sendMessage(ChatColor.AQUA + "✨ 关卡已生成: " + ChatColor.GOLD + actualLevelId);
                        if (summary.has("win_conditions")) {
                            p.sendMessage(ChatColor.GREEN + "  胜利: " + summary.get("win_conditions").getAsString());
                        }
                        if (summary.has("lose_conditions")) {
                            p.sendMessage(ChatColor.RED + "  失败: " + summary.get("lose_conditions").getAsString());
                        }
                        if (summary.has("trigger_count")) {
                            p.sendMessage(ChatColor.YELLOW + "  触发器: " + summary.get("trigger_count").getAsString() + " 个");
                        }
                    });
                }

                String loadResp = backend.postJson("/story/load/" + playerId + "/" + actualLevelId, "{}");
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p == null || !p.isOnline()) return;
                    applyLoadResponse(p, loadResp);
                    p.sendMessage(ChatColor.GREEN + "✔ 关卡已加载！开始你的冒险吧。");
                });

            } catch (Exception e) {
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p != null) p.sendMessage(ChatColor.RED + "❌ 关卡创建出错: " + e.getMessage());
                });
            }
        });

        return true;
    }

    /**
     * Phase 5: 通过 IntentRouter2 调用后端 Intent Engine 进行意图解析，
     * 然后通过 IntentDispatcher2 路由到最优的生成路径。
     */
    private void routeViaIntentEngine(Player player, String text) {
        intentRouter2.askIntent(player.getName(), text, intents -> {
            // 取第一个意图（通常只有一个 CREATE_STORY）
            IntentResponse2 intent = intents.get(0);

            // 强制 type 为 CREATE_STORY（因为 /create 命令的意图总是创建关卡）
            IntentResponse2 createIntent = new IntentResponse2(
                    IntentType2.CREATE_STORY,
                    intent.levelId,
                    intent.minimap,
                    text,
                    intent.sceneTheme,
                    intent.sceneHint,
                    intent.worldPatch,
                    intent.difficulty,
                    intent.sceneType);

            plugin.getLogger().info("[/create] Intent parsed: difficulty=" + createIntent.difficulty
                    + " sceneType=" + createIntent.sceneType + " player=" + player.getName());

            // 切回主线程执行 dispatch
            Bukkit.getScheduler().runTask(plugin, () -> {
                player.sendMessage(ChatColor.AQUA + "📊 难度评分: " + createIntent.difficulty
                        + "★ | 类型: " + createIntent.sceneType);
                intentDispatcher2.dispatch(player, createIntent);
            });
        });
    }

    /** Fallback: 意图解析失败时直接走 /story/inject */
    private void fallbackDirectInject(Player player, String text) {
        String playerId = player.getName();
        String rawLevelId = "custom_" + playerId.toLowerCase() + "_" + System.currentTimeMillis();
        final String levelId = rawLevelId.length() > 48 ? rawLevelId.substring(0, 48) : rawLevelId;
        UUID playerUuid = player.getUniqueId();
        String injectBody = buildInjectBody(levelId, text, playerId);

        Bukkit.getScheduler().runTaskAsynchronously(plugin, () -> {
            try {
                String injectResp = backend.postJson("/story/inject", injectBody);
                JsonObject injectObj = JsonParser.parseString(injectResp).getAsJsonObject();
                String status = injectObj.has("status") ? injectObj.get("status").getAsString() : "error";
                if (!"ok".equals(status)) {
                    String msg = injectObj.has("msg") ? injectObj.get("msg").getAsString() : "未知错误";
                    Bukkit.getScheduler().runTask(plugin, () -> {
                        Player p = Bukkit.getPlayer(playerUuid);
                        if (p != null) p.sendMessage(ChatColor.RED + "❌ 关卡创建失败: " + msg);
                    });
                    return;
                }
                final String actualLevelId = injectObj.has("level_id")
                        ? injectObj.get("level_id").getAsString() : levelId;
                String loadResp = backend.postJson("/story/load/" + playerId + "/" + actualLevelId, "{}");
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p == null || !p.isOnline()) return;
                    applyLoadResponse(p, loadResp);
                    p.sendMessage(ChatColor.GREEN + "✔ 关卡已加载！开始你的冒险吧。");
                });
            } catch (Exception e) {
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p != null) p.sendMessage(ChatColor.RED + "❌ 关卡创建出错: " + e.getMessage());
                });
            }
        });
    }

    private String buildInjectBody(String levelId, String text, String playerId) {
        JsonObject body = new JsonObject();
        body.addProperty("level_id", levelId);
        body.addProperty("title", text.length() > 30 ? text.substring(0, 30) : text);
        body.addProperty("text", text);
        body.addProperty("player_id", playerId);
        return body.toString();
    }

    @SuppressWarnings("unchecked")
    private void applyLoadResponse(Player player, String resp) {
        try {
            JsonElement rootEl = JsonParser.parseString(resp);
            if (!rootEl.isJsonObject()) return;
            JsonObject root = rootEl.getAsJsonObject();

            JsonObject patchObj = null;
            if (root.has("bootstrap_patch") && root.get("bootstrap_patch").isJsonObject()) {
                patchObj = root.getAsJsonObject("bootstrap_patch");
            } else if (root.has("world_patch") && root.get("world_patch").isJsonObject()) {
                patchObj = root.getAsJsonObject("world_patch");
            }
            if (patchObj == null) return;

            if (patchObj.has("version")
                    && "plugin_payload_v1".equals(patchObj.get("version").getAsString())) {
                if (payloadExecutor != null) payloadExecutor.enqueue(player, patchObj);
                return;
            }

            Type type = new TypeToken<Map<String, Object>>() {}.getType();
            Map<String, Object> patch = GSON.fromJson(patchObj, type);
            if (patch == null || patch.isEmpty()) return;

            @SuppressWarnings("rawtypes")
            Object mcObj = patch.get("mc");
            Map<String, Object> mcPatch = (mcObj instanceof Map) ? (Map<String, Object>) mcObj : patch;
            world.execute(player, mcPatch);

        } catch (Exception ignored) {
        }
    }
}
