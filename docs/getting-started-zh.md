# Xylocopa 新手入门

> 实操教程：建项目 → 提任务 → 看 agent 执行，走完一个完整闭环。
>
> English: [getting-started.md](getting-started.md)

这份文档写给刚装完 Xylocopa（[安装步骤](../README.md#getting-started)）、不知道下一步做什么的新人。它不重复 README 的功能列表，只回答新用户实际会卡住的三个问题：

1. 任务输入框底下那排按钮都是什么？
2. Inbox / Project / Task / Agent / Session 各是什么意思，怎么配合？
3. 至少掌握哪些操作就能跑起来？

---

## 整体流程一览

Xylocopa 沿用 [GTD](https://gettingthingsdone.com/what-is-gtd/) 的思路：把想法从脑子里倒出来，之后再决定怎么处理，时机到了再动手。区别在于动手的是 agent。

```
          ┌─────────────────────────────────────────────────────────┐
          │                                                         │
  灵感    │    Inbox  ──▶  Project  ──▶  Task  ──▶  Agent  ──▶  Session
 ──────▶  │    (捕捉)      (归属)        (规划)     (执行)         (复盘&记忆)
          │                                                         │
          └─────────────────────────────────────────────────────────┘
                                                                    │
                                                   经验沉淀 ─────────┘
                                                   写回 PROGRESS.md
```

灵感先进 Inbox；之后（或立刻）归到某个 project 并派发，这会启动一个 agent（一个 Claude Code 会话）去执行；agent 跑完或卡住，你复盘 session，确认完成或继续迭代。值得留的经验写进项目的 `PROGRESS.md`。

---

## 核心概念

### Project（项目）
一组相关工作的容器，通常对应磁盘上一个 git 仓库——可以是已有仓库，也可以让 Xylocopa 从 GitHub URL 克隆。project 下派发的 agent 在该项目的目录里运行。

<p align="center"><img src="getting-started/03-projects.png" alt="项目列表页" width="360"></p>

只建一个大杂烩项目也完全可行。作者自己有一个 `random-things`，水电费、调研、一次性脚本都往里放，以后想拆再拆。

<p align="center"><img src="getting-started/04-new-project.png" alt="新建项目表单" width="320"></p>

建项目：长按底部导航栏的 `+` → **New Project**。只有名字是必填的；填了 Git URL 会自动克隆，不填就在 `~/xylocopa-projects/<name>/` 下建一个空目录。

### Task（任务）
一件具体的事——"加一个联系表单"、"修移动端底栏"、"交电费"。task 有标题、可选描述、可选的所属 project，以及几个参数（模型、思考深度、worktree、Auto 模式）。新建的 task 进 inbox，派发时离开。

### Inbox（收件箱）
跨所有 project 共享的一个队列：先进来，再处理。

<p align="center"><img src="getting-started/02-inbox.png" alt="Inbox 列表（多条待处理任务）" width="320"></p>

共用一个 inbox 是为了让记录这个动作不需要先回答"归哪个项目"。归类之后再说。

### Agent
Xylocopa 管理的一个运行中的 Claude Code 会话。派发任务时，agent 在 project 目录（或一个隔离的 [git worktree](https://git-scm.com/docs/git-worktree)，见 Worktree 开关）里启动，跑完等你复盘。

每个 agent 住在名为 `xy-<短 id>` 的 tmux 会话里，从任何终端 attach 上去都能继续聊——和网页是双向同步的。

### Session
每次对话都持久化为一个 session（Claude 写的 JSONL 加上 Xylocopa 的按消息缓存）。session 不会过期，除非手动删除；任何 session 都能带着完整上下文恢复。

---

## 最少 5 分钟上手

### 1. 建一个 project

长按底部导航栏的 `+`，**Create** 菜单有三个选项：

<p align="center"><img src="getting-started/08-create-menu.png" alt="Create 菜单 -- New Agent / New Project / New Task" width="320"></p>

选 **New Project**，起名（小写字母 / 数字 / `- _ .`），可选粘贴 Git URL，点 **Create Project**。先建一个 `random-things` 或 `misc` 当杂物间也很合理。

### 2. 提一条任务

回到 Inbox 页，短按 `+`（长按是 Create 菜单），**New Task** 面板从底部滑上来。

<p align="center"><img src="getting-started/06-new-task-dispatch-ready.png" alt="New Task 面板（选了 project 后）" width="320"></p>

- **Title** —— 可选，不填会从描述自动生成。
- **Project** —— 选一个，或留空之后再归类。
- **Describe what needs to be done** —— 自由文本，就是发给 agent 的 prompt。
- **Model** —— Opus / Sonnet / Haiku。默认 Opus，简单任务选便宜的。
- **Effort** —— L / M / xH / Max。越高思考越多、越慢、越贵。
- **Worktree** —— 打开后 agent 在隔离的 git worktree 里干活，不和你或其他 agent 打架。
- **Auto** —— 见下面 [Auto 模式与安全](#auto-模式与安全)。

### 3. 现在派发，还是先存 inbox？

输入框底下六个按钮，从左到右：

<p align="center"><img src="getting-started/01-input-bar-annotated-zh.png" alt="输入框按钮标注图" width="520"></p>

| 图标 | 名称 | 作用 |
|---|---|---|
| `+` | 附件 | 图片、PDF、文本文件，作为上下文传给 agent。 |
| 🎙️ | 语音输入 | 用 OpenAI Whisper 转文字（需要 `OPENAI_API_KEY`）。 |
| 📅 | 定时提醒 | 给这条任务设推送提醒，任务本身留在 inbox。 |
| ✈️ | 立刻派发 | 建任务并立即派发，直接进 agent 对话页。选了 project 才显示。 |
| 📥 | 存进 inbox | 存下并收起面板。 |
| ⚡ | 连续快存 | 存下但面板不关，接着写下一条。 |

右边三个彩色按钮就是离开面板的三种方式：记想法用 ⚡ 或 📥，想立刻开干用 ✈️。

### 4. 看 agent 跑

派发后进入 agent 对话页。

<p align="center"><img src="getting-started/12-chat-header-annotated-en.png" alt="Chat header — id pill / worktree pill / Task / branch / Stop" width="640"></p>

顶部第一行是状态点和操作按钮（Stop / Resume / OK），第二行是标签条：项目、worktree、Auto、Task、4 字符 id（长按可复制）。

在这个页面你可以实时看 agent 的输出、审批或拒绝工具调用（Auto 关着时）、发后续消息纠偏、或停掉它。桌面端右下角的分屏按钮可以同时盯 2–4 个 agent：

<p align="center"><img src="getting-started/09-desktop-inbox.png" alt="桌面端 Inbox" width="640"></p>

### 5. 收藏值得留的那条消息

一次长任务可能有几百条消息，下个月还会用到的往往就一条——文件路径、命令、某个决策。双击那条消息 → **Bookmark**，写一句备注或者直接跳过（跳过则用自动生成的标题 + emoji）。

两种粒度：⭐ **Starred**（chat header 右上角）置顶整段会话；📑 **Bookmarks** 只钉住一条消息。

每个项目详情页有 **Bookmarks** tab，在 **Starred** 旁边。每行显示标题、原消息预览、时间和编辑按钮；点进去跳回原对话并短暂高亮那条消息。

<p align="center"><img src="getting-started/13-bookmarks-list.png" alt="项目 Bookmarks 列表" width="360"></p>

---

## 处理 inbox

积压了就用三种方式消化：

1. 点进任务 → 编辑、选 project、点 **Dispatch**。
2. 拖左边的 `≡` 手柄重排，最上面的先做。
3. **AI 批处理**（inbox 右上角的 `AI` 按钮）：triage agent 把所有任务读一遍、润色 prompt、分配 project，你确认后再批量派发。

暂时做不了的任务可以延期：展开卡片，点 🌙 按钮选个日期，任务挪进底部折叠的 **Deferred** 分组，到期自动回来。

<p align="center"><img src="getting-started/10-inbox-defer-annotated-zh.png" alt="延期按钮和延期分组" width="360"></p>

项目级的任务列表和统计见 [project 详情页](getting-started/07-project-detail.png)。

---

## 在 project 里直接起 agent

每个 project 详情页顶部有一张 **New Agent** 卡片，已经知道任务归属时从这里启动最快，不经过 inbox。

<p align="center"><img src="getting-started/11-new-agent-annotated-zh.png" alt="项目内的 New Agent 卡片" width="520"></p>

和 New Task 面板比有两个区别：

- **定时（🕐）** —— 不立刻派发，到设定时间自动启动，比如夜里 2 点跑重构。
- **Task 开关** —— 这次运行要不要建一条 Task 记录。关：一次性会话，适合"这个函数干嘛的"这类快问快答；开：创建并关联 Task，出现在任务列表里，跑完自动生成摘要，适合以后可能重试或回看的事。

Model、Effort、Worktree、Auto 与 New Task 面板一致，见[第 2 节](#2-提一条任务)。

---

## Auto 模式与安全

打开 **Auto** 后，agent 以 `claude --dangerously-skip-permissions` 启动，执行工具调用不再逐个询问。无论 Auto 开关与否，[safety hook](../README.md#safety-guardrails) 都会硬拦这些操作：

- `rm -rf`
- 非 worktree 目录下的 `git push --force` / `git reset --hard`
- `git clean -f`、`git checkout -- .`、`git restore .`
- `DROP TABLE`、`TRUNCATE`
- 写到 project 目录之外的 `Write` / `Edit`

低风险任务（文档、UI 细节、worktree 里的隔离重构）开 Auto；想逐个审批工具调用就关着。

---

## Agent 跑偏了怎么办

恢复流程是 Try → Summarize → Retry：

1. 停掉 agent。
2. 在任务详情页点 **Summarize**，Xylocopa 读完整个 session，写一份"试过什么、什么没成、下一步建议"。
3. 编辑这份简报、加上你的指点，点 **Redo**。新 agent 带着简报开始，不重复同样的错。

非 session 特定的经验沉淀到项目的 `PROGRESS.md`，派发新 agent 时自动取回。

---

## 常见场景

**地铁上想到一件事** —— 打开 PWA，点 `+`，对麦克风说一句，点 ⚡，回头再处理。

**攒了十几条任务** —— 打开 inbox 点 **AI**，确认 triage 结果后批量派发。

**不想整理** —— 建一个 `random-things` 装所有任务。代价是没有按项目的经验检索，其他功能照常。

**Agent 原地打转** —— 停掉，Summarize，加一句纠正，Redo。比让它烧到 token 上限好。

**同时盯几个 agent** —— 桌面端用右下角分屏按钮（2–4 格，各自导航）；移动端用 Attention 按钮，有未读时变青色，点击跳到最早的未读对话。

---

## 用第三方 / 本地模型（可选）

Claude Code 本身支持 Amazon Bedrock、Google Vertex AI 和 LiteLLM 这类网关（可接本地模型）。配置方式与标准 Claude Code 相同：在 shell 或 `.env` 里设对应环境变量，Xylocopa 启动的 `claude` 子进程会原样继承。LiteLLM + 本地模型的实操见 [unsloth.ai/docs/basics/claude-code](https://unsloth.ai/docs/basics/claude-code)。

注意 UI 范围：模型下拉菜单只列 Anthropic 的 `claude-*` ID。Bedrock / Vertex 沿用相同模型名，通常直接可用；纯非 Anthropic 后端只能通过 `.env` 里的 `CC_MODEL` 默认值运行，UI 上的按 agent 切换没有接通。

---

## 接下来看什么

- [README](../README.md) —— 功能列表与安装
- [workflow.md](workflow.md) —— 一天任务的完整走例
- [ARCHITECTURE.md](ARCHITECTURE.md) —— 系统架构
- [install-cert.md](install-cert.md) —— 在客户端设备上信任自签名 HTTPS 证书
