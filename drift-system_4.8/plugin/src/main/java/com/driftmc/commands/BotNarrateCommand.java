package com.driftmc.commands;

import java.util.logging.Level;

import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;

/**
 * /botnarrate <CRITICAL|ACTION|INFO|DEBUG> <message>
 *
 * DriftAgent 行为广播命令。
 */
public class BotNarrateCommand implements CommandExecutor {

    private static final String BOT_PREFIX = "§6[DriftAgent] §r";
    private boolean enabled = true;

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length < 2) {
            return false;
        }

        String level = args[0].toUpperCase();
        StringBuilder msgBuilder = new StringBuilder();
        for (int i = 1; i < args.length; i++) {
            if (i > 1) {
                msgBuilder.append(' ');
            }
            msgBuilder.append(args[i]);
        }
        String message = msgBuilder.toString();

        if (!enabled) {
            return true;
        }

        for (Player player : Bukkit.getOnlinePlayers()) {
            switch (level) {
                case "CRITICAL":
                    player.sendTitle(
                            ChatColor.GOLD + "[DriftAgent]",
                            ChatColor.WHITE + message,
                            10,
                            60,
                            20);
                    player.sendMessage(BOT_PREFIX + message);
                    break;
                case "ACTION":
                    player.sendActionBar(Component.text("[DriftAgent] " + message, NamedTextColor.GOLD));
                    player.sendMessage(BOT_PREFIX + "§7" + message);
                    break;
                case "INFO":
                    player.sendMessage(BOT_PREFIX + message);
                    break;
                case "DEBUG":
                    player.sendActionBar(Component.text("[DriftAgent] " + message, NamedTextColor.GRAY));
                    break;
                default:
                    player.sendMessage(BOT_PREFIX + message);
                    break;
            }
        }

        Bukkit.getLogger().log(Level.INFO, "[BotNarrate/{0}] {1}", new Object[] { level, message });
        return true;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public boolean isEnabled() {
        return this.enabled;
    }
}
