# BearCode

BearCode 是一个基于 Python 的本地 **Agent Runtime**，同时提供网页工作台（界面名 **Emendation agent**）和命令行入口。它把模型推理、工具执行、权限确认、会话恢复、长期记忆、Skills、在线自进化与 **Prompt 资产治理平台** 连接在一起，适合用于项目分析、编程辅助，也适合学习和扩展 Agent 的运行机制。

项目重点是 **Harness**：模型提出操作意图，Runtime 负责判断权限、执行工具、保存状态和回写结果。网页与 CLI 共用这套能力；网页工作台采用纸白浅色主题，前端是原生 HTML / CSS / JavaScript，没有构建步骤。

实现方式上不依赖 LangGraph、LangChain、SpringAI 等框架：直接依赖只有两个模型 SDK 与少量工具库，编排由消息循环、工具表、JSON 文件持久化和标准库 HTTP 服务完成。语义判断交给模型，格式、权限、快照和发布门槛交给代码。

## 快速启动：前端与后端

**运行 `python -m agents.web`，即可同时启动后端 API 和前端页面。** 当前前端采用原生 HTML / CSS / JavaScript，由同一个 Python 服务提供，无需安装 npm 依赖或单独运行前端开发服务器。

| 部分 | 源码位置 | 启动或访问方式 |
| --- | --- | --- |
| 后端服务 | `agents/web.py`、`agents/` | `python -m agents.web` |
| 前端页面 | `web/` | 后端启动后，打开 <http://127.0.0.1:8765> |
| 命令行入口 | `agents/main.py` | `python -m agents.main`，按需使用 |

### 1. 准备环境并安装依赖

推荐使用 Python **3.11 或 3.12**。下面命令以 macOS / Linux 为例；Windows PowerShell 中把 `python3` 换成 `py -3.12` 或 `python`，激活脚本换成 `.venv\Scripts\Activate.ps1`，直接调用解释器用 `.venv\Scripts\python.exe`。Git 用于获取代码，ripgrep 可选；仅运行网页工作台不需要 Node.js。运行前端测试或使用依赖 Node.js 的 MCP 工具时，再准备 Node.js 环境。

Windows 中文环境建议以 UTF-8 模式启动服务，避免中文会话读取和 emoji 输出触发 GBK 编码错误：`$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'`，再执行启动命令。会话文件与工具产物本身都以 UTF-8 写入。

在 **BearCode 仓库根目录**执行：

```bash
# 确认 Python 版本
python3 --version

# 首次使用时创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 使用固定版本依赖安装
python -m pip install -r requirements.lock
```

如果默认 `python3` 版本不合适，可将创建虚拟环境的命令改为 `python3.12 -m venv .venv`。后续启动时只需激活已有虚拟环境，不必重复创建或安装。

### 2. 配置模型连接

先创建本地配置文件；已有 `.env` 时保留原文件：

```bash
test -f .env || cp .env.example .env
```

用编辑器打开项目根目录的 `.env`，将密钥、接口地址和模型 ID 替换为你实际使用的服务配置。下面两种方式**选择一种**；如果使用服务专用变量，需要删除或注释模板里已启用的 `APIKEY`、`API`、`MINI_CLAUDE_MODEL`，避免混用配置。

**OpenAI-compatible 接口：**

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-provider.example/v1
MODEL=your-model-id
```

**Anthropic-compatible 接口：**

```env
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
MODEL=your-model-id
```

上述地址和模型名称都是占位值，请以服务商提供的配置为准。也支持通用变量 `APIKEY` / `API`，以及兼容别名 `MINI_CLAUDE_API_KEY` / `MINI_CLAUDE_API_BASE` / `MINI_CLAUDE_MODEL`。

配置读取规则：

- 从当前工作目录向上查找 `.env`；终端中已设置的同名环境变量优先于 `.env`。
- 通用 `API` / `MINI_CLAUDE_API_BASE` 优先于服务专用的 Base URL；通用密钥也优先于服务专用密钥。
- `OPENAI_BASE_URL` 与 `ANTHROPIC_BASE_URL` 明确选择各自协议；使用通用 `API` 时，原生 `api.anthropic.com`、路径以 `/anthropic` 结尾或包含 `/anthropic/` 的地址使用 Anthropic 协议，其余使用 OpenAI 协议。
- 模型名称优先使用 `MODEL`，其次为 `MINI_CLAUDE_MODEL`；CLI 还可用 `--model` 覆盖。Web 入口通过环境配置选择模型。
- 修改 `.env` 后需重启服务。密钥保留在后端，不在前端页面录入或展示。

暂未配置模型时，也可以打开工作台查看页面和已有数据；发送任务前需要完成配置。

### 3. 启动后端服务

在仓库根目录打开终端：

```bash
source .venv/bin/activate
python -m agents.web
```

看到以下提示说明服务已经启动，请保持终端运行：

```text
Bear Code Web → http://127.0.0.1:8765
按 Ctrl+C 停止；模型配置沿用项目 .env。
```

也可以直接调用虚拟环境中的 Python，省去激活步骤：

```bash
.venv/bin/python -m agents.web
```

Windows PowerShell 对应命令：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m agents.web
```

