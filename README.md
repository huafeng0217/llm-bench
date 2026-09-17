# LLM Bench — 大模型基准测评

一个**本地运行的 Web 应用**：填入任意 OpenAI 兼容接口（`base_url` + API Key + 模型名），即可自动跑公开 benchmark、判分、生成排行榜对比。

纯本地部署，**API Key 只保存在你自己电脑上**，不上传任何服务器。

## 功能特性

- **模型管理**：增删模型、连接测试、服务商预设（DeepSeek / 通义 / Kimi / GLM / OpenAI / OpenRouter / Ollama），Key 打码显示
  - **模型用途分两类：被测模型 / 判别器**。判别器只用于给安全类基准判分，**不会出现在评测的模型下拉里**，
    后端也会直接拒绝拿它发起评测 —— 安全评测里最忌讳「裁判自己也在被测之列」（自偏袒）。改用途时可逆，
    已有历史评测时会先提示条数再确认，**历史成绩不会被删**（数据是真的，出现过就留着）
- **题库下载**：一键下载 34 个公开 benchmark 数据集到本地 `data/` 目录；GitHub raw 主源失败会自动回退 ghproxy 加速镜像，HuggingFace 失败回退 hf-mirror
- **评测引擎**：并发调用 API、自动判分、3 次重试、逐题明细导出（JSONL）
- **任务管理**：运行中的任务可随时**停止**（保留已跑进度）；**删除会自动先停止**；支持**批量删除**；被中断的任务可**续跑**（只补剩下的题）
- **排行榜**：按分类分区（通用知识 / 中文能力 / 科学推理 / 常识推理 / 数学推理 / Agent·工具调用 / 代码工程 / 安全·对齐），先看「总览（各分类冠军）」，点分类展开「综合榜（分类内平均分）+ 各基准分项榜」
  - 同一模型同一基准的多次测试**只在完整评测里取最高分**；没跑完题库的会标出「部分 x/y」、排在完整成绩之后，
    不参与最高分评选、也不计入综合平均分（`app/scoring.py` 一处定义，排行榜/总览/AI 总结共用）
  - 按「**覆盖度优先**」排序，避免只跑简单基准刷分
  - **子集家族折叠成一个基准**：BFCL v4 的 16 个子集在界面上是一张卡（下拉切换子集，选项里带一行简介），
    排行榜里是一块「官方加权总分 + 每组得分 + 子集明细（可展开）」；每个子集都标出**官方分组与权重**
  - **官方加权总分**：按 BFCL 官方口径复合（`Overall = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%`，
    组内先取子集平均再按组权重加权），并逐行显示**权重覆盖率** —— 本项目覆盖官方权重 60%（缺 Agentic 40%），
    所以它不会被当成完整 BFCL 成绩；缺组时按已跑组归一化
- **基准卡片文案分两层**：卡片上是两行以内的 `summary`（写「差在哪 / 低分说明什么」，不复述名字、不写题量），
  **完整说明在弹层里**（点简介文字或卡片页脚的「简介」按钮；不遮挡别的卡片）——
  另外「开始评测」上方再给一次（真要花钱之前最后一眼看的地方）
- **成绩总览**：基准 × 模型的热力矩阵，每行最高分单独标出（同样只在完整评测之间评，部分评测不高亮）；点模型名展开能力画像
- **逐题对比**：勾选两个任务，逐题并排看两边答案差异（可只看分歧题）
- **AI 总结**：让任一已接入的模型写一份中文评测总结 —— **统计量由程序算好**（排名、覆盖率、差距是否显著），模型只负责解释，因此换个模型来写数字也不会变
- **安全 / 对齐评测**（3 个基准，**由裁判模型判分**）：HarmBench 直接请求 200 条、JailbreakBench 有害行为 100 条、
  JailbreakBench 良性请求 100 条。判分提示词**照抄官方**（HarmBench 分类器 / JBB 越狱与拒答判官），
  裁判从「判别器」类型的模型里选（不能是被测模型自己）。**至少两个一起看**：只看有害请求的抵抗力会把
  「什么都不敢答」评成最高分，良性那 100 条正是用来暴露「对齐过头」的。
  换裁判分数就不可比 —— 任务列表里直接显示这次是谁判的；`scripts/calibrate_judge.py` 可以先用官方**人工标注集**
  量出这个裁判的一致率再决定用不用它（实测：`kimi-k3` 在 HarmBench 上 **97.5%**、JBB 上 **95.0%** 与人工一致，
  且没有「把越狱判成安全」的偏差；`qwen3.7-flash` 是 92.5% / 82.5%）。
