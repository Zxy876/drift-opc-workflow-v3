# Drift VM 游戏前 CI/CD 实战 Runbook

适用场景：
- GCP VM 上运行 Drift 全栈（AsyncAIFlow + Backend + Workers + Minecraft）
- 目标是玩家进服前把关键链路全部拉到 PASS

本文基于一次真实修复过程整理，强调可复制、可落地、可回归验证。

## 1. 目标状态（Definition of Done）

以下必须为 PASS：
- 代码同步到 `origin/main`
- MySQL + `asyncaiflow` 数据库可用
- Redis 可用
- AsyncAIFlow `:8080` 可访问
- Drift Backend `:8000` 可访问
- Minecraft `:25565` 监听
- Workers 全量 active（Python 9/9，Java 3/3）
- 冒烟测试通过（`/levels`、`/workflows`、MC TCP）

## 2. 本次修复中的真实故障画像

- VM 上 AsyncAIFlow JAR 缺失（`target/asyncaiflow-0.1.0-SNAPSHOT.jar` 不存在）
- VM 上缺少 `mvn`，导致无法构建 JAR
- 预检脚本早期误报 MySQL/Redis 失败：根因是缺少客户端工具（`mysqladmin/mysql/redis-cli`），不是服务本身挂掉
- 插件 JAR 检查失败：目录里有插件，但命名不匹配 `DriftSystem*.jar`
- MC 重启后需要等待一小段冷启动时间，立即检测可能误判 25565 未监听
- AsyncAIFlow 冒烟不应强依赖特定 JSON 字段，建议先以可达性为准

## 3. 一次性修复步骤（按顺序执行）

在本机执行（要求已配置 gcloud）：

```bash
# 0) 进入仓库
cd /path/to/drift-opc-workflow-v3

# 1) 确认目标 VM
# 示例实例：drift-demo-vm (asia-east1-b)
gcloud compute instances list --format='table(name,zone,status,EXTERNAL_IP)'

# 2) 代码同步到 VM
# 进入 VM 后：
#   cd ~/drift-opc-workflow-v3
#   git fetch origin
#   git pull origin main

# 3) 安装缺失基础工具（只需一次）
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command '
  sudo apt-get update -y >/dev/null &&
  sudo apt-get install -y maven mysql-client redis-tools >/dev/null
' --quiet

# 4) 构建 AsyncAIFlow JAR
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command '
  cd ~/drift-opc-workflow-v3/AsyncAIFlow_4.8 &&
  mvn -q -DskipTests package &&
  ls -l target/asyncaiflow-0.1.0-SNAPSHOT.jar
' --quiet

# 5) 确保数据库存在
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command '
  mysql -h 127.0.0.1 -uroot -proot -e "CREATE DATABASE IF NOT EXISTS asyncaiflow;"
' --quiet

# 6) 重启全栈服务
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command '
  sudo systemctl daemon-reload &&
  sudo systemctl restart drift-asyncaiflow.service drift-backend.service &&
  sudo systemctl restart drift-java-worker@repository.service drift-java-worker@gpt.service drift-java-worker@git.service &&
  sudo systemctl restart drift-python-worker@drift_trigger.service drift-python-worker@drift_web_search.service drift-python-worker@drift_plan.service &&
  sudo systemctl restart drift-python-worker@drift_code.service drift-python-worker@drift_review.service drift-python-worker@drift_test.service &&
  sudo systemctl restart drift-python-worker@drift_deploy.service drift-python-worker@drift_git_push.service drift-python-worker@drift_refresh.service &&
  sudo systemctl restart drift-minecraft.service
' --quiet

# 7) 构建并部署插件（确保命名可被预检匹配）
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command '
  cd ~/drift-opc-workflow-v3/drift-system_4.8/plugin &&
  mvn -q -DskipTests package &&
  JAR=$(ls -1 target/*.jar | grep -vE "(sources|javadoc|original)" | head -n1) &&
  sudo cp "$JAR" /opt/drift-demo/mc-server/plugins/DriftSystem-runtime.jar &&
  ls -l /opt/drift-demo/mc-server/plugins/DriftSystem*.jar &&
  sudo systemctl restart drift-minecraft.service
' --quiet
```

## 4. 标准预检脚本（VM 内执行）

建议保留一个固定脚本，例如 `/tmp/vm_precheck_v2.sh`，覆盖以下检测：
- 代码同步
- MySQL/DB/Redis
- AsyncAIFlow/Backend/MC 端口和接口
- 插件 JAR 与配置
- Workers 计数
- API Key 存在性
- 冒烟测试

执行：

```bash
gcloud compute ssh drift-demo-vm --zone asia-east1-b --command 'bash /tmp/vm_precheck_v2.sh' --quiet
```

判定标准：
- 必须全 PASS：核心服务、端口、Workers、冒烟
- 可接受 WARN：配置地址提示、mock 开关提示

## 5. 常用排障命令

```bash
# 关键服务状态
sudo systemctl status drift-asyncaiflow.service --no-pager -n 60
sudo systemctl status drift-backend.service --no-pager -n 60
sudo systemctl status drift-minecraft.service --no-pager -n 80

# 关键日志
sudo journalctl -u drift-asyncaiflow.service -f
sudo journalctl -u drift-backend.service -f
sudo journalctl -u drift-minecraft.service -f

# 端口验证
ss -tlnp | egrep ':3306|:6379|:8000|:8080|:25565'
```

## 6. 经验结论（给后续维护者）

- 先修“构建产物缺失”和“基础工具缺失”，再做服务重启，效率最高
- 预检脚本不要把“命令不存在”误判成“服务挂了”
- MC 服务重启后要有 warm-up 等待，避免端口瞬时误判
- AsyncAIFlow 冒烟建议优先检测接口可达，不要死绑某个字段名
- 插件 JAR 建议统一命名 `DriftSystem*.jar`，避免运维检查和人工排查歧义

## 7. 交接清单（交班时必须给）

- 当前 commit hash
- `systemctl is-active` 全量截图或文本
- 预检脚本最后一次输出
- 若有 WARN，写清是否可接受及原因

## 8. 7x24 常驻化运维

### 开机自启
所有服务通过 `drift-stack.target` 统一管理：
- `systemctl enable drift-stack.target` -> 开机时全栈自启
- `systemctl start drift-stack.target` -> 手动一键拉起全栈

### 健康检查
- `drift-healthcheck.timer` 每 5 分钟运行一次
- 自动检测 4 个核心端口 + 13 个 worker 进程
- 发现异常自动重启对应服务
- 查看日志：`journalctl -u drift-healthcheck.service --since "1 hour ago"`

### 代码热更新
- `drift-auto-update.timer` 每 10 分钟检查 origin/main
- 只重启实际变更的服务（后端/面板/worker）
- 查看日志：`journalctl -u drift-auto-update.service --since "1 hour ago"`

### 端口清单
| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | drift-backend | Drift Backend API |
| 8080 | drift-asyncaiflow | AsyncAIFlow Runtime |
| 8888 | drift-panel | Experience Panel |
| 25565 | drift-minecraft | Minecraft Server |
| 3306 | mysql | MySQL |
| 6379 | redis | Redis |

### 一键诊断
```bash
# 全栈状态
systemctl list-units 'drift-*' --no-pager

# 最近健康检查
journalctl -u drift-healthcheck.service -n 20 --no-pager

# 最近自动更新
journalctl -u drift-auto-update.service -n 20 --no-pager

# 手动触发健康检查
systemctl start drift-healthcheck.service && journalctl -u drift-healthcheck.service -n 30 --no-pager
```