端口仍为 `8765`，浏览器访问 `http://127.0.0.1:8765`。服务进程依附于启动它的终端，关闭该窗口或按 `Ctrl+C` 后链接会失效，需要重新启动。

### 4. 打开前端页面

在浏览器访问 **[http://127.0.0.1:8765](http://127.0.0.1:8765)**。

前端会连接同一地址下的 `/api/*`，通过 SSE 接收回复和工具执行状态。可以在工作台创建会话、描述任务、查看执行过程，并在需要权限时允许或拒绝；Skills、评测、记忆和设置也从侧栏进入。

前端文件由后端直接提供，**不需要 `npm install`、`npm run dev` 或构建步骤**。不要双击 `web/index.html`，也不要用独立静态服务器代替 `agents.web`：完整界面需要同源 API、会话 Cookie 和请求校验。

### 5. 端口、重启与开发调试

默认端口为 `8765`，如需更换：

```bash
python -m agents.web --port 8766
```

此时访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)。端口更改后，前端会自动使用新的同源地址，无需单独修改 API 配置。

| 修改内容 | 生效方式 |
| --- | --- |
| `web/` 中已有的 HTML、CSS、JS 文件 | 保存后刷新浏览器；没有自动热更新 |
| Python 后端代码或 `.env` | 在服务终端按 `Ctrl+C`，再运行启动命令，随后刷新页面 |
| 新增前端模块或静态资源文件 | 在 `agents/web.py` 的静态资源白名单中注册路径，重启服务后刷新页面 |

关闭浏览器不会停止服务。停止服务请在启动它的终端按 `Ctrl+C`。平时使用一个服务进程操作当前项目即可；切换端口时注意打开对应的新地址。

### 常见启动问题

| 现象 | 处理方式 |
| --- | --- |
| `ModuleNotFoundError: No module named 'anthropic'` 等依赖错误 | 激活 `.venv` 后重新安装 `requirements.lock`，或直接用 `.venv/bin/python` 启动 |
| `No module named agents` | 确认终端位于 BearCode 仓库根目录 |
| `Address already in use` | 停止占用该端口的旧服务，或用 `--port 8766` 启动并访问新地址 |
| 页面一直停在“正在加载”，按钮或图标无反应 | 如果刚更新代码，先重启 Python 服务再刷新页面；旧进程可能未注册新增的前端模块 |
| 模型未配置、鉴权失败或模型不存在 | 核对密钥、Base URL、模型 ID，以及终端环境变量与 `.env` 的优先级；修改后重启 |
| 出现请求校验失败或连接中断 | 确认服务仍在运行，刷新当前端口的页面，以重新建立本地会话 |
| Windows 上报 `'gbk' codec can't encode/decode` | 以 UTF-8 模式启动：设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8` 后重启服务；会话文件本身已是 UTF-8 |
| Windows 上 MCP 报「系统找不到指定的文件」 | `.mcp.json` 中把 `npx` 改为 `npx.cmd`；Python 子进程不会自动解析 `.ps1` / `.cmd` 后缀 |
| 链接点开没反应 | 说明服务进程已退出，按上文命令重新启动并确认 `http://127.0.0.1:8765` 返回 200 |

Web 服务固定监听 `127.0.0.1`，面向本机单用户使用。当前 `Dockerfile` 默认启动 CLI，且未包含 `web/` 静态资源；网页工作台请按上述本地方式启动。更多界面功能与运行边界见 [Web 工作台说明](docs/web-workspace.md)。