- **多种题型**：
  - 选择题（**2 选 1** ~ 5 选 1）：MMLU / C-Eval / CMMLU / MMLU-Pro / GPQA / TruthfulQA / ARC-Challenge / HellaSwag / WinoGrande
  - 数值题：GSM8K / MATH-500 / AIME（整数答案）
  - 函数调用：BFCL v4（AST 匹配评分，含 Live 真实提问子集）
  - 安全 / 对齐：HarmBench / JailbreakBench（**裁判模型判分**，见上）
  - 多轮对话：BFCL v4 multi_turn（模拟环境 + 一轮内多步工具调用）
  - **代码执行**：HumanEval / LiveCodeBench —— 真的把模型代码跑进一次性 Docker 容器判分

## 快速开始

### 环境要求

- Python 3.10+
- 依赖见 `requirements.txt`

### 安装

```bash
pip install -r requirements.txt
```

### 启动

```bash
# 在项目根目录执行
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000**

### 免 Key 体验

没有 API Key 也能先跑通全流程：模型名填 `demo`，base_url 填 `mock://local`，会用一个内置模拟模型（约 70% 正确率）演示评测。

## 使用流程

1. **添加模型**：填 `base_url` + API Key + 模型名（可先从服务商预设自动填 base_url），点「测试连接」验证
2. **下载数据集**（可选）：在基准卡片点「下载」，或命令行下载（见下）
3. **发起评测**：点选一个基准 + 选择模型，点「开始评测」
4. **查看结果**：评测任务列表看进度/正确率（默认显示最新 5 条，可展开分页），点「明细」看逐题，排行榜看对比

## 支持的数据集

| 分类 | 数据集 | 题量 | 题型 |
|---|---|---|---|
| 通用知识 | MMLU | 约 1.4 万 | 4 选 1 |
| 通用知识 | MMLU-Pro | 约 1.2 万 | 10 选 1 |
| 通用知识 | TruthfulQA | 776 | 4~10 选 1 |
| 中文能力 | C-Eval | 1346 | 4 选 1 |
| 中文能力 | CMMLU | 约 1.1 万 | 4 选 1 |
| 科学推理 | GPQA Diamond | 198 | 4 选 1 |
| 科学推理 | ARC-Challenge | 1172 | 4~5 选 1 |
| 常识推理 | HellaSwag | 10042 | 4 选 1 |
| 常识推理 | WinoGrande | 1267 | **2 选 1** |
| 数学推理 | GSM8K | 7473 | 数值答案 |
| 数学推理 | MATH-500 | 500 | 数值 / 表达式 |
| 数学推理 | AIME 2022-2024 | 90 | 整数答案 |
| 数学推理 | AIME 2025 | 30 | 整数答案 |
| Agent / 工具调用 | BFCL v4 · 单函数（Python / Java / JavaScript） | 550 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 多函数选择 | 200 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 并行调用 / 并行多选 | 400 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 无关拒绝 | 240 | 拒绝调用 |
| Agent / 工具调用 | BFCL v4 · 多轮对话（base / 长上下文 / 缺函数 / 缺参数） | 800 | 多轮工具调用 |
| Agent / 工具调用 | BFCL v4 · **Live** 单函数 / 多函数 / 并行 / 并行多选 | 1351 | 函数调用（真实提问） |
| Agent / 工具调用 | BFCL v4 · **Live** 无关拒绝 | 884 | 拒绝调用（真实提问） |
| 安全 / 对齐 | HarmBench（直接请求） | 200 | 裁判判分（有害行为是否被照做） |
| 安全 / 对齐 | JailbreakBench 有害行为 | 100 | 裁判判分（越狱是否成功） |
| 安全 / 对齐 | JailbreakBench 良性请求 | 100 | 裁判判分（是否过度拒绝） |
| 代码工程 | HumanEval | 164 | 沙箱执行官方单元测试 |
| 代码工程 | LiveCodeBench v6 | 175 | 沙箱执行竞赛测试用例 |
| 演示样例 | MMLU / C-Eval 样例 | 各 12 | 4 选 1 |

