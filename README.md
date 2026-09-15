# LLM Bench — 大模型基准测评

一个**本地运行的 Web 应用**：填入任意 OpenAI 兼容接口（`base_url` + API Key + 模型名），即可自动跑公开 benchmark、判分、生成排行榜对比。

纯本地部署，**API Key 只保存在你自己电脑上**，不上传任何服务器。

## 功能特性

- **模型管理**：增删模型、连接测试、服务商预设（DeepSeek / 通义 / Kimi / GLM / OpenAI / OpenRouter / Ollama），Key 打码显示
- **题库下载**：一键下载 23 个公开 benchmark 数据集到本地 `data/` 目录；GitHub raw 主源失败会自动回退 ghproxy 加速镜像，HuggingFace 失败回退 hf-mirror
- **评测引擎**：并发调用 API、自动判分、3 次重试、逐题明细导出（JSONL）
- **任务管理**：运行中的任务可随时**停止**（保留已跑进度）；**删除会自动先停止**；支持**批量删除**；被中断的任务可**续跑**（只补剩下的题）
- **排行榜**：按分类分区（通用知识 / 中文能力 / 科学推理 / 数学推理 / Agent·工具调用 / 代码工程），先看「总览（各分类冠军）」，点分类展开「综合榜（分类内平均分）+ 各基准分项榜」
  - 同一模型同一基准的多次测试**只取最高分**
  - 按「**覆盖度优先**」排序，避免只跑简单基准刷分
- **成绩总览**：基准 × 模型的热力矩阵，每行最高分单独标出；点模型名展开能力画像
- **逐题对比**：勾选两个任务，逐题并排看两边答案差异（可只看分歧题）
- **AI 总结**：让任一已接入的模型写一份中文评测总结 —— **统计量由程序算好**（排名、覆盖率、差距是否显著），模型只负责解释，因此换个模型来写数字也不会变
- **多种题型**：
  - 选择题（4 选 1 ~ 10 选 1）：MMLU / C-Eval / CMMLU / MMLU-Pro / GPQA / TruthfulQA
  - 数值题：GSM8K / MATH-500 / AIME（整数答案）
  - 函数调用：BFCL v4（AST 匹配评分）
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
| 数学推理 | GSM8K | 7473 | 数值答案 |
| 数学推理 | MATH-500 | 500 | 数值 / 表达式 |
| 数学推理 | AIME 2022-2024 | 90 | 整数答案 |
| 数学推理 | AIME 2025 | 30 | 整数答案 |
| Agent / 工具调用 | BFCL v4 · 单函数（Python / Java / JavaScript） | 550 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 多函数选择 | 200 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 并行调用 / 并行多选 | 400 | 函数调用 |
| Agent / 工具调用 | BFCL v4 · 无关拒绝 | 240 | 拒绝调用 |
| Agent / 工具调用 | BFCL v4 · 多轮对话（base / 长上下文 / 缺函数 / 缺参数） | 800 | 多轮工具调用 |
| 代码工程 | HumanEval | 164 | 沙箱执行官方单元测试 |
| 代码工程 | LiveCodeBench v6 | 175 | 沙箱执行竞赛测试用例 |
| 演示样例 | MMLU / C-Eval 样例 | 各 12 | 4 选 1 |

> 合计 **25 个基准**（含 2 个演示样例；Agent 分类下 11 个 BFCL v4 子集）。

## 数据集下载

```bash
python scripts/download.py                    # 下载全部 23 个可下载题库
python scripts/download.py cmmlu gpqa         # 下载指定题库
python scripts/download.py BFCL_v4_multi_turn_base   # 下载单个 BFCL 子集
python scripts/download.py humaneval livecodebench   # 代码类题库
```

也可以在网页的基准卡片上点「下载」按钮，由后端自动调用脚本下载（可看到下载状态）。

> 下载源说明：MMLU / C-Eval / GPQA / MMLU-Pro / GSM8K / MATH-500 走 HuggingFace datasets-server；CMMLU / BFCL / HumanEval / TruthfulQA 走 GitHub；AIME 走 HuggingFace parquet；LiveCodeBench 走 HuggingFace 的 jsonl（约 134MB，**只有 v6**）。国内网络可能需要能访问这些源（GitHub raw 失败自动回退 ghproxy，HuggingFace 失败自动回退 hf-mirror）。

## 项目结构

