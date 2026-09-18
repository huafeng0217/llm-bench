/**
 * 前端模板自检：把 index.html 里的渲染函数抠出来，用「含部分评测」的假数据渲染一遍。
 *
 * 为什么需要它
 * ------------
 * 排行榜和成绩总览是纯前端拼字符串（没有构建步骤、没有框架），模板分支只能靠人眼看。
 * 这次加的「部分评测」分支正是最容易被忽略的那类改动 —— 只有真的有部分评测数据时
 * 才会走到，而正常运行下**一条都不会触发**（当前 33 个格子全部是完整评测）。
 * 也就是说：写错了也不会有人发现，直到某天有人跑了个短评测，页面直接白屏。
 *
 * 所以这里不启动服务器、不依赖浏览器，直接把函数抠出来喂假数据，断言渲染结果：
 *   1. 部分评测格**不参与**「该基准最高分」高亮，并且带「部分 x/y」标记；
 *   2. 完整评测格照旧拿高亮；
 *   3. 排行榜里部分评测不排名次、排在后面、综合排行提示不计入平均。
 *
 * 需要 Node（只有这一个脚本需要）。没装 Node 时 verify_all.py 会标 SKIP，不算失败。
 *
 * 用法::
 *
 *     node scripts/verify_web_templates.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const HTML = path.join(ROOT, "app", "static", "index.html");

const src = fs.readFileSync(HTML, "utf8");
// 统一换行：文件是 CRLF，而下面靠 "\n}\n" 找函数结尾
const script = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"))
  .replace(/\r\n/g, "\n");

/** 抠出 index.html 里一个顶层函数的完整源码（靠行首的 } 找结尾）。 */
function grab(name) {
  const i = script.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`找不到 function ${name} —— 函数改名了就该更新本脚本`);
  const j = script.indexOf("\n}\n", i);
  if (j < 0) throw new Error(`找不到 function ${name} 的结尾`);
  return script.slice(i, j + 3);
}

const results = [];
const check = (name, ok, detail = "") => results.push([name, !!ok, detail]);
const escLine = script.split("\n").find(l => l.startsWith("const esc = "));
if (!escLine) throw new Error("找不到 esc 的定义");

// ---------------- 成绩总览 ----------------
const ovCode = [escLine, grab("drawOverview"), grab("renderOvProfile")].join("\n");

// 同一基准上：模型甲 163/164 完整 99.39%，模型乙只有 6/164 的部分评测却 100%
const ovData = {
  models: [{ id: 1, name: "模型甲" }, { id: 2, name: "模型乙" }],
  groups: [{
    id: "code", name: "代码工程", color: "#e08a3c",
    benchmarks: [{
      id: "humaneval", name: "HumanEval",
      scores: {
        1: { accuracy: 99.39, total: 163, full_count: 164, partial: false, eval_id: 11, avg_latency_ms: 100 },
        2: { accuracy: 100.0, total: 6, full_count: 164, partial: true, eval_id: 12, avg_latency_ms: 50 },
      },
    }],
  }],
};

const box = { innerHTML: "" };
const doc = { getElementById: (id) => (id === "overview" ? box : null) };
const ovHtml = new Function("ovData", "ovOnlyData", "ovPickModel", "document",
  ovCode + "\ndrawOverview();\nreturn document.getElementById('overview').innerHTML;"
)(ovData, true, 2, doc);

check("总览：完整成绩拿到最高分高亮", ovHtml.includes("ov-cell ov-top"));
check("总览：部分评测格不参与最高分高亮（100% 也不高亮）",
  !/ov-cell ov-partial"[^>]*>\s*100%<\/td>/.test(ovHtml),
  "100% 的部分评测被当成了最高分");
check("总览：部分评测格带 ov-partial 标记", ovHtml.includes("ov-cell ov-partial"));
check("总览：部分评测格显示「部分 6/164」", ovHtml.includes("部分 6/164"));
check("总览：图例说明不参与最高分评选", ovHtml.includes("不参与最高分评选"));
check("总览：点模型名仍能展开能力画像", ovHtml.includes("模型乙 的能力画像"));