## 命令行使用

CLI 与网页是两个可选入口，使用网页时无需同时启动 CLI。下面命令同样在仓库根目录、已激活虚拟环境的终端中执行。

```bash
# 交互式对话
python -m agents.main

# 执行一次任务
python -m agents.main "总结这个项目的目录结构和核心模块"

# 只读分析并生成计划
python -m agents.main --plan "分析 Skills 检索逻辑应该如何优化"

# 恢复当前项目的最近会话
python -m agents.main --resume

# 查看全部参数和内置命令
python -m agents.main --help
```

进入交互模式后，可以直接输入任务，也可使用 `/plan`、`/skills`、`/memory`、`/compact` 等命令。

## 核心能力

- **网页工作台**：对话、流式回复、工具授权、任务详情、Skills、评测、记忆与设置，共用本地 Runtime。
- **通用 Agent Runtime**：统一编排模型、工具、权限、上下文、Memory、Skills 和外部扩展。
- **完整 Agent Loop**：模型请求、tool call 解析、权限检查、工具执行、tool result 回写、继续推理、会话保存形成闭环。
- **OpenAI / Anthropic 双协议**：支持 OpenAI-compatible 和 Anthropic-compatible 接口。
- **工具系统与权限控制**：支持读写文件、精确编辑、搜索、Shell、Skill 调用、子 Agent 和 MCP 工具；Plan Mode 下阻断写操作和 Shell。
- **Skills 体系**：通过项目级和用户级 `SKILL.md` 保存可复用方法，支持检索、调用、inline / fork 执行和版本化演化。
- **长期 Memory**：按项目路径 hash 隔离记忆，保存用户偏好、项目背景、历史决策和参考资料。
- **Skills 自进化**：从用户反馈中抽取可复用规则，保存隔离候选，经评测或人工审核后发布到 `SKILL.md`。
- **MCP 外部工具扩展**：基于上游实现的 stdio JSON-RPC MCP Client，把外部 MCP Server 工具包装为 `mcp__server__tool`。
- **子 Agent**：支持 `explore`、`plan`、`general` 以及自定义子 Agent。
- **会话恢复和上下文压缩**：自动保存 session，支持 `--resume`、`/compact`，并对大工具结果做截断或持久化。
- **Prompt 资产治理平台**：冻结评测边界（`probe / pr_val / test`）、稳定规则编号与逐规则通过率、人工确认的规则编译、历史回复合规扫描、单规则级改写、资产生命周期注册表与工具型 SOP 评测入口。
- **纸白浅色工作台**：原生前端，SSE 增量推送、权限确认、治理页与候选审核；提示条按需弹出、可手动关闭。
- **Token 与缓存观测**：稳定 system 前缀、去掉每轮工具后重建、`/cost` 显示缓存读/写 token，会话状态提供 `cacheRead` / `cacheWrite` / `sideInput` / `sideOutput`。

## 项目架构

核心运行链路：

```text
用户输入
  -> agents/main.py（CLI）或 agents/web.py（网页）
  -> Agent.chat()
  -> 构建 Prompt / 检索 Skills / 预取 Memory / 初始化 MCP
  -> 调用 OpenAI-compatible 或 Anthropic-compatible 模型
  -> 模型返回文本或 tool call
  -> Runtime 做权限检查
  -> 执行工具 / Skill / MCP / 子 Agent
  -> tool result 回写模型
  -> 保存 Session
  -> 后台执行 Skill usage tracking 和 online skill evolution
```

## 目录结构

