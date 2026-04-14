/**
 * Drift RL Agent — Mineflayer Bot + TCP Bridge
 *
 * 功能：
 * 1. 连接 MC 服务器（35.201.132.58:25565）
 * 2. 暴露 TCP 接口（端口 9999）供 Python RL 环境控制
 * 3. 支持的命令：get_state / action / command / reset / ping
 *
 * 启动：node player/player_bot.js
 */

const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const net = require('net')
const fs = require('fs')
const path = require('path')
const yaml = require('js-yaml')

// ─── 加载配置 ─────────────────────────────────────────────
let config = {
  drift: {
    mc_server: { host: '35.201.132.58', port: 25565 },
    backend_url: 'http://35.201.132.58:8000',
  },
  bot: { username: 'DriftRLAgent', bridge_port: 9999 },
}
try {
  const cfgPath = path.join(__dirname, '..', 'configs', 'drift_servers.yaml')
  const raw = fs.readFileSync(cfgPath, 'utf8')
  config = yaml.load(raw)
  console.log('[Config] 已加载配置文件')
} catch (err) {
  console.log(`[Config] 使用默认配置 (${err.message})`)
}

const MC_HOST = config.drift?.mc_server?.host || '35.201.132.58'
const MC_PORT = config.drift?.mc_server?.port || 25565
const BOT_NAME = config.bot?.username || 'DriftRLAgent'
const BRIDGE_PORT = config.bot?.bridge_port || 9999

// ─── 聊天历史 ──────────────────────────────────────────────
const chatHistory = []
const MAX_CHAT_HISTORY = 100

// ─── 关卡事件检测标志 ──────────────────────────────────────
let levelCompleted = false
let lastDeathCause = null
let triggersCompleted = 0

// ─── 创建 Bot ──────────────────────────────────────────────
let bot = null
let botReady = false

function createBot() {
  console.log(`[Bot] 连接 MC 服务器 ${MC_HOST}:${MC_PORT} (用户名: ${BOT_NAME})`)

  bot = mineflayer.createBot({
    host: MC_HOST,
    port: MC_PORT,
    username: BOT_NAME,
    hideErrors: false,
  })

  bot.loadPlugin(pathfinder)

  bot.on('login', () => {
    console.log('[Bot] 登录成功')
  })

  bot.on('spawn', () => {
    botReady = true
    console.log('[Bot] 已生成，准备接收命令')

    // 配置 pathfinder
    const mcData = require('minecraft-data')(bot.version)
    const defaultMove = new Movements(bot, mcData)
    bot.pathfinder.setMovements(defaultMove)
  })

  bot.on('message', (message) => {
    const text = message.toString()
    chatHistory.push({ time: Date.now(), text })
    if (chatHistory.length > MAX_CHAT_HISTORY) chatHistory.shift()

    // 检测关卡事件
    if (text.includes('恭喜') || text.includes('通关') || text.includes('完成')) {
      levelCompleted = true
    }
    if (text.includes('触发') || text.includes('收集') || text.includes('完成任务')) {
      triggersCompleted++
    }

    console.log(`[Chat] ${text}`)
  })

  bot.on('death', () => {
    lastDeathCause = 'death'
    console.log('[Bot] 死亡')
  })

  bot.on('health', () => {
    // 血量变化时记录（可扩展）
  })

  bot.on('kicked', (reason) => {
    console.log(`[Bot] 被踢出: ${reason}`)
    botReady = false
  })

  bot.on('error', (err) => {
    console.error(`[Bot] 错误: ${err.message}`)
  })

  bot.on('end', () => {
    console.log('[Bot] 连接断开，3 秒后重连...')
    botReady = false
    setTimeout(createBot, 3000)
  })
}

// ─── 获取当前状态 ────────────────────────────────────────
function getState() {
  if (!bot || !botReady || !bot.entity) {
    return { error: 'bot_not_ready' }
  }

  const pos = bot.entity.position

  // 附近实体
  const nearbyEntities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position && pos.distanceTo(e.position) < 20)
    .sort((a, b) => pos.distanceTo(a.position) - pos.distanceTo(b.position))
    .slice(0, 10)
    .map(e => ({
      type: e.type,
      name: e.name || e.displayName || 'unknown',
      rel_x: +(e.position.x - pos.x).toFixed(2),
      rel_y: +(e.position.y - pos.y).toFixed(2),
      rel_z: +(e.position.z - pos.z).toFixed(2),
      health: e.health || 0,
    }))

  // 附近方块（简化：只检测关键位置）
  const nearbyBlocks = []
  for (let dx = -2; dx <= 2; dx++) {
    for (let dy = -1; dy <= 2; dy++) {
      for (let dz = -2; dz <= 2; dz++) {
        const block = bot.blockAt(pos.offset(dx, dy, dz))
        if (block && block.name !== 'air') {
          nearbyBlocks.push({ name: block.name, x: dx, y: dy, z: dz })
        }
      }
    }
  }

  // 背包
  const inventory = bot.inventory.items().map(i => ({
    name: i.name,
    count: i.count,
    slot: i.slot,
  }))

  return {
    position: [+pos.x.toFixed(2), +pos.y.toFixed(2), +pos.z.toFixed(2)],
    health: bot.health,
    food: bot.food,
    on_ground: bot.entity.onGround,
    yaw: +bot.entity.yaw.toFixed(4),
    pitch: +bot.entity.pitch.toFixed(4),
    velocity: [
      +bot.entity.velocity.x.toFixed(4),
      +bot.entity.velocity.y.toFixed(4),
      +bot.entity.velocity.z.toFixed(4),
    ],
    nearby_entities: nearbyEntities,
    nearby_blocks: nearbyBlocks.slice(0, 50),
    inventory,
    chat_history: chatHistory.slice(-10),
    time_of_day: bot.time?.timeOfDay || 0,
    is_raining: bot.isRaining,
    // Drift 特有事件
    level_completed: levelCompleted,
    triggers_completed: triggersCompleted,
    last_death_cause: lastDeathCause,
  }
}