// ---------------- 排行榜 ----------------
// 从 famCover 开始切片：家族块用到的辅助函数与模板都在这一段里，
// 连同 pct 一起注入，测的就是页面上真正跑的那几行代码。
const i0 = script.indexOf("const famCover = m =>");
const i1 = script.indexOf('document.getElementById("board").innerHTML');
if (i0 < 0 || i1 < 0) throw new Error("找不到排行榜模板片段（refresh 里的 famCover/overview/famBlock/details）");
const pctLine = script.split("\n").find(l => l.startsWith("const pct = "));
if (!pctLine) throw new Error("找不到 pct 的定义");

const bds = [{
  id: "code", name: "代码工程", color: "#e08a3c", n_benchmarks: 1,
  combined: [
    { model_name: "模型甲", avg_accuracy: 99.39, covered: 1, total: 1, partial: 0, families: [] },
    { model_name: "模型乙", avg_accuracy: 0, covered: 1, total: 1, partial: 1, families: [] },
  ],
  boards: [{
    benchmark: "humaneval", benchmark_name: "HumanEval", family: "", group: "", group_name: "", group_weight: null,
    rows: [
      { model_name: "模型甲", benchmark: "humaneval", benchmark_name: "HumanEval",
        accuracy: 99.39, total: 163, full_count: 164, partial: false,
        avg_latency_ms: 100, prompt_tokens: 10, completion_tokens: 20 },
      { model_name: "模型乙", benchmark: "humaneval", benchmark_name: "HumanEval",
        accuracy: 100.0, total: 6, full_count: 164, partial: true,
        avg_latency_ms: 50, prompt_tokens: 1, completion_tokens: 2 },
    ],
  }],
  families: [],
}, {
  // 家族分类：子集不单独出现在 combined 里，而是折叠进 families 块
  id: "agent", name: "Agent / 工具调用", color: "#e0952f", n_benchmarks: 1,
  combined: [{
    model_name: "模型甲", avg_accuracy: 42.65, covered: 1, total: 1, partial: 1, coverage: 0.5,
    families: [{ id: "BFCL v4", score: 42.65, weight_covered: 0.5, groups_covered: 3,
                 groups_total: 4, subsets_run: 4, subsets_total: 16, partial: true }],
  }],
  boards: [],
  families: [{
    id: "BFCL v4", name: "BFCL v4",
    note: "官方总分 = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%",
    source: "https://gorilla.cs.berkeley.edu/leaderboard",
    groups: [
      { id: "non_live", name: "Non-Live", weight: 0.1, order: 1, subsets: ["a", "b"] },
      { id: "live", name: "Live", weight: 0.1, order: 2, subsets: ["c"] },
      { id: "multi_turn", name: "Multi-Turn", weight: 0.3, order: 3, subsets: ["d"] },
      { id: "hallucination", name: "Hallucination", weight: 0.1, order: 4, subsets: ["e", "f"] },
      { id: "agentic", name: "Agentic（Web Search + Memory）", weight: 0.4, order: 5, subsets: [] },
    ],
    rows: [{
      model_name: "模型甲", score: 42.65, weight_covered: 0.5, weight_total: 1.0,
      groups_covered: 3, groups_total: 4, subsets_run: 6, subsets_total: 9, partial: true, incomplete: true,
      groups: [
        { id: "non_live", name: "Non-Live", weight: 0.1, order: 1, run: 2, total: 2, score: 61.0 },
        { id: "live", name: "Live", weight: 0.1, order: 2, run: 0, total: 1, score: null },
        { id: "multi_turn", name: "Multi-Turn", weight: 0.3, order: 3, run: 3, total: 4, score: 29.67 },
        { id: "hallucination", name: "Hallucination", weight: 0.1, order: 4, run: 1, total: 2, score: 78.75 },
        { id: "agentic", name: "Agentic（Web Search + Memory）", weight: 0.4, order: 5, run: 0, total: 0, score: null },
      ],
    }],
    boards: [{
      benchmark: "BFCL_v4_simple_python", benchmark_name: "BFCL v4 · 单函数",
      family: "BFCL v4", group: "non_live", group_name: "Non-Live", group_weight: 0.1,
      rows: [{ model_name: "模型甲", benchmark: "BFCL_v4_simple_python",
               benchmark_name: "BFCL v4 · 单函数", accuracy: 65.0, total: 400, full_count: 400,
               partial: false, avg_latency_ms: 900, prompt_tokens: 5000, completion_tokens: 900 }],
    }, {
      benchmark: "BFCL_v4_live_simple", benchmark_name: "BFCL v4 · Live 单函数",
      family: "BFCL v4", group: "live", group_name: "Live", group_weight: 0.1,
      rows: [{ model_name: "模型甲", benchmark: "BFCL_v4_live_simple",
               benchmark_name: "BFCL v4 · Live 单函数", accuracy: 24.5, total: 258, full_count: 258,
               partial: false, avg_latency_ms: 800, prompt_tokens: 4000, completion_tokens: 700 }],
    }],
  }],
}];