```text
BearCode/
├── agents/
│   ├── main.py                    # CLI 入口、REPL、参数解析
│   ├── web.py                     # Web 后端：静态页面、HTTP API、SSE 流式事件
│   ├── config.py                  # CLI、Web、评测共用的环境和模型配置
│   ├── agent.py                   # Agent Runtime、模型调用、工具调度、上下文压缩
│   ├── context_runtime.py         # 两种协议共用的上下文预检和限时 Memory 召回
│   ├── tool_results.py            # 无模型依赖的工具结果压缩策略
│   ├── result_storage.py          # 大工具结果单独落盘、检查点与上下文保留预览
│   ├── streaming.py               # 流式尝试隔离与失败输出回退
│   ├── checkpoints.py             # 工具执行检查点与中断恢复
│   ├── tools.py                   # 内置工具和权限系统
│   ├── tool_schemas.py            # 工具定义与参数 Schema
│   ├── prompt.py                  # System prompt 动态构建
│   ├── skills.py                  # Skills 加载、检索、执行、创建和演化封装
│   ├── online_skill_evolution.py  # 在线 Skill 抽取和 add/merge/discard 决策
│   ├── skill_evolution.py         # Skill 落盘、版本快照、审计统计
│   ├── online_skill_eval.py       # 回归评测、冻结边界与逐规则指标
│   ├── governance_eval.py         # 冻结题集、指纹校验、分层门槛
│   ├── governance_rules.py        # 稳定规则编号、规则编译、逐规则指标
│   ├── governance_assets.py       # 本地资产注册表与生命周期状态机
│   ├── governance_tools.py        # 工具型 SOP 评测入口
│   ├── governance.py              # 治理页聚合与动作入口
│   ├── skill_candidates.py        # 候选隔离区与快照校验
│   ├── session_memory.py          # 结构化折叠记忆
│   ├── background_jobs.py         # 可恢复的后台学习任务队列
│   ├── storage.py                 # 原子写与跨进程文件锁
│   ├── memory.py                  # 长期记忆系统
│   ├── mcp_client.py              # MCP stdio JSON-RPC 客户端
│   ├── subagent.py                # 子 Agent 配置
│   ├── session.py                 # 会话保存与恢复
│   └── ui.py                      # 终端 UI 输出
├── web/                          # 前端源码，原生 ES modules，无需打包
│   ├── index.html                # 页面结构
│   ├── styles.css                # 纸白浅色主题与响应式布局
│   ├── app.js                    # 页面交互、API 请求与 SSE 订阅
│   ├── chat-view.mjs             # Markdown 排版与消息局部更新
│   ├── chat-scroll.mjs           # 聊天滚动控制
│   ├── conversation-state.mjs    # 会话状态、草稿与附件
│   ├── governance-view.mjs       # 治理页渲染
│   └── tests/                    # 前端单元测试
├── .bear/
│   ├── skills/                    # 项目级 Skills
│   ├── skill-evolution/           # 候选、评测与自进化审计产物
│   └── governance/                # 资产注册表与规则数据
├── data/                          # 评测样例与题目数据
├── docs/                          # Web 使用、运行可靠性与治理改造计划
├── tests/                         # Python 回归测试
├── Dockerfile
├── .env.example                  # 模型配置模板（含缓存兼容开关）
├── .mcp.json                      # MCP Server 配置
├── requirements.lock             # 固定版本依赖，安装时使用
├── requirements.txt              # 直接依赖与版本范围
└── README.md
```

## Skills 自进化

BearCode 的核心特色是 **Skills 自进化**。它从用户明确反馈中抽取未来可复用的规则，默认保存候选；通过评测或明确的人工审核后，才发布到项目级或用户级 `SKILL.md`。

### 1. 开启自动自进化

默认 `BEAR_AUTO_SKILL_EVOLUTION` 是开启的。为了明确配置，建议在 `.env` 中写：

```env
BEAR_AUTO_SKILL_EVOLUTION=1
BEAR_AUTO_SKILL_TARGET=project
```

含义：

- `BEAR_AUTO_SKILL_EVOLUTION=1`：启用在线 Skill 自进化。
- `BEAR_AUTO_SKILL_TARGET=project`：自动新增的 Skill 写入当前项目 `.bear/skills/`。

如果希望沉淀为所有项目共享的个人 Skill：

```env
BEAR_AUTO_SKILL_TARGET=user
```

对应路径：

```text
project: <project>/.bear/skills/<skill_name>/SKILL.md
user:    ~/.bear/skills/<skill_name>/SKILL.md
```

### 2. 用允许写入的权限模式启动

后台保存 Skill 候选需要当前权限模式允许维护操作。推荐使用：

```bash
BEAR_AUTO_SKILL_EVOLUTION=1 \
BEAR_AUTO_SKILL_TARGET=project \
python -m agents.main --accept-edits
```

也可以使用更激进的模式：

```bash
python -m agents.main --yolo
```

不建议长期默认使用 `--yolo`，因为它会跳过确认。日常推荐 `--accept-edits`。

### 3. 给出可复用反馈

