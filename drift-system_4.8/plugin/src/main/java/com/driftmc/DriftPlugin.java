package com.driftmc;

import java.time.Duration;
import java.util.logging.Level;

import org.bukkit.Bukkit;
import org.bukkit.command.CommandExecutor;
import org.bukkit.command.PluginCommand;
import org.bukkit.plugin.java.JavaPlugin;

import com.driftmc.asyncaiflow.AsyncAIFlowClient;
import com.driftmc.backend.BackendClient;
import com.driftmc.cinematic.CinematicController;
import com.driftmc.commands.AdvanceCommand;
import com.driftmc.commands.CinematicCommand;
import com.driftmc.commands.CreateLevelCommand;
import com.driftmc.commands.DriftCommand;
import com.driftmc.commands.DriftLoadCommand;
import com.driftmc.commands.HeartMenuCommand;
import com.driftmc.commands.LevelCommand;
import com.driftmc.commands.LevelsCommand;
import com.driftmc.commands.MiniMapCommand;
import com.driftmc.commands.NpcMasterCommand;
import com.driftmc.commands.QuestLogCommand;
import com.driftmc.commands.RecommendCommand;
import com.driftmc.commands.SayToAICommand;
import com.driftmc.commands.StoryCreativeCommand;
import com.driftmc.commands.StoryRuntimeToolCommand;
import com.driftmc.commands.TalkCommand;
import com.driftmc.commands.TaskDebugCommand;
import com.driftmc.commands.custom.CmdSay;
import com.driftmc.commands.custom.CmdStoryNext;
import com.driftmc.commands.custom.CmdTeleport;
import com.driftmc.commands.custom.CmdTime;
import com.driftmc.dsl.DslExecutor;
import com.driftmc.dsl.DslRegistry;
import com.driftmc.exit.ExitIntentDetector;
import com.driftmc.hud.QuestLogHud;
import com.driftmc.hud.RecommendationHud;
import com.driftmc.hud.dialogue.ChoicePanel;
import com.driftmc.hud.dialogue.DialoguePanel;
import com.driftmc.intent.IntentRouter;
import com.driftmc.intent2.IntentDispatcher2;
import com.driftmc.intent2.IntentRouter2;
import com.driftmc.listeners.NearbyNPCListener;
import com.driftmc.listeners.PlayerChatListener;
import com.driftmc.listeners.PlayerJoinListener;
import com.driftmc.listeners.TutorialSafetyListener;
import com.driftmc.npc.NPCManager;
import com.driftmc.scene.RuleEventBridge;
import com.driftmc.scene.RuleEventListener;
import com.driftmc.scene.SceneAwareWorldPatchExecutor;
import com.driftmc.session.PlayerSessionManager;
import com.driftmc.story.StoryCreativeManager;
import com.driftmc.story.StoryManager;
import com.driftmc.tutorial.TutorialManager;
import com.driftmc.world.PayloadExecutorV1;
import com.driftmc.world.WorldPatchExecutor;

public class DriftPlugin extends JavaPlugin {

    private BackendClient backend;
    private AsyncAIFlowClient asyncAIFlowClient;
    private SceneAwareWorldPatchExecutor worldPatcher;
    private StoryManager storyManager;
    private StoryCreativeManager storyCreativeManager;
    private TutorialManager tutorialManager;
    private PlayerSessionManager sessionManager;
    private NPCManager npcManager;
    private DslRegistry dslRegistry;
    private DslExecutor dslExecutor;
    private IntentRouter intentRouter;
    private IntentRouter2 intentRouter2;
    private IntentDispatcher2 intentDispatcher2;
    private RuleEventBridge ruleEventBridge;
    private ExitIntentDetector exitIntentDetector;
    private RecommendationHud recommendationHud;
    private QuestLogHud questLogHud;
    private DialoguePanel dialoguePanel;
    private ChoicePanel choicePanel;
    private CinematicController cinematicController;
    private PayloadExecutorV1 payloadExecutor;
    private String taskDebugToken;