const boardHtml = new Function("bds", "expandedCats", "esc",
  pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
)(bds, { code: true, agent: true }, s => String(s ?? ""));

check("排行榜：部分评测带「部分 6/164」标签", boardHtml.includes("部分 6/164"));
check("排行榜：部分评测不排名次", boardHtml.includes('title="只跑了部分题库，不排名次"'));
check("排行榜：完整评测仍有名次徽章", boardHtml.includes('class="rank r1"'));
check("排行榜：综合排行提示部分评测不计入平均", boardHtml.includes("1 项为部分"));
check("排行榜：措辞已改成「完整评测中的最高分」", boardHtml.includes("完整评测中的最高分"));

// 家族折叠块 + 官方加权总分
check("家族：折叠块渲染出官方加权总分", boardHtml.includes("官方加权总分"));
check("家族：显示权重覆盖（50%）", boardHtml.includes("<b>50%</b>"));
check("家族：显示已跑组数与子集数", boardHtml.includes("3/4 组") && boardHtml.includes("6/9 子集"));
check("家族：本项目提供的每个官方分组各一列",
  [">Non-Live</th>", ">Live</th>", ">Multi-Turn</th>", ">Hallucination</th>"]
    .every(s => boardHtml.includes(s)));
check("家族：本项目未提供的分组（Agentic）不占一列空列", !boardHtml.includes(">Agentic</th>"));
check("家族：未跑的分组显示 — 而不是 0", boardHtml.includes('title="未跑（共 1 个子集）"') && boardHtml.includes(">—</td>"));
check("家族：组内只跑了一部分子集时标出 (run/total)",
  boardHtml.includes("(3/4)") && boardHtml.includes("(1/2)"));
check("家族：综合榜的覆盖列标出权重覆盖", boardHtml.includes("BFCL v4 权重 50%"));
check("家族：说明了口径，并标出官方权重覆盖 60%",
  boardHtml.includes("组内先取子集平均") && boardHtml.includes("官方权重覆盖 60%"));
check("家族：子集明细默认收起（details）", boardHtml.includes("<details") && boardHtml.includes("子集明细（有成绩的 2 个"));
check("家族：分组总数按定义算，不是按有成绩的子集数", boardHtml.includes("4 个官方分组 · 6 个子集"));
check("家族：子集明细带官方分组标签", boardHtml.includes(">Non-Live<br>") && boardHtml.includes("权重 10%"));
check("家族：子集名去掉了家族前缀", boardHtml.includes(">单函数</td>") && !boardHtml.includes(">BFCL v4 · 单函数</td>"));

// ---------------- 选择基准：家族折叠成一张卡 + 子集切换器 ----------------
const g0 = script.indexOf("function familyCard(");
const g1 = script.indexOf("async function downloadFamily(");
if (g0 < 0 || g1 < 0) throw new Error("找不到 familyCard 模板片段");