自进化不是把每一句话都写成 Skill。它只适合沉淀稳定、明确、未来同类任务仍适用的规则。

不太适合沉淀：

```text
帮我写一篇 500 字政府报告。
```

适合沉淀：

```text
以后写政府报告、工作汇报、调研材料时，默认先给可直接使用的初稿，不要连续追问；结构按“标题、背景、主要情况、问题分析、工作举措、下一步计划”组织，语言要正式克制。
```

适合演化已有 Skill：

```text
以后这类政府报告还要加入关键数据指标，不能只写概念性表述。
```

### 4. 自进化链路如何工作

```text
第 N 轮用户任务
  -> Agent 输出结果
  -> 保存 pending extraction window
第 N+1 轮用户反馈
  -> 合并进上一轮 window
  -> 随会话持久化待提交反馈，再提交后台队列（不依赖本轮回答成功）
  -> online_ingest()
  -> Extractor 抽取候选 Skill
  -> Maintainer 判断 add / merge / discard
  -> 保存隔离候选：/skill-candidates 查看
  -> /skill-eval 验证已有 Skill 的合并候选
  -> /skill-apply <id> 发布通过评测的候选
  -> 写入 SKILL.md；/skill-rollback <name> 可恢复旧版本
  -> 记录 provenance、usage stats 和版本快照
```

核心文件：

```text
agents/agent.py
agents/online_skill_evolution.py
agents/skills.py
agents/skill_evolution.py
```

审计产物：

```text
.bear/skill-evolution/usage.jsonl
.bear/skill-evolution/online_provenance.jsonl
.bear/skill-evolution/online_skill_provenance.json
.bear/skill-evolution/skill_usage_stats.json
.bear/skill-evolution/history/
```

## 在线 Skill 回归评测

REPL 中运行 `/skill-eval`。首次生成 `replay_review.jsonl`，选择“原任务重做 / 反馈修正”、绑定目标消息轮次，确认会话归属和验收条件后再次运行。题目和上下文从来源重建，重复目标只计一次。当前版、候选和已有冠军在同一批任务上重新作答；同会话窗口固定分组，样本数与模型调用量有上限，异常或不完整比较不会晋级。

这些是文本任务的回归检查，来源可能参与过 Skill 学习，不代表独立 benchmark 成绩。规则不再从 Skill 关键词自动猜测；冠军快照单独保存。当前 `requirements` 的字符串默认必须通过，可选项使用 `{"text":"要求", "required":false}`，关键失败不能靠其他分数抵消。`json_schema` 检查可校验字段及类型，`BEAR_SKILL_JUDGE_MODEL` 可单独指定同服务的裁判模型。

新 Skill 或缺少独立回放样本时，可在审阅后明确使用 `/skill-apply <id> --manual-review`；这是人工发布，不代表通过评测。后台任务有并发上限并保存待办；来源日志按增量索引读取。`/cleanup-state [days]` 预览旧工具结果和评测运行目录，明确加 `--apply` 才删除，当前冠军运行目录保留。

当前候选发布、权限、验收条件和恢复流程详见 [运行可靠性与 Skills 发布流程](docs/runtime-hardening.md)。

## Prompt 资产治理平台

治理平台把「Skill 改得好不好」拆成可复核的证据链，入口在 Web 治理页与 `/skill-eval` 命令。

**冻结评测边界**：`probe / pr_val / test` 三段题集带 manifest 指纹，加载时校验跨 split 与训练题泄漏，优化器只能看到允许的部分；分层门槛逐段判断，最终 `test` 只跑一次，默认 `automerge=False`。报告把「回归分数」与「冻结分数」分列，避免用见过的题目冒充泛化能力。

```text
/skill-eval --frozen               使用冻结题集
/skill-eval --frozen --final-test  声明最终 test（只可一次）
/skill-eval --rule-level           单规则级候选改写
/skill-eval --historic             历史回复合规扫描
/skill-eval --historic --semantic  扫描时启用模型语义判定
```

**稳定规则编号与逐规则指标**：规则 ID 由人工审核过的验收条件内容哈希派生，跨运行稳定，因此不需要升级审核数据结构或迁移旧记录。逐规则通过率写入 `rule_metrics.json`，治理页展示每条规则的通过率与失败样本。口径上**规则通过率不等于任务成功率**。

