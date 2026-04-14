package com.driftmc.scene;

import org.bukkit.ChatColor;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.block.Block;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.entity.EntityPickupItemEvent;
import org.bukkit.event.player.PlayerInteractEntityEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.inventory.ItemStack;

/**
 * Captures common in-world interactions and forwards them to {@link RuleEventBridge}.
 */
public final class RuleEventListener implements Listener {

    private final RuleEventBridge bridge;
    private final SceneAwareWorldPatchExecutor worldPatcher;

    public RuleEventListener(RuleEventBridge bridge, SceneAwareWorldPatchExecutor worldPatcher) {
        this.bridge = bridge;
        this.worldPatcher = worldPatcher;
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerInteract(PlayerInteractEvent event) {
        if (bridge == null) {
            return;
        }
        Player player = event.getPlayer();
        Action action = event.getAction();
        Block block = event.getClickedBlock();
        if (action == Action.RIGHT_CLICK_BLOCK || action == Action.LEFT_CLICK_BLOCK || action == Action.PHYSICAL) {
            Material material = block != null ? block.getType() : null;
            bridge.emitInteractBlock(player, action.name().toLowerCase(),
                    material,
                    block != null ? block.getLocation() : player.getLocation());
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlayerInteractEntity(PlayerInteractEntityEvent event) {
        if (bridge == null) {
            return;
        }
        Player player = event.getPlayer();
        Entity target = event.getRightClicked();
        bridge.emitInteractEntity(player, target, "interact");
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onEntityPickupItem(EntityPickupItemEvent event) {
        if (bridge == null) {
            return;
        }
        if (!(event.getEntity() instanceof Player)) {
            return;
        }

        Player player = (Player) event.getEntity();
        ItemStack stack = event.getItem().getItemStack();
        Location loc = event.getItem().getLocation();
        bridge.emitCollect(player, stack.getType(), stack.getAmount(), loc);

        // Phase 9: check item_collect TriggerZone rules and fire quest events
        String displayName = "";
        if (stack.hasItemMeta() && stack.getItemMeta() != null && stack.getItemMeta().hasDisplayName()) {
            displayName = ChatColor.stripColor(stack.getItemMeta().getDisplayName());
        }
        bridge.emitItemCollectTriggers(player, stack.getType(), displayName, loc);
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onPlayerQuit(PlayerQuitEvent event) {
        if (worldPatcher != null) {
            worldPatcher.cleanupDifficultyState(event.getPlayer());
        }
    }
}