const bms = [
  { id: "BFCL_v4_simple_python", name: "BFCL v4 · 单函数", category: "Agent / 工具调用",
    lang: "英文", status: "仍有区分度", summary: "最基础的函数调用：给一个工具，看会不会选错函数、漏参数或类型不对",
    description: "单函数子集说明", source: "https://x",
    family: "BFCL v4", group: "non_live", group_name: "Non-Live", group_weight: 0.1,
    family_note: "官方总分 = Agentic×40% + Multi-Turn×30% + …", count: 400, downloadable: true },
  { id: "BFCL_v4_simple_java", name: "BFCL v4 · 单函数(Java)", category: "Agent / 工具调用",
    lang: "英文", status: "仍有区分度", summary: "单函数的 Java 版：工具定义换成 Java，看参数写法会不会跟着跑偏",
    description: "Java 说明", source: "https://x",
    family: "BFCL v4", group: "non_live", group_name: "Non-Live", group_weight: 0.1,
    family_note: "官方总分 = Agentic×40% + Multi-Turn×30% + …", count: 0, downloadable: true },
  { id: "BFCL_v4_live_parallel", name: "BFCL v4 · Live 并行调用", category: "Agent / 工具调用",
    lang: "多语", status: "仍有区分度", summary: "真实提问里要同时调多个函数；题量太小，只看趋势、别排名次",
    description: "Live 并行说明", source: "https://x",
    family: "BFCL v4", group: "live", group_name: "Live", group_weight: 0.1,
    family_note: "官方总分 = Agentic×40% + Multi-Turn×30% + …", count: 16, downloadable: true },
];

const familyHtml = new Function("fid", "bms", "selBenchmark", "familySel", "downloadStates",
  "esc", "pct", "statusTag",
  grab("statusTag") + "\n" + script.slice(g0, g1) + "\nreturn familyCard(fid, bms);"
)("BFCL v4", bms, "BFCL_v4_live_parallel", {}, { BFCL_v4_simple_java: { status: "running", message: "下载中" } },
  s => String(s ?? ""), w => Math.round(w * 100) + "%", s => `<span class="tag">${s}</span>`);

check("选择基准：多个子集折叠成一张卡", familyHtml.split('class="card bm').length === 2);
check("选择基准：卡片里有子集切换器", familyHtml.includes('<select class="famsel"'));
check("选择基准：下拉列出全部子集", bms.every(b => familyHtml.includes(`value="${b.id}"`)));
check("选择基准：选项里带官方分组名", familyHtml.includes("Non-Live · 单函数") && familyHtml.includes("Live · Live 并行调用"));
check("选择基准：选项里带上简介，便于在 16 个子集之间挑", familyHtml.includes("Live · Live 并行调用 — 真实提问里要同时调多个函数；题量太小，只看趋势、别排名次"));
check("选择基准：未下载的子集在选项里标出", familyHtml.includes("（未下载）") && familyHtml.includes("（400 题）"));
check("选择基准：卡片标出当前子集的官方分组与权重", familyHtml.includes("Live 组 · 官方权重 10%"));
check("选择基准：显示已就绪数量", familyHtml.includes("已就绪 2/3"));
check("选择基准：显示官方总分口径", familyHtml.includes("官方总分口径：官方总分 = Agentic×40%"));
check("选择基准：当前选中的是 selBenchmark 那个子集",
  familyHtml.includes('data-id="BFCL_v4_live_parallel"') && familyHtml.includes('value="BFCL_v4_live_parallel" selected'));
check("选择基准：有子集缺失时提供「下载全部」", familyHtml.includes("下载全部 3 个子集"));
check("选择基准：全部就绪时不显示「下载全部」",
  !new Function("fid", "bms", "selBenchmark", "familySel", "downloadStates", "esc", "pct", "statusTag",
    grab("statusTag") + "\n" + script.slice(g0, g1) + "\nreturn familyCard(fid, bms);"
  )("BFCL v4", bms.map(b => ({ ...b, count: 10 })), "", {}, {},
    s => String(s ?? ""), w => Math.round(w * 100) + "%", s => `<span class="tag">${s}</span>`)
    .includes("下载全部"));

