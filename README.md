# collaborating-with-claude

> 在 **anyrouter 中转 + cc-switch（ccs）** 环境下，让 Claude Code 的多 agent 编排真正跑得起来。
> 原生 `Workflow` / `Agent` / `Task` 子 agent 用 **opus** 时在这套上游下**必定 429 冷启动死**——本工具把它换成独立 `claude -p` 进程，绕开它。

如果你也是「Claude Code 走 cc-switch 切换 + anyrouter（或同类）中转」这套，并且发现一开 Workflow / 子 agent 就卡到 `429 Service Unavailable`，那这个工具就是为你写的。

---

## 遇到问题

### 一直 `attempt` / 429，连不上？

anyrouter 这类中转对**新对话首次接入**有冷启动：刚连上时请求会被反复拒（不停 `attempt` / `429`），要反复重试才通。

开始干活前，先在主会话里**反复发 `hi` 或任意短文本戳几次**，直到它正常应答（主会话接入成功）。

> ⚠️ 这个冷启动是**主会话自身**的接入问题，和「子 agent 为什么有的能跑、有的 429 死」是**两回事**——后者由「模型 × 路径」决定（见下「它解决什么问题」），且主会话热不热都不改变它：实测主会话已热透时，原生 opus 子 agent 照样死。

### MCP 起不来（`EPERM` / `Failed to connect`）

MCP server 通常靠 `npx -y ...@latest` 启动，会往 npm cache 写包。如果你的 **Node 装在受限目录**（普通终端无写权限），写 cache 会报 `EPERM`，MCP 随之 `Failed to connect`。把 npm cache 指到当前用户**可写**的目录即可，普通终端就能拉包：

```bash
npm config set cache <你账户下的可写路径>    # 例：C:\Users\<you>\.npm-cache
npm config get cache                           # 确认指向可写盘
claude mcp list                                # 应显示 ✔ Connected
```

排错锚点：以后 npm / npx / MCP 报 `EPERM` 或 `Failed to connect`，先查 `npm config get cache` 是否指向可写位置。

### `/resume` 被一堆 `claude -p` 子 agent 会话刷屏

每个子 agent 都是一次独立 `claude -p`，会在「**子进程 CWD** 派生的项目目录」下持久化一个 session 文件。若从你的主目录发起编排，这些一次性子 agent 会话就全堆进主项目的 `/resume` 列表，且不会自动清理。

本工具默认把子 agent 的 CWD 钉到专用目录 `~/.claude/.bridge-cwd`，让这些 session 落进一个**没有任何交互 `/resume` 会去读**的隔离 `projects/<hash>` 文件夹；子 agent 仍通过 `--add-dir`（spec 里的 `cd`）访问目标项目，prompt 用绝对路径即可。要换位置：bridge 传 `--session-cwd <dir>`，或 spec 里设 `"session_cwd":"<dir>"`（须是稳定目录，否则 `--resume` 找不回旧 session）。

> 注：你**自己**在主会话里发的 `hi` / `1` 预热戳（见上）是**交互式真会话**，不归本工具管，会照常出现在 `/resume`——那是另一回事。

---

## 它解决什么问题

在 anyrouter + cc-switch 下**实测**：子 agent 能否跑起来，由 **「模型 × 调用路径」** 决定——与思考强度（effort）、并发数、请求大小都无关。

| 调用路径 | haiku | opus | sonnet |
|---|---|---|---|
| **原生 Workflow / Agent** | ✅ 能用 | ❌ **429 死**（熬 ~200s） | ❌ 死（真冷） |
| **本工具（独立 `claude -p`）** | ✅ | ✅ **能用**（effort low→max + 并发到 8 全过） | ❌ 死（真冷） |

- **haiku**：两条路都能用——这种情况你甚至不需要本工具，原生 `Workflow` 跑 haiku 子 agent 就行。
- **opus**：原生子 agent **必 429 死**；换成本工具的独立 `claude -p` 进程就**完全可用**（low→max 思考、并发到 8 实测全过）。**这是本工具不可替代的地方。**
- **sonnet**：这个 anyrouter 渠道把 sonnet 当冷的，原生和 `claude -p` 都救不了（重试 2×210s 仍 429）——避开，用 haiku/opus。

> **机理**：opus 原生死，与模型本身、`[1m]` 路由、请求大小、session 都无关（已逐一实验排除——`claude -p` 带 `[1m]` 同 ID、灌 66k 大请求、用全新随机 session，opus 照样活）。唯一变量是 **Claude Code 原生子 agent 的请求路径**在 anyrouter 上对 opus 触发 429，而独立 `claude -p` 进程的请求路径不触发。精确到哪个请求字段没抓包确证，但结论**可操作且稳定**：opus 子 agent 走 `claude -p` 就能用。
>
> 这些都是 **anyrouter 中转侧**的行为，不是 Claude Code / cc-switch / 你机器的问题；官方直连应不受影响。