    @Override
    public void onEnable() {
        saveDefaultConfig();

        // 从 config.yml 读取后端地址
        String url = getConfig().getString("backend_url");
        if (url == null || url.isBlank()) {
            url = getConfig().getString("backend.baseUrl");
        }
        if (url == null || url.isBlank()) {
            url = "http://127.0.0.1:8000";
        }
        url = url.trim();
        if (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }

        getLogger().log(Level.INFO, "[DriftPlugin] 后端地址: {0}", url);

        int backendCallTimeoutSeconds = Math.max(20, getConfig().getInt("system.backend_call_timeout", 150));
        int backendConnectTimeoutSeconds = Math.max(3, getConfig().getInt("system.backend_connect_timeout", 10));
        int backendReadTimeoutSeconds = Math.max(20, getConfig().getInt("system.backend_read_timeout", 120));
        int backendWriteTimeoutSeconds = Math.max(20, getConfig().getInt("system.backend_write_timeout", 120));

        getLogger().log(
            Level.INFO,
            "[DriftPlugin] 后端超时(call/connect/read/write) = {0}/{1}/{2}/{3}s",
            new Object[] {
                backendCallTimeoutSeconds,
                backendConnectTimeoutSeconds,
                backendReadTimeoutSeconds,
                backendWriteTimeoutSeconds,
            });

        this.taskDebugToken = getConfig().getString("debug.task_token", "");

        // 初始化核心组件
        this.backend = new BackendClient(
            url,
            Duration.ofSeconds(backendCallTimeoutSeconds),
            Duration.ofSeconds(backendConnectTimeoutSeconds),
            Duration.ofSeconds(backendReadTimeoutSeconds),
            Duration.ofSeconds(backendWriteTimeoutSeconds));
        this.sessionManager = new PlayerSessionManager();
        this.storyManager = new StoryManager(this, backend);
        this.storyCreativeManager = new StoryCreativeManager(this);
        this.tutorialManager = new TutorialManager(this, backend, sessionManager);
        this.npcManager = new NPCManager(this);
        this.worldPatcher = new SceneAwareWorldPatchExecutor(this, npcManager);
        this.worldPatcher.setBackendClient(this.backend);
        this.payloadExecutor = new PayloadExecutorV1(this, backend);
        this.worldPatcher.attachTutorialStateMachine(tutorialManager.getStateMachine());
        this.worldPatcher.attachTutorialManager(tutorialManager);
        this.cinematicController = new CinematicController(this, worldPatcher);
        this.worldPatcher.attachCinematicController(cinematicController);
        this.tutorialManager.attachWorldPatcher(worldPatcher);
        this.tutorialManager.attachSceneLoader(worldPatcher.getSceneLoader());
        this.dslRegistry = DslRegistry.createDefault(worldPatcher, npcManager, backend);
        this.dslExecutor = new DslExecutor(dslRegistry);
        this.intentRouter = new IntentRouter(this, backend, dslExecutor, npcManager, worldPatcher, sessionManager);
        this.questLogHud = new QuestLogHud(this, backend);
        this.choicePanel = new ChoicePanel(this);
        this.dialoguePanel = new DialoguePanel(this, choicePanel);
        this.ruleEventBridge = new RuleEventBridge(this, backend, worldPatcher, questLogHud, dialoguePanel, choicePanel,
                sessionManager, tutorialManager);
        this.tutorialManager.attachRuleEventBridge(ruleEventBridge);
        this.choicePanel.setRuleEventBridge(ruleEventBridge);
        this.recommendationHud = new RecommendationHud(this, backend, storyManager);

        // 意图系统 (新版多意图管线)
        this.intentRouter2 = new IntentRouter2(this, backend);
        this.intentDispatcher2 = new IntentDispatcher2(
            (org.bukkit.plugin.Plugin) this,
            backend,
            (WorldPatchExecutor) worldPatcher,
            payloadExecutor);

        // AsyncAIFlow 集成：读取配置，注入 AsyncAIFlowClient
        String asyncAIFlowUrl = getConfig().getString("asyncaiflow.url", "http://127.0.0.1:8080");
        int difficultyThreshold = getConfig().getInt("asyncaiflow.difficulty_threshold", 3);
        String repoContext = getConfig().getString("asyncaiflow.repo_context", "drift-system");
        this.asyncAIFlowClient = new AsyncAIFlowClient(asyncAIFlowUrl);
        this.intentDispatcher2.setAsyncAIFlowClient(asyncAIFlowClient);
        this.intentDispatcher2.setAsyncAIFlowDifficultyThreshold(difficultyThreshold);
        this.intentDispatcher2.setDriftRepoContext(repoContext);
        getLogger().log(Level.INFO, "[DriftPlugin] AsyncAIFlow 地址: {0}, 难度阈值: {1}",
                new Object[]{asyncAIFlowUrl, difficultyThreshold});

        this.intentDispatcher2.setTutorialManager(tutorialManager);
        this.intentDispatcher2.setQuestLogHud(questLogHud);
        this.intentDispatcher2.setDialoguePanel(dialoguePanel);
        this.intentDispatcher2.setChoicePanel(choicePanel);
        this.exitIntentDetector = new ExitIntentDetector(this, backend, worldPatcher, recommendationHud, questLogHud);

        // 注册聊天监听器（核心：自然语言驱动）
        Bukkit.getPluginManager().registerEvents(
                new PlayerChatListener(this, intentRouter2, intentDispatcher2, tutorialManager, ruleEventBridge,
                        exitIntentDetector, choicePanel),
                this);

        // 注册玩家加入/离开监听器（教学系统）
        Bukkit.getPluginManager().registerEvents(
                new PlayerJoinListener(this, tutorialManager),
                this);

        // 注册剧情创造管理器监听器
        Bukkit.getPluginManager().registerEvents(storyCreativeManager, this);

        // 注册通用规则事件监听器
        Bukkit.getPluginManager().registerEvents(new RuleEventListener(ruleEventBridge), this);

        // 注册 NPC 生命周期监听
        Bukkit.getPluginManager().registerEvents(npcManager, this);

        // 注册 NPC 临近监听（触发老版 IntentRouter）
        Bukkit.getPluginManager().registerEvents(
                new NearbyNPCListener(this, npcManager, intentRouter, ruleEventBridge, sessionManager), this);

        // 注册教学安全守护（教程模式专用）
        Bukkit.getPluginManager().registerEvents(new TutorialSafetyListener(this, worldPatcher.getSceneLoader()), this);

        // 注册命令
        registerCommand("drift", new DriftCommand(backend, storyManager, worldPatcher, tutorialManager));
        registerCommand("storycreative", new StoryCreativeCommand(this, storyCreativeManager, storyManager));
        registerCommand("minimap", new MiniMapCommand(this, url));
        registerCommand("talk", new TalkCommand(intentRouter, ruleEventBridge));
        registerCommand("saytoai", new SayToAICommand(this, backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("advance", new AdvanceCommand(this, backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("storynext", new CmdStoryNext(this, backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("heartmenu", new HeartMenuCommand(backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("level", new LevelCommand(this, backend, intentRouter, worldPatcher, payloadExecutor, sessionManager));
        registerCommand("levels", new LevelsCommand(backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("npc", new NpcMasterCommand(npcManager));
        registerCommand("tp2", new CmdTeleport(backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("time2", new CmdTime(backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("sayc", new CmdSay(backend, intentRouter, worldPatcher, sessionManager));
        registerCommand("recommend", new RecommendCommand(recommendationHud));
        registerCommand("questlog", new QuestLogCommand(questLogHud));
        registerCommand("cinematic", new CinematicCommand(cinematicController));
        registerCommand("taskdebug", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.TASKS));
        registerCommand("worldstate", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.WORLDSTATE));
        registerCommand("leveldebug", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.LEVELDEBUG));
        registerCommand("eventdebug", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.EVENTDEBUG));
        registerCommand("spawnfragment", new StoryRuntimeToolCommand(this, backend, worldPatcher, StoryRuntimeToolCommand.Mode.SPAWN_FRAGMENT));
        registerCommand("storyreset", new StoryRuntimeToolCommand(this, backend, worldPatcher, StoryRuntimeToolCommand.Mode.STORY_RESET));
        CreateLevelCommand createLevelCommand = new CreateLevelCommand(this, backend, worldPatcher, payloadExecutor);
        createLevelCommand.setIntentRouter2(intentRouter2);
        createLevelCommand.setIntentDispatcher2(intentDispatcher2);
        registerCommand("create", createLevelCommand);
        registerCommand("driftload", new DriftLoadCommand(this, backend, worldPatcher));
        registerCommand("debugscene", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.SCENE));
        registerCommand("debuginventory", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.INVENTORY));
        registerCommand("predictscene", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.PREDICTION));
        registerCommand("explainscene", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.EXPLAIN));
        registerCommand("debugpatch", new TaskDebugCommand(this, backend, taskDebugToken, TaskDebugCommand.ViewMode.PATCH));

        getLogger().info("======================================");
        getLogger().info("   DriftSystem / 心悦宇宙");
        getLogger().info("   完全自然语言驱动的AI冒险系统");
        getLogger().info("======================================");
        getLogger().log(Level.INFO, "✓ 后端连接: {0}", url);
        getLogger().info("✓ 自然语言解析: 已启用");
        getLogger().info("✓ 世界动态渲染: 已启用");
        getLogger().info("✓ 剧情引擎: 已就绪");
        getLogger().info("✓ DSL注入: 支持");
        getLogger().info("✓ 新手教学: 已启用");
        getLogger().info("======================================");
        getLogger().info("玩家可以直接在聊天中说话来推进剧情！");
        getLogger().info("新玩家将自动进入教学系统！");
        getLogger().info("======================================");
    }

    @Override
    public void onDisable() {
        // 清理创造模式会话
        if (storyCreativeManager != null) {
            storyCreativeManager.cleanup();
        }

        if (tutorialManager != null) {
            Bukkit.getOnlinePlayers().forEach(tutorialManager::cleanupPlayer);
        }

        if (worldPatcher != null) {
            worldPatcher.shutdown();
        }
        if (payloadExecutor != null) {
            payloadExecutor.shutdown();
        }

        getLogger().info("======================================");
        getLogger().info("   DriftSystem 已关闭");
        getLogger().info("======================================");
    }

    private void registerCommand(String name, CommandExecutor executor) {
        PluginCommand command = getCommand(name);
        if (command == null) {
            getLogger().log(Level.SEVERE, "plugin.yml 未定义命令: {0}", name);
            return;
        }
        command.setExecutor(executor);
    }

    public BackendClient getBackend() {
        return backend;
    }

    public StoryManager getStoryManager() {
        return storyManager;
    }

    public WorldPatchExecutor getWorldPatcher() {
        return worldPatcher;
    }

    public TutorialManager getTutorialManager() {
        return tutorialManager;
    }

    public CinematicController getCinematicController() {
        return cinematicController;
    }
}
