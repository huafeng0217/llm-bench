# LLM Bench — 大模型基准测评

一个**本地运行的 Web 应用**：填入任意 OpenAI 兼容接口（`base_url` + API Key + 模型名），即可自动跑公开 benchmark、判分、生成排行榜对比。

纯本地部署，**API Key 只保存在你自己电脑上**，不上传任何服务器。

## 功能特性

- **模型管理**：增删模型、连接测试、服务商预设（DeepSeek / 通义 / Kimi / GLM / OpenAI / OpenRouter / Ollama），Key 打码显示
- **题库下载**：一键下载 12 个公开 benchmark 数据集到本地 `data/` 目录
- **评测引擎**：并发调用 API、自动判分、3 次重试、逐题明细导出（JSONL）
- **排行榜**：按「模型 × 基准」取最高正确率排名
- **停止评测**：运行中的任务可随时停止（保留进度）
- **多种题型**：
  - 选择题（4 选 1 ~ 10 选 1）：MMLU / C-Eval / CMMLU / MMLU-Pro 等
  - 数值题：GSM8K / MATH-500
  - 函数调用：BFCL v4（AST 匹配评分）

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
4. **查看结果**：评测任务列表看进度/正确率，点「明细」看逐题，排行榜看对比

## 支持的数据集

| 分类 | 数据集 | 题量 | 题型 |
|---|---|---|---|
| 通用知识 | MMLU | 约 1.4 万 | 4 选 1 |
| 通用知识 | MMLU-Pro | 约 1.2 万 | 10 选 1 |
| 中文能力 | C-Eval | 1346 | 4 选 1 |
| 中文能力 | CMMLU | 约 1.1 万 | 4 选 1 |
| 科学推理 | GPQA Diamond | 198 | 4 选 1 |
| 数学推理 | GSM8K | 7473 | 数值答案 |
| 数学推理 | MATH-500 | 500 | 数值/表达式 |
| Agent / 工具调用 | BFCL v4（5 子集） | 1240 | 函数调用 |
| 演示样例 | MMLU / C-Eval 样例 | 各 12 | 4 选 1 |

## 数据集下载

```bash
python scripts/download.py              # 下载全部 12 个可下载题库
python scripts/download.py cmmlu gpqa   # 下载指定题库
```

也可以在网页的基准卡片上点「下载」按钮，由后端自动调用脚本下载。

> 下载源说明：MMLU / C-Eval / GPQA / MMLU-Pro / GSM8K / MATH-500 走 HuggingFace datasets-server；CMMLU / BFCL 走 GitHub（GitHub raw 主源失败时自动回退 ghproxy 镜像）。国内网络可能需要能访问这些源。

## 项目结构

```
app/
  main.py          # FastAPI 路由（模型 / 题库 / 评测任务 / 排行榜 / 下载）
  engine.py        # 评测引擎：加载题库、并发调用、判分、落库、导出
  benchmarks.py    # benchmark 元数据（名称/分类/说明/来源）
  db.py            # SQLite 封装
  static/index.html # 前端单页（无构建步骤）
scripts/
  download.py      # 统一下载入口
  download_datasets.py  # MMLU / C-Eval 下载
  download_more.py      # CMMLU / GPQA / MMLU-Pro / GSM8K / MATH-500 下载
  download_bfcl.py      # BFCL v4 下载
data/              # 题库、数据库、评测结果（已 gitignore，仅保留样例）
requirements.txt
```

## 安全说明

- **API Key 只保存在本地** `data/app.db` 与 `data/models.json`，不会上传任何服务器
- `data/` 目录已在 `.gitignore` 中排除（仅保留两个无敏感信息的演示样例），**推送代码不会带上你的 Key**
- 页面接口返回的 Key 一律打码

## License

仅供个人学习与评测使用。各 benchmark 数据集版权归原作者，商用前请确认各自许可。