> 合计 **36 个基准**（含 2 个演示样例；Agent 分类下 16 个 BFCL v4 子集，界面上折叠成一个「BFCL v4」）。
> BFCL v4 官方共 22 个子集，本站提供其中 16 个（官方权重 60%）——缺 Web Search / Memory 那 5 个：
> 前者要 SerpAPI 付费服务、后者要先跑记忆预处理并拉 embedding 模型，都跟「纯本地、可复现」冲突。

## 数据集下载

```bash
python scripts/download.py                    # 下载全部 34 个可下载题库
python scripts/download.py cmmlu gpqa         # 下载指定题库
python scripts/download.py BFCL_v4_multi_turn_base   # 下载单个 BFCL 子集
python scripts/download.py BFCL_v4_live_simple BFCL_v4_live_irrelevance   # BFCL Live 子集
python scripts/download.py humaneval livecodebench   # 代码类题库
```

也可以在网页的基准卡片上点「下载」按钮，由后端自动调用脚本下载（可看到下载状态）。

> 下载是**原子写**的：先写 `xxx.jsonl.part`，整份写完再改名成 `xxx.jsonl`。
> 所以下载中途你在 `data/` 里看到的仍是旧文件（或什么都没有），不会出现一个
> 「被截断但看起来正常」的题库 —— 那种文件会被当成完整题库，让覆盖率算错。

> 下载源说明：MMLU / C-Eval / GPQA / MMLU-Pro / GSM8K / MATH-500 走 HuggingFace datasets-server；CMMLU / BFCL / HumanEval / TruthfulQA 走 GitHub；AIME 走 HuggingFace parquet；LiveCodeBench 走 HuggingFace 的 jsonl（约 134MB，**只有 v6**）。国内网络可能需要能访问这些源（GitHub raw 失败自动回退 ghproxy，HuggingFace 失败自动回退 hf-mirror）。

## 项目结构

```
app/
  main.py           # FastAPI 路由（模型 / 题库 / 评测任务 / 排行榜 / 下载 / 沙箱状态）
  config.py         # 全局常量：路径 / 选项字母 / 默认参数 / 沙箱并发
  datasets.py       # 题库定位与加载、逐题明细导出、题量行数缓存
  llm.py            # 模型调用层（OpenAI 封装 / 重试 / mock）
  runner.py         # 评测任务编排（run_evaluation / 进度 / 续跑）
  engine.py         # 向后兼容门面：re-export 上面这些（新代码别再用它）
  qtypes/           # 题型：prompt、答案抽取、判分
    __init__.py       # 注册表 + detect()
    types.py          # Outcome / RunCtx / QuestionType
    choice.py         # 选择题
    numeric.py        # 数值 / 表达式题
    code_unit.py      # HumanEval 式（沙箱跑官方单元测试）
    code_stdio.py     # LiveCodeBench 式（沙箱跑竞赛用例）
    _code.py          # 代码题公共层（抽代码 + 额度阶梯）
    bfcl.py           # BFCL 单轮函数调用
    bfcl_multi_turn.py# BFCL 多轮对话
  benchmarks/       # 基准注册：元数据 + 下载器写在同一个文件里
    __init__.py       # 自动收集各模块的 ENTRIES，派生出 META / DOWNLOADERS / AVAILABLE
    types.py          # Benchmark 条目类型
    _util.py          # 下载公共工具（HTTP / gzip / parquet / 写 jsonl）
    mmlu.py ceval.py cmmlu.py gpqa.py mmlu_pro.py gsm8k.py math500.py
    aime.py truthfulqa.py humaneval.py livecodebench.py bfcl.py
  sandbox.py        # 代码执行沙箱（Docker）+ 容器内判分器
  lcb.py            # LiveCodeBench 测试用例的安全解码
  scoring.py        # 取数口径：题库派生事实 + 「一次成绩算不算数」（排行榜/总览/AI 总结共用）
  summary.py        # AI 总结：统计层（排名/覆盖率/显著性/护栏）+ 生成
  db.py             # SQLite 封装
  static/index.html # 前端单页（无构建步骤）
scripts/
  download.py           # 统一下载入口（薄 CLI，实现在 app/benchmarks/）
  resume_eval.py        # 续跑被中断的评测任务
  verify_all.py         # 一键跑完下面全部自检
  verify_imports.py     # 静态检查：未定义的名字 / 失效的相对导入
  verify_datasets.py    # 题库与元数据：行数缓存 / 题数口径一致 / 下载原子写 / 卡片文案放得下
  verify_models.py      # 模型用途：判别器不能被评测 / 改类型二次确认（临时库）
  verify_safety.py      # 安全评测：判分方向 / 裁判约束 / 判分失败处理（mock 裁判）
  calibrate_judge.py    # 用官方人工标注集校准裁判（会花 token，支持 --dry-run）
  verify_scoring.py     # 取数口径：完整/部分评测怎么选（临时库，不碰 data/app.db）
  verify_dispatch.py    # 判分分派：8 条用例覆盖 6 种题型（mock 模型，不花钱）
  verify_sandbox.py     # 沙箱隔离安全验证（跑攻击载荷）
  verify_humaneval.py   # HumanEval 抽取 + 参考解法自检
  verify_livecodebench.py  # LiveCodeBench 判分器自检
  verify_web_templates.mjs # 前端模板渲染自检（需要 Node，不需要浏览器）
  verify_summary.py     # 统计层自检（含与数据库对账）
  verify_summaries.py   # 已生成的 AI 总结与统计事实是否一致
data/               # 题库、数据库、评测结果（已 gitignore，仅保留样例）
requirements.txt
```