---

## 前置要求

| 依赖 | 说明 |
|---|---|
| **Claude Code CLI** (`claude`) | 工具通过 `claude -p` 子进程工作。**版本敏感**——见下方「版本与脆性」。 |
| **cc-switch** | 提供本地代理 + 注入认证环境变量（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`）。 |
| **anyrouter（或同类中转）** | cc-switch 背后的实际上游中转商。 |
| **Python 3.8+** | 跑桥与编排脚本，仅用标准库（无第三方依赖）。 |
| **主会话可用** | 主会话首次接入要戳热（见「遇到问题」）才能发指令。注：本工具的 `claude -p` 子进程**独立**重试冷启动，不依赖主会话保活。 |

---

## 安装

把本 skill 放到 Claude Code 的 skills 目录（或任意位置后建软链）：

```bash
# 示例：克隆到用户 skills 目录
git clone https://github.com/moyunliuyin/collaborating-with-claude.git ~/.claude/skills/collaborating-with-claude
```

无需 `pip install`——脚本只用 Python 标准库。

---

## 快速自测

确认桥与代理都通（期望 `ok_count=2`）：

```bash
python ~/.claude/skills/collaborating-with-claude/scripts/orchestrate.py \
  --inline '{"mode":"parallel","cd":".","model":"haiku",
             "agents":[{"prompt":"Reply one word: alpha"},
                       {"prompt":"Reply one word: beta"}]}'
