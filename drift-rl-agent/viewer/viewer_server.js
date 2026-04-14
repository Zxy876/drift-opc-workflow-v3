/**
 * Drift RL Agent Viewer — prismarine-viewer 服务
 *
 * 在浏览器中实时查看 Bot 的第一人称视角。
 * 启动：node viewer/viewer_server.js
 * 打开：http://localhost:3007
 */

const mineflayer = require('mineflayer')
const { mineflayer: mineflayerViewer } = require('prismarine-viewer')

// 配置（支持环境变量覆盖）
const MC_HOST = process.env.MC_HOST || '35.201.132.58'
const MC_PORT = parseInt(process.env.MC_PORT || '25565')
const BOT_NAME = process.env.BOT_NAME || 'DriftViewer'
const VIEWER_PORT = parseInt(process.env.VIEWER_PORT || '3007')

console.log(`[Viewer] 连接 MC 服务器 ${MC_HOST}:${MC_PORT}`)

const bot = mineflayer.createBot({
  host: MC_HOST,
  port: MC_PORT,
  username: BOT_NAME,
})

bot.once('spawn', () => {
  console.log(`[Viewer] Bot 已生成，启动 viewer 在端口 ${VIEWER_PORT}`)

  mineflayerViewer(bot, {
    port: VIEWER_PORT,
    firstPerson: true,
  })

  console.log(`[Viewer] 打开浏览器访问 http://localhost:${VIEWER_PORT}`)
})

bot.on('error', (err) => {
  console.error(`[Viewer] 错误: ${err.message}`)
})

bot.on('end', () => {
  console.log('[Viewer] 连接断开')
  process.exit(1)
})

// 优雅退出
process.on('SIGINT', () => {
  console.log('\n[Viewer] 正在退出...')
  bot.quit()
  process.exit(0)
})