**半自动规则编译**：模型只输出严格 JSON，先暂存为候选规则；人工在治理页确认后才会生效，模型输出不会直接变成规则，也不做关键词自动推导。

**历史回复体检**：只扫描已保存的历史回答，不重新生成、不参与版本胜负；加 `--semantic` 才启用模型语义判定。结果写入 `historic_scan.jsonl`。

**单规则级改写**：模型只返回目标规则 ID 与 `old_text` / `new_text`，本地精确替换一处，避免整篇重写带来不可控变化。

**资产注册表与生命周期**：`.bear/governance/assets.json` 记录 owner / team / scope 与评审记录，状态机为 `Draft → Review → Active → Deprecated → Archived`，并检测跨规则冲突。归档资产不能触发付费评测。

**工具型 SOP 评测入口**：复用只读工具白名单与用例工具集，产物按目录、按口径分开保存。

治理页提供规则确认、状态切换、冲突展示与决策台账；建议采纳不等于自动发布。改造计划与外部参考归属见 [Prompt 资产治理平台改造计划](docs/Prompt资产治理平台改造计划.md)。

## 会话、权限与运行边界

默认只恢复当前项目的最近更新会话。也可使用 `--resume-id <会话ID>` 指定会话；项目路径或 API 协议不一致时拒绝恢复，避免把其他项目的操作上下文带入当前目录。

恢复会话也保留 Skill 学习上下文、待反馈窗口和稳定的对话分组。未知模型默认按 32000 token 容量处理，可通过 `--context-window N` 或 `BEAR_CONTEXT_WINDOW` 指定实际容量。

预算覆盖主模型、辅助调用及运行中的子 Agent，`/clear` 只清理上下文，不重置本次进程的累计费用。默认输入/输出估价仍为每百万 token 3/15 美元，可通过 `--input-price`、`--output-price` 或 `.env` 的 `BEAR_INPUT_PRICE_PER_MILLION`、`BEAR_OUTPUT_PRICE_PER_MILLION` 修改。预算是基于已返回用量的估算；已发出的并发请求、SDK 内部重试和失败请求的未知用量仍可能超出估算上限。

为减少 prompt cache miss，运行时默认使用静态折叠指引，且工具轮次结束后不再每轮重建 system prompt；设置 `BEAR_LEGACY_CONTEXT_REFRESH=1` 可恢复旧的动态刷新行为。`/cost` 会额外输出 `Prompt cache: ... read / ... write`；`Agent.get_token_usage()`（Web 会话状态也读取它）增加 `cacheRead`、`cacheWrite`、`sideInput`、`sideOutput` 四个字段。缓存读/写 token 目前只用于观测，费用估算仍按完整输入单价计算。当前实现只保证稳定的 system 前缀与缓存埋点：OpenAI 侧依赖服务端自动前缀缓存，Anthropic 侧尚未设置显式 `cache_control` 断点，因此不等价于显式缓存控制。

Ctrl+C 会取消当前任务；Shell 超时和取消会清理其进程组（macOS/Linux）。退出时会关闭模型客户端和 MCP，并最多等待后台 Skill 任务 5 秒，再取消未完成任务；可用 `BEAR_SHUTDOWN_TIMEOUT` 调整等待时间。

`/skill-eval` 会等待当前后台 Skill 任务完成，再读取来源记录和技能快照；此等待不使用退出超时，也不会因此取消学习任务。

在线 Skill 创建、演化、淘汰及手动 `/skill-create`、`/skill-evolve`、`/skill-restore` 共用权限检查。没有显式 allow 规则时，默认及 `--dont-ask` 模式不会自动写入或移走 Skill；`--accept-edits` 下仍遵守 deny 规则。后台任务不会弹出确认提示；需要确认的操作会被拒绝。归档后可输入 `/skill-restore <名称>` 恢复。

MCP Server 启动本身也需要权限，不能通过发现工具隐式执行项目配置中的命令。默认及 `--accept-edits` 模式逐个确认启动命令；计划模式不启动 MCP，`--dont-ask` 下没有显式 allow 规则时拒绝启动。可用 `mcp_start(服务名)` 配置启动规则；启动权限与 `mcp__服务名__工具名` 的调用权限分别检查。计划审批遇到输入关闭、无审批回调或无效结果时保留计划模式，不授权执行。