## 扩展：加一种题型 / 加一个基准

**题型**（怎么问、怎么判）由 `app/qtypes/` 的注册表统一管理，主循环只做一件事：

```python
outcome = await qtypes.detect(ctx, item).runner(model_cfg, item, params, ctx)
```

加题型 = 新建一个 `app/qtypes/<id>.py` + 在 `TYPES` 里加一条（`detect` + `runner` + 是否需要沙箱），
**不需要改 `run_evaluation`**。以前这里是一串硬编码的 `if/elif`，四种判分逻辑全部 inline 在主循环里。

**基准**（题库 + 元数据 + 下载）在 `app/benchmarks/` 下，**一个基准一条 `Benchmark` 记录，
元数据和下载器写在同一个文件里**；`__init__.py` 自动收集成注册表，`META` / `DOWNLOADERS` / `AVAILABLE` 都由它派生。

每条基准有**两个文案字段**，别写混：

| 字段 | 显示在哪 | 写什么 | 长度约束 |
|---|---|---|---|
| `summary` | 卡片正文 | 「**差在哪 / 低分说明什么 / 为什么值得跑**」—— 不要复述名字已经说过的机制，也不要写题量（卡片标签上已有） | **≤ 40 字**（最窄卡片两行放得下，超了会被 CSS 省略） |
| `description` | 点「简介」打开的弹层 + 「开始评测」上方 | 完整口径：题目形态、判分方式、注意事项、训练数据污染风险 | 不限 |

以前卡片直接把 `description` 截成两行，30 个基准里 24 个（80%）看不全（livecodebench 只显示得下 23%）——
问题不在「文字太长」，而在**同一段文字被要求既当速览又当说明书**。现在卡片只放两行以内的 `summary`，
完整说明挪到**弹层**（点简介文字 / 「简介」按钮打开；点遮罩、「关闭」或 Esc 关掉）+ 评测区各一处才出现。

> 这两条约束（每个基准都有 summary、两行内放得下、不复述名字/不重复题量）由
> `python scripts/verify_datasets.py` 守着 —— 被省略的文案在页面上看起来完全正常，靠肉眼是发现不了的。
>
> **自己丢进 `data/` 的题库**（没有对应 `Benchmark` 记录的）走 `FALLBACK` 元数据，同样要有能放下的简介：
> 卡片上会写明「自己放进 data/ 的题库：没有官方口径说明，卡片只按文件名与题量显示」，
> 而不是显示一段对所有自定义题库都一样的格式说明（完整说明里才讲 jsonl 字段怎么填）。

**加一个基准 = 加一个文件**（或往同源模块的 `ENTRIES` 里加一条），不用改别处。
以前要同时改四处（`benchmarks.py` 的 META、`download.py` 的两份字典、以及下载函数所在的脚本），
实测某个基准 id 散落在 10 个文件里。

**同一份数据源的多个子集（家族）**：在模块里声明一条 `Family`（含官方分组与权重）并给每条 `Benchmark`
写上 `family=` / `group=`，界面就会自动把它们**折叠成一个基准**（选择基准处是下拉切换子集，
排行榜里是一块「官方加权总分 + 每组得分」），并按官方口径算加权总分。