// ---------------- 卡片简介：一行 summary + 选中后展开完整说明 ----------------
// 起因：以前卡片直接把长 description 截成两行，30 个基准里约 2/3 看不全（最长的砍掉六成）。
const plain = {
  id: "humaneval", name: "HumanEval", category: "代码工程", lang: "英文",
  status: "仍有区分度", summary: "给函数签名与 docstring 让模型补全函数体，官方单测真跑一遍，糊不过去",
  description: "164 道手写编程题，每题给函数签名与 docstring，模型补全函数体，官方单元测试真跑一遍判分。",
  source: "https://x", count: 164, downloadable: true,
};
const mkCard = b => new Function("b", "selBenchmark", "downloadStates", "sbxState", "esc", "statusTag",
  grab("statusTag") + "\n" + grab("bmCard") + "\nreturn bmCard(b);"
)(b, "", {}, null, s => String(s ?? ""), s => `<span class="tag">${s}</span>`);
const cardHtml = mkCard(plain);

check("卡片：正文只放一行简介（summary）", cardHtml.includes(">给函数签名与 docstring 让模型补全函数体，官方单测真跑一遍，糊不过去</div>"));
check("卡片：摘要行的正文就是 summary，完整 description 不再内联进卡片",
  /class="desc" title="点击查看完整简介"/.test(cardHtml)
  && !cardHtml.includes("164 道手写编程题"));