项目级 allow 规则须先通过 `/trust-project` 信任当前配置；项目级 deny 始终生效。信任绑定项目路径和配置摘要，修改权限或 MCP 配置后需要重新信任。每个工具在执行时重新检查当前模式，MCP 调用逐次展示参数并确认。截断响应中的工具不会执行。

文件工具按传入路径操作，不会把不存在的绝对路径替换为当前目录中的同名文件。上下文压缩要求完整且类型正确的结构化记忆；模型失败、截断、字段缺失或任务状态为空时跳过压缩，保留原始消息历史和压缩次数。

Session、Skill 统计、来源索引和评测状态使用临时文件原子替换；统计、来源索引、Skill 演化、回放池和冠军注册表的读改写使用协作文件锁。损坏 JSON 会保留并报错。文件锁不能阻止未使用该锁的外部编辑器覆盖文件，多文件更新也不等同于数据库事务。

## 折叠对照评测

BearCode 早期自行试跑折叠评测时，得到的数据偏低。因此，用于说明 GAIA / HLE 指标和记忆折叠消融的 `53.3%`、`20.2%`、`44.7%` **引用 DeepAgent 论文公开报告的结果**，仅作为量化参照，不是本项目实测成绩。数据集和评测入口都在仓库中，想了解 BearCode 在自己配置下的表现，可以自行运行。

先校验案例，再运行同题的折叠开启/关闭对照。运行命令会调用模型；结果会保存逐题答案、判分、折叠前后的轨迹和汇总。

```bash
python -m agents.eval_folding --cases data/folding_eval_smoke.jsonl --output .bear/folding-eval/smoke-001 --validate-only
python -m agents.eval_folding --cases data/folding_eval_smoke.jsonl --output .bear/folding-eval/smoke-001 --fold-after-turn 2
```

评测入口默认采用参考 DeepAgent 源码的三路并行记忆生成（事件、工作、工具）和每题最多 3 次折叠；可通过 `--fold-memory-mode single_json`、`--max-fold-limit N` 做本项目内部对照。`--fold-after-turn` 是 BearCode 为短样例增加的固定触发，不是论文原始触发方式。此入口的工具环境与判分器仍不同于论文，生成的分数不能直接对标论文表格。

汇总只对两组都完成、开启组确实折叠、关闭组未折叠且题目工具需求已审核的配对计算正确率；无效配对和原因保留在 `summary.json`。原始 GAIA/HLE 题目需先逐题审核所需工具并整理为带 `required_tools` 的 JSONL，文本题也可能需要当前入口不具备的联网能力。`review_queue.jsonl` 保存需人工复核的预测，可在评测运行目录中查看。

为避免单次模型回复过长，评测默认将主模型每次回复限制为 2048 token，可用 `--max-output-tokens` 调整；`--max-cost` 是回复完成后的估算检查，不能代替单次输出上限。

样例仅用于确认评测链路；评测入口见 `agents/eval_folding.py`，运行口径与结果字段以评测目录中的 `summary.json` 为准。

## 开发验证

Python 回归测试使用模拟模型，不请求付费模型：

```bash
source .venv/bin/activate
python -m unittest discover -s tests -q
```

仅检查 Web 后端时：

```bash
python -m unittest discover -s tests -p 'test_web*.py' -q
```

前端语法和单元测试使用 Node.js（CI 使用 Node.js 22），无需安装 npm 包：

```bash
node --check web/app.js
node --test web/tests/*.test.mjs
```

Windows PowerShell 等价命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --check web/app.js
node --test web/tests
```

测试覆盖会话增量、草稿与附件、聊天滚动、Markdown 排版，以及 Web 权限与 HTTP 访问边界。模型效果和 Skill 泛化能力需要另行评测。

## 更多文档

仓库内目前保留三篇专题文档，其余说明已并入本 README：

- [Web 工作台：界面功能、启动与运行边界](docs/web-workspace.md)
- [运行可靠性与 Skills 发布流程](docs/runtime-hardening.md)
- [Prompt 资产治理平台改造计划](docs/Prompt资产治理平台改造计划.md)

## 来源与许可

本项目的基础 Agent Runtime 参考并复用了 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 的实现，包括工具、提示词、Memory、MCP 和子 Agent 等模块。上游代码采用 MIT 许可，原作者版权与完整许可文本见仓库根目录 [LICENSE](LICENSE)。BearCode 在此基础上进行了修改和扩展。