// ─── 执行动作 ──────────────────────────────────────────────
function executeAction(action) {
  if (!bot || !botReady) return

  // 移动控制
  if (action.move_forward !== undefined) {
    bot.setControlState('forward', action.move_forward === 1)
    bot.setControlState('back', action.move_forward === 2)
  }
  if (action.move_strafe !== undefined) {
    bot.setControlState('left', action.move_strafe === 2)
    bot.setControlState('right', action.move_strafe === 1)
  }
  if (action.jump !== undefined) {
    bot.setControlState('jump', action.jump === 1)
  }
  if (action.sprint !== undefined) {
    bot.setControlState('sprint', action.sprint === 1)
  }

  // 攻击最近目标
  if (action.attack === 1) {
    const entityPos = bot.entity.position
    const target = bot.nearestEntity(e =>
      e.type === 'mob' || e.type === 'hostile' || e.type === 'animal'
    )
    if (target && entityPos.distanceTo(target.position) < 4) {
      bot.attack(target)
    }
  }

  // 使用物品
  if (action.use_item === 1) {
    bot.activateItem()
  }

  // 视角旋转
  if (action.look_delta) {
    const [dyaw, dpitch] = action.look_delta
    const newYaw = bot.entity.yaw + dyaw * 0.1
    const newPitch = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, bot.entity.pitch + dpitch * 0.1))
    bot.look(newYaw, newPitch, true)
  }
}

// ─── 重置关卡状态 ──────────────────────────────────────────
function resetLevelFlags() {
  levelCompleted = false
  lastDeathCause = null
  triggersCompleted = 0
  chatHistory.length = 0
}

// ─── TCP Bridge 服务器 ──────────────────────────────────────
const server = net.createServer((conn) => {
  console.log('[Bridge] Python 客户端已连接')

  let buffer = ''

  conn.on('data', (data) => {
    buffer += data.toString()

    // 按换行符分割消息（支持批量命令）
    let newlineIdx
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx)
      buffer = buffer.slice(newlineIdx + 1)

      try {
        const cmd = JSON.parse(line)
        const response = handleCommand(cmd)
        conn.write(JSON.stringify(response) + '\n')
      } catch (err) {
        conn.write(JSON.stringify({ error: err.message }) + '\n')
      }
    }
  })

  conn.on('end', () => {
    console.log('[Bridge] Python 客户端断开')
  })

  conn.on('error', (err) => {
    console.error(`[Bridge] 连接错误: ${err.message}`)
  })
})

function handleCommand(cmd) {
  switch (cmd.type) {
    case 'ping':
      return { ok: true, ready: botReady, time: Date.now() }

    case 'get_state':
      return getState()

    case 'action':
      executeAction(cmd.action || {})
      return { ok: true }

    case 'command':
      // 执行 MC 聊天命令（如 /level, /easy, /advance, /create 等）
      if (bot && botReady && cmd.text) {
        bot.chat(cmd.text)
      }
      return { ok: true, command: cmd.text }

    case 'reset':
      // 重置：加载指定关卡，清除本局状态标志
      resetLevelFlags()
      if (bot && botReady && cmd.level_id) {
        bot.chat(`/level ${cmd.level_id}`)
      }
      return { ok: true, level_id: cmd.level_id }

    case 'look_at':
      // 看向指定坐标
      if (bot && botReady && cmd.x !== undefined) {
        bot.lookAt(new Vec3(cmd.x, cmd.y, cmd.z))
      }
      return { ok: true }

    case 'navigate_to':
      // 用 pathfinder 导航到指定坐标
      if (bot && botReady && cmd.x !== undefined) {
        const { GoalNear } = goals
        bot.pathfinder.setGoal(new GoalNear(cmd.x, cmd.y, cmd.z, 1))
      }
      return { ok: true }

    case 'stop_all':
      // 停止所有动作
      if (bot && botReady) {
        bot.clearControlStates()
        bot.pathfinder.setGoal(null)
      }
      return { ok: true }

    default:
      return { error: `unknown command type: ${cmd.type}` }
  }
}

server.listen(BRIDGE_PORT, '0.0.0.0', () => {
  console.log(`[Bridge] TCP Bridge 监听端口 ${BRIDGE_PORT}`)
  console.log('[Bridge] 等待 Python RL 环境连接...')
})

// ─── 启动 Bot ──────────────────────────────────────────────
createBot()

// 优雅退出
process.on('SIGINT', () => {
  console.log('\n[Bot] 正在退出...')
  if (bot) bot.quit()
  server.close()
  process.exit(0)
})