check("卡片：简介文字可点开弹层（不再就地展开）",
  /class="desc"[^>]*showBmDetail\(/.test(cardHtml) && !cardHtml.includes("desc-full"));
check("卡片：正文有「简介」按钮可打开弹层", /class="ghost"[^>]*showBmDetail\(/.test(cardHtml));
check("卡片：没有 summary 时退回显示 description",
  mkCard({ ...plain, summary: "" })
    .includes(">164 道手写编程题，每题给函数签名与 docstring，模型补全函数体，官方单元测试真跑一遍判分。</div>"));
check("家族卡片：同样只显示简介 + 可点开弹层",
  familyHtml.includes(">真实提问里要同时调多个函数；题量太小，只看趋势、别排名次</div>")
  && /class="desc"[^>]*showBmDetail\(/.test(familyHtml));

// ---------------- 评测提示：选中后在这里给出完整说明 ----------------
const hintSrc = script.slice(script.indexOf("async function updateEvalHint("),
                              script.indexOf("async function startEval("));
const hintEls = { "e-model": { options: [{ text: "模型甲" }], selectedIndex: 0 },
                  "start-btn": { style: {} }, "e-hint": { innerHTML: "" } };
const hintHtml = await new Function("benchMeta", "selBenchmark", "esc", "pct", "document",
  "sbxState", "probeSandbox", "loadBenchmarks",
  hintSrc + "\nreturn updateEvalHint().then(() => document.getElementById('e-hint').innerHTML);"
)({ "BFCL_v4_live_parallel": { ...bms[2], summary: "真实提问里要同时调多个函数；题量太小，只看趋势、别排名次" } },
  "BFCL_v4_live_parallel", s => String(s ?? ""), w => Math.round(w * 100) + "%",
  { getElementById: id => hintEls[id] }, null, async () => ({ available: true, message: "" }), () => {});

check("评测提示：显示基准名而不是 id", hintHtml.includes("基准【BFCL v4 · Live 并行调用】"));
check("评测提示：标出官方分组与权重", hintHtml.includes("Live 组 · 官方权重 10%"));
check("评测提示：给出完整说明（不只是那一行简介）", hintHtml.includes("Live 并行说明"));
check("评测提示：同时给出那一行简介", hintHtml.includes("真实提问里要同时调多个函数"));

// ---------------- 模型用途：判别器不能出现在评测下拉里 ----------------
// 后端也会拦（verify_models.py 断言 400），但 UI 不该让人白选一场。
const modelsSrc = script.slice(script.indexOf("async function loadModels("),
                               script.indexOf("function applyPreset("));
if (!modelsSrc) throw new Error("找不到 loadModels");
const modelList = [
  { id: 1, name: "被测甲", base_url: "u1", api_key: "***", kind: "test", evaluations: 3 },
  { id: 2, name: "裁判乙", base_url: "u2", api_key: "***", kind: "judge", evaluations: 2 },
  { id: 3, name: "被测丙", base_url: "u3", api_key: "***", kind: "test", evaluations: 0 },
];
const els = {};
await new Function("api", "document", "esc", "updateEvalHint",
  modelsSrc + "\nreturn loadModels();"
)(async () => modelList,
  { getElementById: id => els[id] || (els[id] = { innerHTML: "", value: "", style: {} }) },
  s => String(s ?? ""), () => {});

const modelHtml = els["models"].innerHTML;
check("模型表：判别器标出「判别器」", modelHtml.includes(">判别器</span>"));
check("模型表：被测模型标出「被测模型」", modelHtml.includes(">被测模型</span>"));
check("模型表：显示历史评测条数（改类型时要用）", modelHtml.includes("3 条历史评测"));
check("模型表：判别器的按钮是「改回被测」", modelHtml.includes(">改回被测</button>"));
check("模型表：被测模型的按钮是「设为判别器」", modelHtml.includes(">设为判别器</button>"));
check("评测下拉：排除判别器", !els["e-model"].innerHTML.includes("裁判乙"));
check("评测下拉：保留被测模型",
  els["e-model"].innerHTML.includes("被测甲") && els["e-model"].innerHTML.includes("被测丙"));
check("AI 总结下拉：不过滤（判别器写总结是「使用」，不是「被评测」）",
  els["s-model"].innerHTML.includes("裁判乙"));

// ---------------- 样式约定（CSS 写错在页面上看不出来，只能靠断言钉住） ----------------
// 卡片：网格必须保持默认 stretch（同行等高、底部对齐），展开的完整说明必须是**绝对定位浮层**——
// 两者缺一：只 stretch 不浮层 → 点开一张会把整行撑高（用户报过）；只浮层不 stretch → 底部参差（用户也报过）。
check("卡片网格保持默认 stretch（同行等高、底部对齐）",
  !/\.grid\s*\{[^}]*align-items:\s*start/.test(src),
  "加了 align-items:start 会让同行卡片底部参差不齐");
check("卡片是竖排 flex + 页脚 margin-top:auto（按钮落在同一条水平线上）",
  /\.bm\s*\{[^}]*flex-direction:\s*column/.test(src)
  && /\.bm\s+\.foot\s*\{[^}]*margin-top:\s*auto/.test(src));
// 完整简介必须走**弹层**：就地展开（撑高同行）和绝对定位浮层（盖住下面的卡片/评测栏）都被否掉了。
check("完整简介走弹层，卡片里不再有就地展开的 desc-full", !/desc-full/.test(src));
check("弹层有背景遮罩、且能点背景/✕/Esc 关闭",
  /\.modal\s*\{[^}]*position:\s*fixed/.test(src) && /id="bm-modal"/.test(src)
  && /onclick="hideBmDetail\(\)"/.test(src) && /showBmDetail/.test(src)
  && /if \(box && !box\.hidden\) \{ hideBmDetail\(\); return; \}/.test(src));
check("弹层内容不止简介（分类/题量/分组权重/需裁判等标签 + 来源 + 选中按钮）",
  /bm-modal-tags/.test(src) && /需裁判模型/.test(src) && /选中这个基准/.test(src)
  && /function pickBenchmarkById/.test(src));
check("能点空白处/Esc 取消基准选择",
  /function clearBenchmarkSel\s*\(/.test(src) && /document\.addEventListener\("click"/.test(src)
  && /Escape/.test(src));
// 页脚控件必须「同款」：以前按钮是描边小按钮、来源是 12px 裸链接（无边框无内边距），
// 一行里高矮胖瘦不齐（用户报「丑、不协调」）；弹层里还拿描边灰按钮当主操作，主次不分。
// 这类问题在页面上只能靠肉眼发现，所以把「同款」钉成断言。
check("页脚的链接与按钮是同款小胶囊（同边框 + 同内边距 5px 12px + 同字号 12.5px）",
  /\.bm \.foot a,\s*\.modal \.foot a\s*\{[^}]*border:1px solid var\(--border2\)[^}]*padding:5px 12px[^}]*font-size:12\.5px[^}]*\}/.test(src)
  && /\.foot \.ghost\s*\{[^}]*font-size:12\.5px/.test(src),
  "页脚里的按钮和来源链接必须同边框/同内边距/同字号，否则一行控件看着不齐");
check("弹层页脚的主操作是实心小号按钮（不再拿描边灰当主操作）",
  /button\.sm\s*\{[^}]*padding:5px 14px/.test(src)
  && /class="sm"[^>]*onclick="hideBmDetail\(\); pickBenchmarkById\(/.test(src)
  && !/class="ghost"[^>]*选中这个基准/.test(src),
  "主操作要和次要操作（关闭/来源）在样式上分开：实心 vs 描边");
check("弹层页脚是 flex（否则 margin-left:auto 是空操作，主按钮不会靠到右边）",
  /\.modal \.foot\s*\{[^}]*display:flex/.test(src));
check("取消选择时不会误伤交互（卡片/下拉/按钮内的点击不算空白处）",
  /closest\("\.bm, \.card, button, select, input, a, label, summary, details"\)/.test(src));
// 表格：表头必须和该列数据同对齐方式，否则每列错位半格（用户报过）
check("表头与正文默认居中（数值/进度/状态/按钮列）",
  /th,td\s*\{[^}]*text-align:\s*center/.test(src));
check("文本列用 .tl 左对齐（表头跟着左对齐）", /th\.tl,\s*td\.tl\s*\{[^}]*text-align:\s*left/.test(src));
check("评测任务的「模型/基准」列标了 tl（文本列左对齐）",
  /<th class="tl">模型<\/th><th class="tl">基准<\/th>/.test(src));

// 两张表的行是在别的函数里拼的（不在 <table> 块内），所以单独精确断言正文单元格 ——
// 全局数量检查太松，少标一格它发现不了（反向验证过）。
check("模型管理：正文的模型 / base_url / Key 单元格都标了 tl",
  /<td class="[^"]*\btl\b[^"]*" style="font-weight:550">\$\{esc\(m\.name\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.base_url\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.api_key\)\}/.test(src));
check("评测任务：正文的模型 / 基准单元格都标了 tl",
  /<td class="[^"]*\btl\b[^"]*" style="font-weight:550">\$\{esc\(e\.model_name\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*" title="输出上限/.test(src));

// 通用守卫：文本列（表头 tl）在正文里也要标 tl —— 少标一处那一列就错位半格。
// 两个注意点：① class 可能写成 "tl muted"，所以按词匹配而不是全等；
// ② 有两张表的行是在别的函数里拼的（模型管理 / 评测任务），它们不在 <table> 块内，
//    按块比对会误报，所以对这类表只做文件级的总量检查。
const TL = /class="[^"]*\btl\b/;
const nThAll = (src.match(/<th class="[^"]*\btl\b/g) || []).length;
const nTdAll = (src.match(/<td class="[^"]*\btl\b/g) || []).length;
check("文本列表头(tl)在正文里都有对应单元格(tl)", nTdAll >= nThAll,
  `表头 ${nThAll} 个 tl，正文只有 ${nTdAll} 个`);