以 BFCL 为例，**加一个子集 = 往 `_SUBSET_META` 表里加一行**（id、官方文件名、分组、题量、说明），
分组权重只在 `FAMILY_DEFS` 里写一次。注册表在 import 时会校验：分组 id 必须存在、
权重要配平到 1.0、`group` 不能脱离 `family` —— 写错直接启动失败，而不是界面上少一块。

## AI 总结（可选）

在「AI 总结」板块选一个已接入的模型，点「生成总结」，它会按**能力分类**写一份中文评测总结。

**核心设计：Python 算，AI 只解释。** 统计量（各基准排名、覆盖率、差距是否显著、数据问题）全部由代码算好
再喂给模型，模型只负责组织语言。这样即使换一个很弱的模型来写，**数字也不会错**。

- **一级维度是能力分类**，不是逐个模型铺开基准清单 —— 这样才看得出「不同模型在不同类型基准上的强弱」
- **强弱定义收窄**：只有「第 1 名 **且**至少 2 个模型可比 **且**领先显著」才算强项；末位且显著落后才算弱项。
  差距未达显著时只说「优势不明显」，不会说成「明显更强」
- **数字回查**：总结里出现的每个数字都会回查输入数据，找不到的标出来供你核对
- **数据问题只给终端看**：某格低于随机线（疑似接口不兼容）、最高分来自部分评测等，属于**我们自己的质量信号**，
  刻意不在页面上展示（那份总结是要能分享出去的）。要看就跑 `python scripts/verify_summary.py`
- 同一个模型重新生成会**覆盖**旧条目，不同模型各留一条，方便横向比较
- 生成按输入矩阵的指纹缓存：数据没变不会重复烧 token

> 生成需要几十秒（思考型模型更久），页面有模拟进度条；进度百分比是**估算**的（模型是一次性返回的），
> 所以同时显示真实已用秒数。

## 代码执行沙箱

HumanEval 与 LiveCodeBench 需要**真的把模型生成的代码跑起来**，所以引入了 Docker 沙箱。
每个任务起一个一次性容器（`--rm`），隔离参数全部落在 `docker run` 上：

```
--network none                    禁网
--memory 256m（竞赛题 512m）       内存上限，超了被 OOM 杀
--cpus 0.5                        CPU 上限
--pids-limit 64                   挡 fork 炸弹
--read-only                       根文件系统只读
--tmpfs /tmp:size=64m             只给一小块可写临时区
-v <本次生成的文件>:<容器内路径>:ro   只读挂载，**不挂项目目录**
```

关键点：容器里**只挂本次运行生成的那几个文件**，看不到 `data/app.db`、`data/models.json`（API Key）
或任何其他宿主路径；同时不联网，无法外传数据。

**使用前提**：本机装了 Docker Desktop 且**正在运行**。网页上选中代码类基准时会自动显示沙箱状态，
不可用会直接写明原因（引擎没起 / 缺镜像并给出 `docker pull` 命令），不会跑到一半才失败。

想确认隔离是否真的有效，可以跑一遍攻击性验证（会真的尝试读宿主文件、联网、写系统文件、fork 炸弹、
死循环、吃爆内存，全部应被挡住）：

```bash
python scripts/verify_all.py            # 一键跑完全部自检（推荐）
```

也可以单独跑：

```bash
python scripts/verify_dispatch.py       # 6 种题型的分派 + 完整链路（mock 模型，不花 API 费用）
python scripts/verify_imports.py        # 静态检查：未定义的名字 / 失效的相对导入
python scripts/verify_datasets.py       # 题库与元数据：行数缓存 / 原子写 / 卡片文案放得下
python scripts/verify_models.py         # 模型用途：判别器不能被评测 / 改类型二次确认（临时库）
python scripts/verify_safety.py         # 安全评测：判分方向 / 裁判约束 / 判分失败处理（不花钱）
python scripts/calibrate_judge.py --dry-run   # 先看裁判校准要花多少 token，再决定跑不跑
python scripts/verify_scoring.py        # 取数口径：完整/部分评测怎么选，三处是否一致（临时库）
python scripts/verify_sandbox.py        # 8 项隔离验证 + 3 项判分链路验证
python scripts/verify_humaneval.py      # 代码抽取 8 项 + 164 道官方参考解法
python scripts/verify_livecodebench.py  # 判分器 10 项（两种题型 + 四类失败判定）
python scripts/verify_summary.py        # 统计层：护栏 / 显著性 / 与数据库对账
python scripts/verify_summaries.py      # 已生成的 AI 总结与统计事实是否一致
node   scripts/verify_web_templates.mjs # 前端模板：部分评测的标记与最高分高亮（不需要浏览器）
```

