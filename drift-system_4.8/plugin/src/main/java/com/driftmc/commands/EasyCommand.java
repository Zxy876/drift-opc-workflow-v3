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
import com.driftmc.scene.SceneAwareWorldPatchExecutor;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.reflect.TypeToken;

/**
 * /easy - Reload the current level at a lower difficulty.
 */
public class EasyCommand implements CommandExecutor {

    private static final Gson GSON = new Gson();
    private static final Type MAP_TYPE = new TypeToken<Map<String, Object>>() {}.getType();

    private final JavaPlugin plugin;
    private final BackendClient backend;
    private final SceneAwareWorldPatchExecutor world;

    public EasyCommand(JavaPlugin plugin, BackendClient backend, SceneAwareWorldPatchExecutor world) {
        this.plugin = plugin;
        this.backend = backend;
        this.world = world;
    }

    @Override
    public boolean onCommand(CommandSender sender, Command cmd, String label, String[] args) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(ChatColor.RED + "Only players can use /easy.");
            return true;
        }

        player.sendMessage(ChatColor.AQUA + "\u6b63\u5728\u964d\u4f4e\u96be\u5ea6\u5e76\u91cd\u65b0\u52a0\u8f7d\u5173\u5361...");

        UUID playerUuid = player.getUniqueId();
        String playerId = player.getName();

        Bukkit.getScheduler().runTaskAsynchronously(plugin, () -> {
            try {
                String stateResp = backend.postJson("/story/state/" + playerId, "{}");
                JsonObject stateRoot = JsonParser.parseString(stateResp).getAsJsonObject();
                JsonObject state = stateRoot.has("state") && stateRoot.get("state").isJsonObject()
                        ? stateRoot.getAsJsonObject("state") : stateRoot;

                String currentLevelId = null;
                if (state.has("player_current_level") && !state.get("player_current_level").isJsonNull()) {
                    currentLevelId = state.get("player_current_level").getAsString();
                }
                if (currentLevelId == null && state.has("current_level") && !state.get("current_level").isJsonNull()) {
                    currentLevelId = state.get("current_level").getAsString();
                }

                if (currentLevelId == null || currentLevelId.isBlank()) {
                    Bukkit.getScheduler().runTask(plugin, () -> {
                        Player p = Bukkit.getPlayer(playerUuid);
                        if (p != null && p.isOnline()) {
                            p.sendMessage(ChatColor.YELLOW + "\u5f53\u524d\u6ca1\u6709\u6d3b\u8dc3\u5173\u5361\uff0c\u65e0\u6cd5\u964d\u4f4e\u96be\u5ea6\u3002");
                        }
                    });
                    return;
                }

                // 2. 获取当前难度
                int currentDifficulty = 1;
                try {
                    String advResp = backend.postJson("/story/auto-advance/" + playerId, "{}");
                    JsonObject advRoot = JsonParser.parseString(advResp).getAsJsonObject();
                    if (advRoot.has("current_difficulty")) {
                        currentDifficulty = advRoot.get("current_difficulty").getAsInt();
                    }
                } catch (Exception ignored) {
                    // fallback to D1
                }
                final int targetDifficulty = Math.max(1, currentDifficulty - 1);

                // 3. 以降低后的难度重新加载关卡
                String loadBody = "{\"difficulty_override\":" + targetDifficulty + "}";
                String loadResp = backend.postJson("/story/load/" + playerId + "/" + currentLevelId,
                        loadBody);
                JsonObject loadRoot = JsonParser.parseString(loadResp).getAsJsonObject();

                final String levelId = currentLevelId;
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p == null || !p.isOnline()) {
                        return;
                    }

                    world.cleanupDifficultyState(p);

                    JsonObject patchObj = null;
                    if (loadRoot.has("bootstrap_patch") && loadRoot.get("bootstrap_patch").isJsonObject()) {
                        patchObj = loadRoot.getAsJsonObject("bootstrap_patch");
                    } else if (loadRoot.has("world_patch") && loadRoot.get("world_patch").isJsonObject()) {
                        patchObj = loadRoot.getAsJsonObject("world_patch");
                    }
                    if (patchObj != null) {
                        Map<String, Object> patch = GSON.fromJson(patchObj, MAP_TYPE);
                        if (patch != null && !patch.isEmpty()) {
                            world.execute(p, patch);
                        }
                    }

                    p.sendMessage(ChatColor.GREEN + "" + ChatColor.BOLD + "难度已降至 D" + targetDifficulty + "！关卡重新加载: " + levelId);
                    p.sendMessage(ChatColor.GRAY + "\u4eab\u53d7\u66f4\u8f7b\u677e\u7684\u4f53\u9a8c\u5427~");
                    plugin.getLogger().info("[Easy] " + playerId + " lowered difficulty to D" + targetDifficulty + " for level " + levelId);
                });

            } catch (Exception e) {
                plugin.getLogger().warning("[Easy] Failed for " + playerId + ": " + e.getMessage());
                Bukkit.getScheduler().runTask(plugin, () -> {
                    Player p = Bukkit.getPlayer(playerUuid);
                    if (p != null && p.isOnline()) {
                        p.sendMessage(ChatColor.RED + "\u964d\u4f4e\u96be\u5ea6\u5931\u8d25: " + e.getMessage());
                    }
                });
            }
        });

        return true;
    }
}