const mismatch = [];
[...src.matchAll(/<table[\s\S]*?<\/table>/g)].map(m => m[0]).forEach((blk, i) => {
  const nTh = (blk.match(/<th class="[^"]*\btl\b/g) || []).length;
  const nTd = (blk.match(/<td class="[^"]*\btl\b/g) || []).length;
  if (nTh && nTd && nTd < nTh) mismatch.push(`第 ${i + 1} 张表：表头 ${nTh} 个 tl，正文只有 ${nTd} 个`);
});
check("表内联渲染的表：文本列表头与正文的 tl 数量匹配", mismatch.length === 0, mismatch.join("；"));

// 「判分/执行失败」和「答错」在页面上必须一眼可分：用户实测那条 jbb_benign 有 5% 是
// 失败（模型没产出正文），若和答错混在一起，会被读成「能力差 5%」——数字没错、结论错。
check("任务列表：正确率的说明里写明分母含失败题、会因此偏低",
  /class="acc" title="[^"]*分母是已完成的题数，失败的那 \$\{failed\} 题也在里面[^"]*"/.test(src));
// 只给 >0 的行追加一截字，就会出现「有的标有的没标」的不协调（用户报过）——
// 所以失败数独立成一列：每行都有值（0 灰 / N 红），状态列只放状态。
check("任务列表：失败数独立成列（表头 + 空表 colspan 跟着加一）",
  /<th>进度<\/th><th>正确率<\/th><th style="width:56px">失败<\/th><th>状态<\/th>/.test(src)
  && /<tbody id="evals"><tr><td colspan="8" class="empty">/.test(src)
  && /colspan="\$\{batchMode \? 9 : 8\}"/.test(src));