需要 Docker 的只有 `verify_dispatch` / `verify_sandbox` / `verify_humaneval` / `verify_livecodebench`；
`verify_web_templates.mjs` 需要 Node（其余是纯 Python）。`verify_dispatch.py` / `verify_scoring.py` / `verify_models.py`
全程使用**临时数据库**，不会往 `data/app.db` 写任何东西，可以随时反复跑
（`verify_models.py` 还会把 `models.json` 的读写也指到临时目录，避免覆盖你真实的 key 文件）。

## 已知限制

- **安全 / 对齐类基准的分数是「裁判模型」给的，不是程序算的**：这是本项目唯一不满足「Python 算、AI 只解释」的一类。
  三条必须知道：① **换裁判分数就不可比**（任务列表里直接显示这次是谁判的）；
  ② 本站只做**直接请求**，官方 HarmBench 榜是「被 18 种攻击方法攻击后」的 ASR、JBB 榜用的是 Llama-3-70B 判官，
  所以**不要和官方榜直接比**；③ 官方 contextual（需两轮铺垫）与 copyright（哈希比对判分）两类未收录。
  换裁判前建议先跑 `scripts/calibrate_judge.py`（用官方人工标注集量一致率，支持 `--dry-run` 先看花费）。
- **裁判可能自己拒答**：安全对齐强的模型读到有害内容时常会拒绝判分，此时该题记为**判分失败**（不计入分母，
  页面上的 failed 会显示）——**绝不默认判成「安全」**，否则越狱成功率会被系统性低估。整场都判分失败时任务会标红并提示换裁判。
- **被测模型没吐正文时也记为失败，不算「安全」**：思考型模型（DeepSeek V4 等）的 reasoning token 也占
  `max_tokens`，额度给太小会让它正文为空 —— 而空回复会被裁判判成「没有有害内容」，整场显示 100% 安全。
  实测踩过（20/20 题正文为空、准确性显示 100%）。所以安全类基准的 `max_tokens` 有 **1024 的下限**，
  且「正文为空 / 只有截断标记」会记成判分失败。省裁判额度靠**截断喂给裁判的正文**（前 2000 字符），
  而不是压低被测模型的输出。
- **服务商可能在自己的网关层就挡掉有害请求**：实测 100 题里有 1 题被测服务商直接返回
  `400 InternalError.Algo.Data...`（看着像内容审核），请求根本没到模型。这类题现在记为**判分失败**
  （不计入「拒绝」也不计入「越狱」）—— 这是保守但诚实的做法：我们无法知道模型本来会不会照做。
  所以分数应当理解成「**服务商允许跑的那些题上**的成绩」，明细里能看到具体是哪些题。
- **安全类基准的分数看起来会「偏高」**：本工具做的是**直接请求**（不注入攻击），
  对齐正常的模型大多会直接拒绝 → HarmBench 这类基准的「正确率」常常 80~100%。
  这正是官方要用 18 种攻击方法的原因；想看出差别就得加上攻击注入（尚未实现）。
- **BFCL multi_turn 为简化实现**：不真实执行工具（不模拟文件系统 / API 状态），改为把环境初始状态直接告诉模型、并用中性结果模拟执行反馈。因此只判「每轮该调什么函数」，**不校验环境最终状态**；且判定较严——一轮内各轮全部正确才算该题通过。
- **BFCL 只覆盖官方权重的 60%**：官方总分 = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%，
  本站提供后四组（含 16 个子集），**缺 Agentic（Web Search + Memory）那 40%** —— 前者要 SerpAPI 付费服务、
  后者要先跑记忆预处理并拉 embedding 模型，与「纯本地、可复现」冲突。所以页面上的「官方加权总分」是**按已跑组归一化**的，
  每行都标明权重覆盖率，不要直接和官方榜的 Overall 比。`live_relevance` 也没收录（官方不计分，且判分口径不同）。
- **Live 里有几个子集题量很小**：`live_parallel` 只有 16 题、`live_parallel_multiple` 24 题，一题就值 4~6 个百分点，
  看趋势可以，别拿它排名次（页面上的小样本提示与 Wilson 区间照常生效）。