```
app/
  main.py           # FastAPI 路由（模型 / 题库 / 评测任务 / 排行榜 / 下载 / 沙箱状态）
  config.py         # 全局常量：路径 / 选项字母 / 默认参数 / 沙箱并发
  datasets.py       # 题库定位与加载、逐题明细导出
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
  summary.py        # AI 总结：统计层（排名/覆盖率/显著性/护栏）+ 生成
  db.py             # SQLite 封装
  static/index.html # 前端单页（无构建步骤）
scripts/
  download.py           # 统一下载入口（薄 CLI，实现在 app/benchmarks/）
  resume_eval.py        # 续跑被中断的评测任务
  verify_all.py         # 一键跑完下面全部自检
  verify_imports.py     # 静态检查：未定义的名字 / 失效的相对导入
  verify_dispatch.py    # 判分分派：6 种题型各跑一遍完整链路（mock 模型，不花钱）
  verify_sandbox.py     # 沙箱隔离安全验证（跑攻击载荷）
  verify_humaneval.py   # HumanEval 抽取 + 参考解法自检
  verify_livecodebench.py  # LiveCodeBench 判分器自检
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

**加一个基准 = 加一个文件**（或往同源模块的 `ENTRIES` 里加一条），不用改别处。
以前要同时改四处（`benchmarks.py` 的 META、`download.py` 的两份字典、以及下载函数所在的脚本），
实测某个基准 id 散落在 10 个文件里。

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
python scripts/verify_sandbox.py        # 8 项隔离验证 + 3 项判分链路验证
python scripts/verify_humaneval.py      # 代码抽取 8 项 + 164 道官方参考解法
python scripts/verify_livecodebench.py  # 判分器 10 项（两种题型 + 四类失败判定）
python scripts/verify_summary.py        # 统计层：护栏 / 显著性 / 与数据库对账
python scripts/verify_summaries.py      # 已生成的 AI 总结与统计事实是否一致
```

前两个不需要 Docker，其余需要。`verify_dispatch.py` 全程使用**临时数据库**，
不会往 `data/app.db` 写任何东西，可以随时反复跑。

## 已知限制

- **BFCL multi_turn 为简化实现**：不真实执行工具（不模拟文件系统 / API 状态），改为把环境初始状态直接告诉模型、并用中性结果模拟执行反馈。因此只判「每轮该调什么函数」，**不校验环境最终状态**；且判定较严——一轮内各轮全部正确才算该题通过。
- **MATH-500 为近似判分**：答案常是 LaTeX 表达式，目前只做「规范化字符串比对 + 数值容错」，对格式差异大的复杂表达式可能误判。
- **思考型模型注意 `max_tokens`**：DeepSeek V4 等默认开启思考，推理 token 也占输出额度，难题建议设 ≥4096，否则最终答案可能被截断。代码类基准已自动把下限抬到 8192，并在被截断时按 8192 → 32768 → 131072 逐级放大重问。
- **HumanEval 个别题目数据集本身有矛盾**：如 HumanEval/47 的 docstring 示例（`median([-10,4,6,1000,10,20]) = 15.0`）与官方测试（断言 `== 8.0`）互相冲突，照着 docstring 写反而会失败。
- **LiveCodeBench 只取了 v6**：共 175 题（2025 年新增），题目最新、几乎不可能进过训练数据。单条用例 6 秒超时、单题总预算 90 秒，超出预算的用例记为未通过。
- **LiveCodeBench 判分口径已对齐官方**：stdin 式按「行数 + 逐行字符串 + 逐 token Decimal」比较，函数式用 `==` 比较返回值（仅把最外层 tuple 当 list），并复刻了官方判题环境的 `import_string`。
- **沙箱需要 Docker Desktop 在运行**：未启动时评测会在开始前直接失败并给出原因，不会浪费 API 调用。
- **AI 总结是模型写的，不是判定**：统计量由程序算，但文字表述由所选模型生成。它可能措辞不准或漏掉细节 ——
  页面旁边并排给出「已发现的数据问题」与「本次依据的数据」供你核对，总结里也会标出哪个模型生成的
  （如果生成者自己也在被评测之列，会额外提示）。
- **排行榜与成绩总览仍按「最高分」取数，不看覆盖率**：只跑了 6 题的 100% 会盖过跑满 164 题的 99%，
  所以**不要用部分评测刷分**。AI 总结里已经有「刷分假象」护栏，但那两处还没同步（已知待办）。
- **`.vendor/` 只在重新下载需要解析 parquet 的题库（如 AIME）时才用得到**：解压后约 82MB，已 gitignore，
  删掉也能自愈（下次下载会自动重装）。

## 安全说明

- **API Key 只保存在本地** `data/app.db` 与 `data/models.json`，不会上传任何服务器
- `data/` 目录已在 `.gitignore` 中排除（仅保留两个无敏感信息的演示样例），**推送代码不会带上你的 Key**
- 页面接口返回的 Key 一律打码

## License

仅供个人学习与评测使用。各 benchmark 数据集版权归原作者，商用前请确认各自许可。