check("任务列表：失败列每行都有值（0 灰 / N 红），不再有的标有的没标",
  /failed \? `<span class="st-failed"[^>]*>\$\{failed\}<\/span>`\s*:\s*`<span class="muted">0<\/span>`/.test(src));
check("任务列表：状态列只放状态（失败数不再挤进来）",
  /class="\$\{stCls\}">\$\{ST_TEXT\[e\.status\] \|\| e\.status\}<\/td>/.test(src));
check("任务列表：主数字保持保守口径，旁边并列一个「有效题 X%」（有失败时才出现）",
  /有效题 \$\{validAcc\}%/.test(src)
  && /validAcc = failed && e\.done \? Math\.round\(e\.correct \/ \(e\.done - failed\) \* 1000\) \/ 10 : null/.test(src)
  // 必须是**行内**的：换成块级元素会把这些行撑高，又变成「有的高有的矮」（用户报过同类问题）
  && !/有效题 \$\{validAcc\}%<\/div>/.test(src),
  "未跑成的题不能从主数字里消失，但也不能让主数字独占解释权");
check("任务列表：失败数带说明（失败 ≠ 答错，别读成能力差）",
  /title="判分 \/ 执行失败：[^"]*既不算对也不算答错[^"]*/.test(src));
check("逐题明细：顶部把「正确 / 答错 / 判分失败」三档分开列",
  /evalsCache/.test(src) && /答错 \$\{done - ok - bad\}/.test(src)
  && /判分\/执行失败 \$\{bad\}/.test(src));
check("逐题明细：说明「失败 ≠ 答错」（不是答错的题不能算进能力）",
  /失败 ≠ 答错：这几题没得到有效结果，但按保守口径算在正确率分母里/.test(src));
check("逐题明细：失败那一格能看出原因（悬停显示错误原文）",
  /it\.error \? `<span class="st-failed" title="\$\{esc\(it\.error\)\}">失败/.test(src));

// ---------------- 汇总 ----------------
console.log(`页面: ${HTML}`);
console.log("=".repeat(74));
let bad = 0;
for (const [name, ok, detail] of results) {
  console.log(`[${ok ? "通过" : "失败"}] ${name}`);
  if (!ok) {
    bad++;
    if (detail) console.log(`    ${detail}`);
  }
}
console.log("=".repeat(74));
if (bad) {
  console.log(`\n${bad}/${results.length} 项不通过。渲染结果：\n`);
  console.log(ovHtml);
  console.log(boardHtml);
  process.exit(1);
}
console.log(`全部 ${results.length} 项通过：部分/完整两条渲染分支都正常。`);