- **WinoGrande 是 2 选 1，随机猜就有 50%**：所以它的分数要和「随机基线」比才有意义（60% 和 90% 的差距
  比四选一里大得多）。另外官方 test 划分**没有公开答案**，这里用的是 validation 划分（1267 题）；HellaSwag 同理（10042 题）。
- **ARC-Challenge 的选项不都是 4 个**：官方数据里 22 道题的选项标签是 1/2/3/4、还有 3 道是 5 选 1（A~E），
  下载时按 `choices.label` 的**下标**对齐答案字母，页面上会显示成 A~D/A~E。如果哪天换数据源，这一步要重新核对
  （直接假设「标签就是 ABCD」会把那 22 题全部判错，而且分数只低一点、看不出原因）。
- **HellaSwag 的题干是「情境前缀」，选项是四个后续**：按官方口径原样拼（题干用原始 `ctx`，不额外改写），
  所以模型要理解成「选出最合理的续写」。干扰项是语法通顺但常识上不成立的句子，低分通常意味着常识判断有问题。
- **MATH-500 为近似判分**：答案常是 LaTeX 表达式，目前只做「规范化字符串比对 + 数值容错」，对格式差异大的复杂表达式可能误判。
- **思考型模型注意 `max_tokens`**：DeepSeek V4 等默认开启思考，推理 token 也占输出额度，难题建议设 ≥4096，否则最终答案可能被截断。代码类基准已自动把下限抬到 8192，并在被截断时按 8192 → 32768 → 131072 逐级放大重问。
- **HumanEval 个别题目数据集本身有矛盾**：如 HumanEval/47 的 docstring 示例（`median([-10,4,6,1000,10,20]) = 15.0`）与官方测试（断言 `== 8.0`）互相冲突，照着 docstring 写反而会失败。
- **LiveCodeBench 只取了 v6**：共 175 题（2025 年新增），题目最新、几乎不可能进过训练数据。单条用例 6 秒超时、单题总预算 90 秒，超出预算的用例记为未通过。
- **LiveCodeBench 判分口径已对齐官方**：stdin 式按「行数 + 逐行字符串 + 逐 token Decimal」比较，函数式用 `==` 比较返回值（仅把最外层 tuple 当 list），并复刻了官方判题环境的 `import_string`。
- **沙箱需要 Docker Desktop 在运行**：未启动时评测会在开始前直接失败并给出原因，不会浪费 API 调用。
- **AI 总结是模型写的，不是判定**：统计量由程序算，但文字表述由所选模型生成。它可能措辞不准或漏掉细节 ——
  页面旁边并排给出「已发现的数据问题」与「本次依据的数据」供你核对，总结里也会标出哪个模型生成的
  （如果生成者自己也在被评测之列，会额外提示）。
- **「最高分」只在完整评测之间评**：同一模型在同一基准上跑过多次时，优先取覆盖率 ≥90% 的**完整评测**中的最高分；
  只有在完全没有完整评测时，才退回显示部分评测，并在页面上标出「部分 x/y」、不参与最高分评选、不计入综合平均分。
  排行榜 / 成绩总览 / AI 总结三处共用同一条规则（`app/scoring.py`），不会各自漂移 ——
  所以**跑几题刷出来的高分不会上榜**，想留成绩就得跑完整题库。
- **分类综合榜里「家族算一项」**：BFCL 的 16 个子集不会被当成 16 票投进分类平均分（那会让 Agent 分类变成
  「BFCL 内部细分」），而是用它的**官方加权总分**算一项；排序用「有效覆盖量」（独立基准算 1.0、
  家族按官方权重覆盖率折算），所以「只跑了一个子集」排在「跑满四组」后面 —— 和「覆盖度优先」是同一条政策。
- **`.vendor/` 只在重新下载需要解析 parquet 的题库（如 AIME）时才用得到**：解压后约 82MB，已 gitignore，
  删掉也能自愈（下次下载会自动重装）。

## 安全说明

- **API Key 只保存在本地** `data/app.db` 与 `data/models.json`，不会上传任何服务器
- `data/` 目录已在 `.gitignore` 中排除（仅保留两个无敏感信息的演示样例），**推送代码不会带上你的 Key**
- 页面接口返回的 Key 一律打码

## License

仅供个人学习与评测使用。各 benchmark 数据集版权归原作者，商用前请确认各自许可。
