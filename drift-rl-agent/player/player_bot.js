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
const http = require('http')

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
  config = yaml.load(raw, { schema: yaml.DEFAULT_SCHEMA })
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
// ─── Drift API 状态缓存 ────────────────────────────────
const DRIFT_FETCH_INTERVAL = 5000  // 5 秒源一次
let driftStateCache = {}
let driftLastFetch = 0

/**
 * 从 Drift 后端获取当前玩家关卡状态（异步）
 */
function fetchDriftState() {
  const DRIFT_URL = config.drift?.backend_url || 'http://35.201.132.58:8000'
  const BOT_NAME_ENCODED = encodeURIComponent(BOT_NAME)
  const url = `${DRIFT_URL}/story/status/${BOT_NAME_ENCODED}`

  return new Promise((resolve) => {
    try {
      const parsedUrl = new URL(url)
      const options = {
        hostname: parsedUrl.hostname,
        port: parsedUrl.port || 80,
        path: parsedUrl.pathname + parsedUrl.search,
        method: 'GET',
        timeout: 3000,
      }
      const req = http.request(options, (res) => {
        let body = ''
        res.on('data', d => { body += d })
        res.on('end', () => {
          try {
            const data = JSON.parse(body)
            driftStateCache = data
            driftLastFetch = Date.now()
            resolve(data)
          } catch {
            resolve({})
          }
        })
      })
      req.on('error', () => resolve({}))
      req.on('timeout', () => { req.destroy(); resolve({}) })
      req.end()
    } catch {
      resolve({})
    }
  })
}

/**
 * 返回缓存的 Drift 状态（满足新鲜度则直接返回，否则重新获取）
 */
async function getDriftState() {
  if (Date.now() - driftLastFetch > DRIFT_FETCH_INTERVAL) {
    await fetchDriftState()
    // 从后端状态同步通关
    if (driftStateCache.status === 'completed' && !levelCompleted) {
      if (Date.now() - levelResetTime > 5000) {
        levelCompleted = true
        console.log('[Bot] 从 Drift 后端检测到通关')
      }
    }
  }
  return driftStateCache
}
// ─── 关卡事件检测标志 ──────────────────────────────────────
let levelCompleted = false
let lastDeathCause = null
let triggersCompleted = 0
let levelResetTime = 0  // BUG-A: 关卡重置时间戳，5s 保护窗防假阳性通关

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

    // R1: 预热 Drift 状态缓存
    getDriftState().catch(() => {})

    // 配置 pathfinder
    const mcData = require('minecraft-data')(bot.version)
    const defaultMove = new Movements(bot, mcData)
    bot.pathfinder.setMovements(defaultMove)
  })

  bot.on('message', (message) => {
    const text = message.toString()
    chatHistory.push({ time: Date.now(), text })
    if (chatHistory.length > MAX_CHAT_HISTORY) chatHistory.shift()

    // 检测关卡事件（BUG-A：删除宽泛的'关卡完成'匹配，增加 5s 时间保护）
    if ((text.includes('恭喜') && text.includes('通关'))
        || text.includes('level completed')
        || text.includes('挑战成功')) {
      if (Date.now() - levelResetTime > 5000) {
        levelCompleted = true
        console.log('[Bot] 检测到通关!')
      } else {
        console.log(`[Bot] 忽略早期通关消息 (${Date.now() - levelResetTime}ms): ${text}`)
      }
    }
    // 触发器检测（独立于通关检测）
    if (text.includes('触发') || text.includes('收集') || text.includes('完成任务')) {
      triggersCompleted++
    }

    console.log(`[Chat] ${text}`)
  })

  bot.on('death', () => {
    // 从最近聊天消息推断死因
    const recentChat = chatHistory.slice(-3).map(c => c.text).join(' ')
    if (recentChat.includes('坠落') || recentChat.includes('fell')) {
      lastDeathCause = 'fall_damage'
    } else if (recentChat.includes('溣水') || recentChat.includes('drowned')) {
      lastDeathCause = 'drowning'
    } else if (recentChat.includes('被') || recentChat.includes('slain')) {
      lastDeathCause = 'killed_by_mob'
    } else if (recentChat.includes('爆炸') || recentChat.includes('explode')) {
      lastDeathCause = 'explosion'
    } else if (recentChat.includes('岩浆') || recentChat.includes('lava')) {
      lastDeathCause = 'lava'
    } else {
      lastDeathCause = 'unknown'
    }
    console.log(`[Bot] 死亡 (原因: ${lastDeathCause})`)
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
      objectType: e.objectType || null,  // BUG-E: 区分掉落物 (e.g. "Item")
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
    position: [
      isNaN(pos.x) ? 0 : +pos.x.toFixed(2),
      isNaN(pos.y) ? 0 : +pos.y.toFixed(2),
      isNaN(pos.z) ? 0 : +pos.z.toFixed(2),
    ],
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
    level_completed: levelCompleted || (driftStateCache.status === 'completed'),
    triggers_completed: triggersCompleted,
    // R2: 读取后即刻清除，避免重复计入同一次死亡
    last_death_cause: (() => { const v = lastDeathCause; lastDeathCause = null; return v })(),
    // Drift 后端同步字段（由 getDriftState 异步刷新）
    current_difficulty: driftStateCache.current_difficulty || 0,
    triggers_remaining: driftStateCache.triggers_remaining || 0,
    total_triggers: driftStateCache.total_triggers || 0,
    quest_progress: driftStateCache.quest_progress || 0,
    time_limit: driftStateCache.time_limit || 0,
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
  levelResetTime = Date.now()  // BUG-A: 记录重置时间，防止关卡加载消息触发假阳性
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
      // 触发异步刷新 Drift 状态（不阀塞当前返回）
      if (Date.now() - driftLastFetch > DRIFT_FETCH_INTERVAL) {
        getDriftState().catch(() => {})
      }
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
      driftStateCache = {}  // 清除 Drift 状态缓存
      driftLastFetch = 0
      if (bot && botReady && cmd.level_id) {
        bot.chat(`/level ${cmd.level_id}`)
      }
      // 3 秒后预加载 Drift 状态
      setTimeout(() => getDriftState().catch(() => {}), 3000)
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
        const range = cmd.range || 1
        const timeout = cmd.timeout || 30000
        bot.pathfinder.setGoal(new GoalNear(cmd.x, cmd.y, cmd.z, range))
        // 超时后自动取消导航（防止 pathfinder 卡死）
        setTimeout(() => {
          try { if (bot && botReady) bot.pathfinder.setGoal(null) } catch (e) {}
        }, timeout)
      }
      return { ok: true }

    case 'collect_nearest':
      // 查找最近的指定类型物品并走向它
      if (bot && botReady && cmd.item_name) {
        const itemName = cmd.item_name.toLowerCase()
        const items = Object.values(bot.entities)
          .filter(e => {
            if (!e.position) return false
            const name = (e.name || e.displayName || '').toLowerCase()
            const objType = (e.objectType || '').toLowerCase()
            return (objType === 'item' || e.type === 'object')
              && (name.includes(itemName) || itemName.includes(name))
          })
          .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
        if (items.length > 0) {
          const target = items[0]
          const { GoalNear } = goals
          bot.pathfinder.setGoal(new GoalNear(target.position.x, target.position.y, target.position.z, 1))
          return { ok: true, found: true, target: target.name, distance: +bot.entity.position.distanceTo(target.position).toFixed(2) }
        }
        return { ok: true, found: false }
      }
      return { ok: true, found: false }

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