```

跑之前先在主会话里把代理戳热（发几个 `hi` 直到主会话正常应答，见「遇到问题」）。

---

## 用法

写一个 JSON spec，**后台运行、不设 timeout**（冷启动单次尝试可能跑到 ~210s）：

```bash
python scripts/orchestrate.py --spec /tmp/spec.json
# 或：  --inline '<json>'   |   echo '<json>' | python orchestrate.py
```

stdout 是聚合后的 JSON：
`{mode, cap, ok_count, total, results:[{label, success, agent_messages, error, SESSION_ID}]}`

### Spec 格式

```jsonc
{
  "mode": "agent" | "parallel" | "pipeline",   // 默认 parallel
  "cd":   "E:/code/proj",          // 默认工作目录；每个 agent/stage 可覆盖
  "model":"opus" | "haiku",        // 走 claude -p 时均可用。sonnet 该渠道冷→429。省略=用中转默认
  "mcp":  "", "cap": 16,           // 可选：MCP 配置串/路径；并发上限
  "cold_models":"sonnet",          // 可选：你中转上的「冷模型」集（快速失败）；留空禁用
  "block_tool":"",                 // 可选：防递归的 --disallowedTools 模式
  "effort":"",                     // 可选：思考强度 low/medium/high/xhigh/max；留空=CLI 默认
  "session_cwd":"",                // 可选：子 agent 会话存储用的专用 CWD（隔离 /resume）；留空=~/.claude/.bridge-cwd
  "retries": 3, "timeout": 240,    // 可选：默认 agent 选项

  // mode = agent | parallel：
  "agents": [
    {"prompt":"...", "label":"scout-a", "cd":"...", "model":"...", "mcp":"...", "schema":"..."}
  ],

  // mode = pipeline（{input} = 上一 stage 的输出文本；stage 1 收到原始 item）：
  "items":  ["fileA.py", "fileB.py"],
  "stages": [
    {"prompt":"Review {input} for bugs, list findings."},
    {"prompt":"Adversarially verify these findings:\n{input}"}
  ]
}
```

- `mode:"agent"` 只跑 `agents[0]`。
- `parallel` 是 barrier（栅栏）：某个 agent 失败 → 该项 `success:false`，**不会**拖垮整批。
- `pipeline` 各 item 独立流过所有 stage，stage 间**无栅栏**；某 stage 失败则该 item 被丢弃。

需要 JSON spec 表达不了的控制流（循环、去重、条件 fan-out）时，直接 `import scripts/claude_orchestrator.py` 用它的 `agent` / `parallel` / `pipeline` 原语手写 driver。

---

## 配置参考表（anyrouter + cc-switch）

> 以下默认值来自本机 **anyrouter（经 cc-switch）** 实测。**你的中转可能不同**——把它们当起点，按自己环境实测调整。

### 认证（由 cc-switch 注入，通常无需手动设）

| 环境变量 | 值 / 来源 | 说明 |
|---|---|---|
| `ANTHROPIC_BASE_URL` | cc-switch 写入 | 指向 cc-switch 本地代理。 |
| `ANTHROPIC_AUTH_TOKEN` | cc-switch 写入 | 真实上游 token，由代理转发。 |
| `ANTHROPIC_API_KEY` | 桥自动 `setdefault("PROXY_MANAGED")` | `--bare` 模式需要一个 key 占位才不报错；真 token 由代理替换，**这里只是占位符，不含敏感信息**。 |

### 冷启动相关魔数（绕 429 的核心）

| 参数 | 默认 | 为什么是这个值 |
|---|---|---|
| `--retries` | `3`（→ 共 4 次尝试） | 退避累计 ~210s，**故意大于 anyrouter ~195s 接入冷启动窗口**。 |
| `--retry-base-delay` | `30.0` | 指数退避基数：30 → 60 → 120s。 |
| `--timeout` | `240` | 单次尝试的子进程超时，覆盖 ~213s 冷启动。 |
| `--cold-models` | `sonnet` | anyrouter 实测 **sonnet 真冷→429**（原生 + claude -p 都救不了）；命中则快速失败。留空禁用此判断。 |
| `model` | `opus` / `haiku` | 走 `claude -p` 时两者均可用（含 opus）；`haiku` ~$0.013/agent、`opus` ~$0.078/agent（实测，含 `--bare` ~10–12k cache 创建）。 |

> 💡 **换中转怎么调**：如果你的中转上 `opus` 才是冷的、`sonnet` 是热的，就把 `--cold-models opus` 设上、`model` 用 `sonnet`。`--cold-models` 留空则完全关闭快速失败逻辑。

### 防递归（可选）

`block_tool` / `--block-tool` 传给底层 `--disallowedTools`，用于阻止子 agent 反过来再调起你自己的桥包装器（自指递归）。例如 `"block_tool": "Bash(*your-wrapper*)"`。

留空 = 不阻止。注意 `--bare` 本就不加载 skills / hooks / CLAUDE.md，递归风险已经很低，**只有当你从某个子 agent 能回调的工具里发起本桥时才需要设它**。

---

## MCP 支持

- 在 `--bare` 下通过 `--mcp-config` **可直接挂载 MCP**（无需 `--setting-sources`）。
- **不需要外部 key 的 MCP**（如 `context7`）实测完全可用。
- MCP 起不来（`EPERM` / `Failed to connect`）见上方「遇到问题」。

---

## 版本与脆性（请务必读）

本工具依赖 `claude -p` 的若干**未公开 / 半公开 CLI 行为**：`--bare`、`--mcp-config`、`--json-schema`、`--session-id` / `--resume`、`--fallback-model`、`--max-budget-usd`。Claude CLI 一次更新就可能让其中某些行为变化或失效。

- 📌 **已验证可用的 Claude CLI 版本**：`2.1.170`（用 `claude --version` 查看）——本工具当前实测通过的版本。
- 升级 Claude CLI 后，先重跑「快速自测」确认桥还通。
- 提 issue 时请附上 `claude --version` 与 cc-switch 版本。

---

## 限制与注意

- **并发实测到 8 路全过**（haiku / opus 走 `claude -p`）；更高并发自行小步放量。
- 本工具的 `claude -p` 子进程**独立重试冷启动**（`--retries`，默认退避累计 ~210s），**不依赖主会话保活**；主会话只需自己能接入即可。
- 每个子 agent **真实消耗 token**：`haiku` ~$0.013/agent、`opus` ~$0.078/agent（含 `--bare` ~10–12k cache 创建）。按规模缩放 fan-out。
- `sonnet` 在该 anyrouter 渠道**真冷**（原生 + `claude -p` 都 429）→ 默认快速失败；用 `opus` / `haiku`。
- **haiku 子 agent 原生 `Workflow`/`Agent` 本就能用**，不必走本工具；本工具的核心价值是让 **opus** 子 agent 可用（原生必死）。
- 本工具**只为「中转 + cc-switch」这套环境而生**。如果你是 Claude 官方直连，原生 `Workflow` 工作得好好的，你**不需要**它。

---

## 致谢 / 配套

本工具是 **[cc-switch](https://github.com/farion1231/cc-switch)**（官网 [ccswitch.io](https://ccswitch.io)）用户的配套工具——排查思路、anyrouter 接入冷启动行为均基于 anyrouter + cc-switch 实测。

cc-switch 支持「从 GitHub 仓库一键安装 Skill」，本工具可直接通过它分发、安装。
