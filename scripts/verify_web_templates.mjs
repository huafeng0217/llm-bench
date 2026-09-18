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

// i18n 运行时：被抠出来的渲染函数现在会调用 t()/tf()，所以测试里要先把它们装上。
// 这里**直接求值页面里那份真实运行时**（EN 表 + t/tf），而不是打桩 —— 否则测的是假实现。
// localStorage 在 node 里没有：给它一个返回 "zh" 的桩，测试默认跑中文模式；
// 需要英文冒烟时调用 i18n.setLang("en")（t/tf 闭包里的 LANG 会跟着变）。
globalThis.localStorage = { getItem: () => "zh", setItem: () => {} };
const runtimeSrc = script.slice(script.indexOf("const EN = {"), script.indexOf("function applyI18n"));
const i18n = new Function(runtimeSrc + "\nreturn { t, tf, setLang: l => { LANG = l; } };")();
globalThis.t = i18n.t;
globalThis.tf = i18n.tf;

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
    // 与冠军只差 0.39 分：用来验证「差得再小也不重叠」（靠最小间距摊开）
    { model_name: "模型丙", avg_accuracy: 99.0, covered: 1, total: 1, partial: 0, families: [] },
    // covered < total：走到「未跑全 / N 项为部分」那条分支（否则英文冒烟覆盖不到它）
    { model_name: "模型乙", avg_accuracy: 0, covered: 1, total: 2, partial: 1, families: [] },
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
}, {
  // 「分数更高、但没跑全」的情形：冠军口径下它不参选，悬停里必须点名，
  // 否则图上看就像高亮错了（实测代码工程：冠军 93.98%，另一个 98.17% 只跑了 1/2 项）
  id: "safety", name: "安全 / 对齐", color: "#c44b8a", n_benchmarks: 2,
  combined: [
    { model_name: "模型甲", avg_accuracy: 91.5, covered: 2, total: 2, partial: 0, coverage: 2, families: [] },
    { model_name: "模型乙", avg_accuracy: 99.0, covered: 1, total: 2, partial: 0, coverage: 1, families: [] },
  ],
  boards: [], families: [],
}];

const boardHtml = new Function("bds", "expandedCats", "esc",
  pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
)(bds, { code: true, agent: true }, s => String(s ?? ""));

check("排行榜：部分评测带「部分 6/164」标签", boardHtml.includes("部分 6/164"));
check("排行榜：部分评测不排名次", boardHtml.includes('title="只跑了部分题库，不排名次"'));
check("排行榜：完整评测仍有名次徽章", boardHtml.includes('class="rank r1"'));
check("排行榜：综合排行提示部分评测不计入平均", boardHtml.includes("1 项为部分"));
check("排行榜：措辞已改成「完整评测中的最高分」", boardHtml.includes("完整评测中的最高分"));
// 总览行的「分数分布」：一条轨道 + 每个模型一个**彩色**点（颜色 = 模型身份）。
// 为什么每行都要有轨道：只有 >0 的行才有的话，就成了用户最反感的「有的有有的没」。
check("排行榜总览：每个分类一条轨道，点数 = 该分类模型数，冠军点单独标记",
  (boardHtml.match(/class="ov-dist"/g) || []).length === bds.length
  && (boardHtml.match(/<i class="(?:champ)?"\s+style="left:/g) || []).length === 6
  && (boardHtml.match(/<i class="champ"\s+style="left:/g) || []).length === bds.length,
  "分类数 3、模型总数 3+1+2=6、每行一个冠军点");
// 「差得再小也不重叠」：同一行里相邻两点的间距必须 ≥ 9%（≈13px）。
// 实测通用知识 demo 83.33 / deepseek-flash 82.86 只差 0.47 分，旧版完全叠成一个点。
const strips = [...boardHtml.matchAll(/class="ov-dist"[^>]*>([\s\S]*?)<\/div>/g)]
  .map(m => [...m[1].matchAll(/style="left:([\d.]+)%/g)].map(x => +x[1]).sort((a, b) => a - b));
check("排行榜总览：同一行里相邻点不重叠（最小间距 ≥ 9%）",
  strips.length === bds.length
  && strips.every(ps => ps.every((p, i) => i === 0 || p - ps[i - 1] >= 8.99)),
  JSON.stringify(strips));
// 每行按**自己的区间**放大：code 组理想位置是 5% / 94.63% / 95%，
// 后两点太近 → 摊到 86% / 95%（顺序仍严格按分数，且都落在轨道内）
check("排行榜总览：每行按该分类区间放大，并把挨在一起的点摊开",
  boardHtml.includes("left:5.00%") && boardHtml.includes("left:86.00%") && boardHtml.includes("left:95.00%"),
  "期望 code 行三点在 5% / 86% / 95%；见 " + JSON.stringify(strips));
// 回归：冠军**分数最低**时（实测安全 / 对齐：冠军 91.5% < 另一个 99%），位置仍必须按分数单调。
// 第一版先把冠军挪到数组末尾再算位置，数组一开始就是降序，摊开循环把冠军推到了 86%
// —— 图上冠军跑到别人右边，看着就是错的。所以位置要按升序数组算，画的时候再挪冠军。
const safetyStrip = [...boardHtml.matchAll(/class="ov-dist"[^>]*>([\s\S]*?)<\/div>/g)][2][1];
const champLeft = (safetyStrip.match(/<i class="champ"\s+style="left:([\d.]+)%/) || [])[1];
const safetyXs = [...safetyStrip.matchAll(/style="left:([\d.]+)%/g)].map(m => +m[1]).sort((a, b) => a - b);
check("排行榜总览：冠军分数最低时，位置仍按分数单调（回归）",
  champLeft === "5.00" && JSON.stringify(safetyXs) === "[5,95]",
  `冠军在 ${champLeft}%，全部位置 ${JSON.stringify(safetyXs)}`);// 区间放大之后位置不再是绝对值，所以两端必须标出该行的最低/最高分（否则会被误读成 0–100）。
// 断言必须带上区间标签那份样式：光找 ">91.5%<" 会被「综合得分」列的数字命中（第一版就是这个问题，
// 去掉区间标签照样绿 —— 又是「断言某句话出现过，先数它有几处」）。
const rangeLabel = /font-size:9\.5px;font-variant-numeric:tabular-nums">([\d.]+)%</g;
const rangeLabels = [...boardHtml.matchAll(rangeLabel)].map(m => m[1]);
check("排行榜总览：标出该行的最低/最高分（区间放大的前提）",
  rangeLabels.includes("91.5") && rangeLabels.includes("99.0"),
  `区间标签：${JSON.stringify(rangeLabels)}`);
// 颜色必须真的是「模型身份」：同一模型在冠军名前的点、轨道上的点、展开后的两张表里同色；
// 不同模型不同色。只断言"有颜色"是不够的 —— 那样颜色就只是装饰。
const colorOf = name => [...boardHtml.matchAll(
  new RegExp(`class="mdot" style="background:(#[0-9a-f]{6})"></span>${name}`, "g"))].map(m => m[1]);
const cJia = colorOf("模型甲"), cYi = colorOf("模型乙");
const champDots = [...boardHtml.matchAll(/<i class="champ"\s+style="left:[\d.]+%;background:(#[0-9a-f]{6})"/g)]
  .map(m => m[1]);
check("排行榜总览：同一模型到处同色、不同模型不同色（颜色 = 模型身份）",
  cJia.length >= 2 && new Set(cJia).size === 1
  && cYi.length >= 1 && cJia[0] !== cYi[0]
  && champDots.length === bds.length && new Set(champDots).size === 1 && champDots[0] === cJia[0],
  `模型甲 ${cJia}、模型乙 ${cYi}、冠军点 ${champDots}`);
// 颜色既然代表身份，就得能查到是谁：总览顶部给出「色点 + 模型名」的图例。
// 同样要限定在图例容器里 —— 冠军模型那一格也是同样的「色点 + 模型名」，
// 不限定的话去掉图例照样绿（和上一条同一种毛病）。
const legend = (boardHtml.match(/gap:4px 12px">([\s\S]*?)<\/div>/) || [, ""])[1];
check("排行榜总览：给出配色图例（否则只有悬停才知道颜色是谁）",
  ["模型甲", "模型乙", "模型丙"].every(n => legend.includes(n)),
  `图例内容：${JSON.stringify(legend.slice(0, 120))}`);
// 冠军口径必须写出来：冠军是「跑得最全的一批里分数最高」，不一定是分数最高的那个。
// 要求**表头和哑铃悬停都说**：只写一处时，另一处看起来仍然像高亮错了。
// （第一版只断言「出现过」，结果去掉悬停那份照样绿 —— 反向验证才发现是弱断言。）
check("排行榜总览：冠军口径在表头和哑铃悬停里都说明",
  (boardHtml.match(/冠军 = 跑得最全的一批里分数最高/g) || []).length >= 2,
  `"只说了一处：" + (boardHtml.match(/冠军 = 跑得最全的一批里分数最高/g) || []).length + " 处"`);
check("排行榜总览：分数更高但没跑全的模型，在悬停里点名",
  boardHtml.includes("模型乙 99% 更高，但只跑了 1/2 项"));

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
  /<th class="tl"[^>]*>模型<\/th><th class="tl"[^>]*>基准<\/th>/.test(src));

// 两张表的行是在别的函数里拼的（不在 <table> 块内），所以单独精确断言正文单元格 ——
// 全局数量检查太松，少标一格它发现不了（反向验证过）。
check("模型管理：正文的模型 / base_url / Key 单元格都标了 tl",
  /<td class="[^"]*\btl\b[^"]*" style="font-weight:550">\$\{esc\(m\.name\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.base_url\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.api_key\)\}/.test(src));
check("评测任务：正文的模型 / 基准单元格都标了 tl",
  /<td class="[^"]*\btl\b[^"]*" style="font-weight:550">\$\{esc\(e\.model_name\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*" title="\$\{tf\("输出上限/.test(src));

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
  /class="acc"[^>]*title="\$\{accTitle\}"/.test(src)
  && /分母是已完成的题数，失败的那 \{0\} 题也在里面/.test(src)
  // 没有失败时不提失败（「失败的那 0 题也在里面」读起来别扭）
  && /没有失败题，这个数就是实际正确率/.test(src));
// 只给 >0 的行追加一截字，就会出现「有的标有的没标」的不协调（用户报过）——
// 所以失败数独立成一列：每行都有值（0 灰 / N 红），状态列只放状态。
check("任务列表：失败数独立成列（表头 + 空表 colspan 跟着加一）",
  /<th[^>]*>进度<\/th><th[^>]*>正确率<\/th><th style="width:56px"[^>]*>失败<\/th><th[^>]*>状态<\/th>/.test(src)
  && /<tbody id="evals"><tr><td colspan="8" class="empty"[^>]*>/.test(src)
  && /colspan="\$\{batchMode \? 9 : 8\}"/.test(src));
check("任务列表：失败列每行都有值（0 灰 / N 红），不再有的标有的没标",
  /failed \? `<span class="st-failed"[^>]*>\$\{failed\}<\/span>`\s*:\s*`<span class="muted">0<\/span>`/.test(src));
check("任务列表：状态列只放状态（失败数不再挤进来）",
  /class="\$\{stCls\}">\$\{ST_TEXT\[e\.status\] \|\| e\.status\}<\/td>/.test(src));
check("任务列表：正确率只显示主数字，有效题率写在悬停说明里（不占格子、不用点）",
  /只看跑成的 \{0\} 题是 \{1\}%/.test(src)
  && /validAcc = failed && e\.done \? Math\.round\(e\.correct \/ \(e\.done - failed\) \* 1000\) \/ 10 : null/.test(src),
  "未跑成的题不能从主数字里消失，但也不能让主数字独占解释权");
// 用户否掉了「点一下再展开」：信息本来就该在悬停里，多一层点击只多一个要记的状态
// （还要防止被 2.5 秒一次的重渲染刷掉）。所以那套东西不许回来。
check("任务列表：不再有点击展开那套（没有 toggleValidAcc / validAccOpen / 隐藏 span）",
  !/toggleValidAcc/.test(src) && !/validAccOpen/.test(src)
  && !/id="vacc-/.test(src) && !/accExtra/.test(src));
 // ---------------- 中英切换（i18n）----------------
// 机制：中文原文即 key，英文在 EN 表里查；查不到就回落中文。
// 所以「有没有漏译」是可以自动测的 —— 见下面两条：静态骨架的每个 data-i18n 键都必须在 EN 表里，
// 而且 EN 表的值里不许出现中文（漏成中文等于没翻，界面上还看不出来）。
const staticSrc = src.slice(0, src.indexOf("<script>"));
const staticKeys = [...staticSrc.matchAll(/data-i18n(?:-html|-ph|-title)?="([^"]+)"/g)].map(m => m[1]);
// 用花括号配平取整张表：不能找 `};` —— 表里就有值以 `{1};` 结尾，会把表截断
const enStart = script.indexOf("const EN = {");
let enEnd = -1, depth0 = 0;
for (let i = script.indexOf("{", enStart); i < script.length; i++) {
  if (script[i] === "{") depth0++;
  else if (script[i] === "}") { depth0--; if (depth0 === 0) { enEnd = i + 1; break; } }
}
const enSrc = script.slice(enStart, enEnd);
const EN = new Function(enSrc + "\nreturn EN;")();
check("i18n：静态骨架里每个 data-i18n 键都有英文（防漏译）",
  staticKeys.every(k => Object.prototype.hasOwnProperty.call(EN, k)),
  `缺英文：${staticKeys.filter(k => !(k in EN))}`);
check("i18n：英文表里没有中文（漏成中文等于没翻）",
  Object.entries(EN).filter(([, v]) => /[\u4e00-\u9fff]/.test(v)).map(([k]) => k).length === 0,
  `值是中文：${Object.entries(EN).filter(([, v]) => /[\u4e00-\u9fff]/.test(v)).map(([k]) => k)}`);
check("i18n：顶栏有语言开关，且切换会写 localStorage 并重载",
  /id="langsw"/.test(src) && /data-lang="zh"/.test(src) && /data-lang="en"/.test(src)
  && /function setLang\(lang\)[\s\S]{0,200}localStorage\.setItem\("lang"/.test(script)
  && /location\.reload\(\)/.test(script));
check("i18n：每条请求都带 X-Lang（服务端据此返回对应语言的文案）",
  /"X-Lang": LANG/.test(script));
// 静态骨架里**不该有漏标记的中文**：每个中文文本节点都得挂 data-i18n*（否则切英文时会留下中文）。
// 两个例外：① 语言开关自己的「中文」按钮（那是语言自己的名字，永远不翻）；
// ② data-i18n-html 容器**内部**的标签（整段是一起翻的，如 summary-hint 里的 <b>）。
const shellNoHtml = staticSrc
  .replace(/<style[\s\S]*?<\/style>/g, "")
  .replace(/<!--[\s\S]*?-->/g, "")
  .replace(/<(\w+)[^>]*data-i18n-html[^>]*>[\s\S]*?<\/\1>/g, "");
const unmarked = [...shellNoHtml.matchAll(/<([a-zA-Z][^>]*)>([^<>]*[\u4e00-\u9fff][^<>]*)/g)]
  .filter(m => !m[1].includes("data-i18n") && !m[1].includes('data-lang="zh"'))
  .map(m => m[2].trim().slice(0, 30));
check("i18n：静态骨架里没有漏标记的中文（切英文不会留下中文）", unmarked.length === 0,
  `未标记：${unmarked}`);
// 默认语言跟浏览器：localStorage 里选过就用它，否则 navigator.language 以 zh 开头 → 中文
check("i18n：默认语言跟浏览器、可被 localStorage 覆盖",
  /localStorage\.getItem\("lang"\)/.test(script) && /navigator\.language/.test(script)
  && /startsWith\("zh"\) \? "zh" : "en"/.test(script));
// 整个页面脚本必须能解析。上面所有断言都是把**某几个**渲染函数抠出来跑的 ——
// 别处的语法错误（比如 refresh 里拼模板时少个反引号）它们一个都发现不了，
// 只会在浏览器里白屏。这里补一条兜底。
let parseOk = true, parseErr = "";
try { new Function(script); } catch (e) { parseOk = false; parseErr = String(e).slice(0, 300); }
check("页面脚本整体能解析（语法错误不该只被浏览器发现）", parseOk, parseErr);
check("任务列表：失败数带说明（失败 ≠ 答错，别读成能力差）",
  /title="\$\{t\("判分 \/ 执行失败：[^"]*既不算对也不算答错[^"]*/.test(src));
check("逐题明细：顶部把「正确 / 判定不利 / 判分失败」三档分开列（措辞随基准）",
  /evalsCache/.test(src) && /\$\{adverse\} \$\{done - ok - bad\}/.test(src)
  && /判分\/执行失败 \{0\}/.test(src));
check("逐题明细：说明「失败 ≠ 答错」（不是答错的题不能算进能力）",
  /失败 ≠ \{0\}：这几题没得到有效结果，但按保守口径算在正确率分母里/.test(src));
// 这一格必须看**落库的 failed 列**，不能看「有没有错误文本」：答错也会写 error 当诊断
// （代码题的测试 traceback 就是），看 error 会把答错标成失败 —— 实测 #119 任务列表说失败 0，
// 明细里却有 20 行带错误文本。老数据没这一列（NULL），才退回老判据。
check("逐题明细：失败那一格看落库的 failed 列（不是「有没有错误文本」）",
  /function itemFailed\(it\) \{\s*return it\.failed == null \? !!it\.error : !!it\.failed;/.test(src)
  && /itemFailed\(it\) \? `<span class="st-failed" title="\$\{esc\(it\.error \|\| ""\)\}">\$\{t\("失败"\)\}/.test(src));
// 安全类的 ok=0 不是「答错」，措辞由基准元数据给（adverse_label），前端不硬编码基准名
check("逐题明细：安全类把这一档换成本口径的说法（越狱成功 / 过度拒绝）",
  /\(benchMeta\[ev\.benchmark\] \|\| \{\}\)\.adverse_label \|\| t\("答错"\)/.test(src)
  && /\$\{adverse\} \$\{done - ok - bad\}/.test(src));

// ---------------- 英文界面渲染冒烟（文案工作的最终验收）----------------
// 思路：把假数据里的**中文值**换成 ASCII 占位，再在英文模式下渲染一遍，断言输出里没有中文。
// 为什么要把输入也换掉：模型名 / 基准名 / 分类名 / 状态 / 简介都是**服务端**给的，
// 由服务端按语言返回（第四步做完了），不属于"前端漏译"。不换掉的话，断言会一直
// 报服务端数据的假阳性，真正漏译的地方反而看不见。
// 反过来：只要输出里还有中文，就一定是前端自己拼的某段文案没接 i18n —— 这正是要抓的。
const CJK_RE = /[\u4e00-\u9fff]/;
const _asciiCache = new Map();
function asciify(obj) {
  if (typeof obj === "string") {
    return obj.replace(/[\u4e00-\u9fff]+/g, run => {
      if (!_asciiCache.has(run)) _asciiCache.set(run, "T" + (_asciiCache.size + 1));
      return _asciiCache.get(run);
    });
  }
  if (Array.isArray(obj)) return obj.map(asciify);
  if (obj && typeof obj === "object") {
    const out = {};
    for (const k of Object.keys(obj)) out[k] = asciify(obj[k]);
    return out;
  }
  return obj;
}
async function enSmoke(name, render) {
  i18n.setLang("en");
  let html = "";
  // 渲染函数里有 async 的（如 updateEvalHint 返回 Promise），统一 await —— 否则拿到的是 Promise
  try { html = await render(); } finally { i18n.setLang("zh"); }
  const left = [...new Set((html.match(/[\u4e00-\u9fff][^<>{}]{0,18}/g) || []).map(x => x.trim()))].slice(0, 6);
  check(`英文冒烟：${name}渲染后不出现中文`, !CJK_RE.test(html), `残留：${left}`);
}

// 成绩总览
await enSmoke("成绩总览", () => {
  const b = { innerHTML: "" };
  return new Function("ovData", "ovOnlyData", "ovPickModel", "document",
    ovCode + "\ndrawOverview();\nreturn document.getElementById('overview').innerHTML;"
  )(asciify(ovData), true, 2, { getElementById: id => (id === "overview" ? b : null) });
});
// 排行榜（总览 + 展开后的两张表）
await enSmoke("排行榜", () => new Function("bds", "expandedCats", "esc",
  pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
)(asciify(bds), { code: true, agent: true }, s2 => String(s2 ?? "")));
// 家族表头只显示组名括号前的短名。中文的组名用**全角**括号（Agentic（Web Search + Memory）），
// 英文用的是**半角**（Agentic (Web Search + Memory)）—— 只按全角「（」拆就拆不开，
// 英文模式下表头会整串铺开（96px 的列被撑爆），而上面那条冒烟用的假数据是全角括号，
// 测不出这个差别，所以要单独用半角括号的组名渲染一遍。
{
  const renderBoard = bd => new Function("bds", "expandedCats", "esc",
    pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
  )(bd, { agent: true }, s2 => String(s2 ?? ""));
  const withParen = (p1, p2) => {
    const bd = JSON.parse(JSON.stringify(asciify(bds)));
    for (const cat of bd)
      for (const f of cat.families || [])
        for (const g of f.groups)
          if (g.name.includes("（")) {
            g.name = g.name.replace("（", p1).replace("）", p2);
            // 表头只渲染「有子集的组」，而 Agentic 组在假数据里没提供子集
            // —— 不给它子集的话这一列根本不画，断言就成了空跑。
            if (!g.subsets.length) g.subsets = ["agentic_placeholder"];
          }
    return bd;
  };
  const hdrFull = renderBoard(withParen("（", "）"));
  const hdrHalf = renderBoard(withParen(" (", ")"));
  check("家族表头：组名带括号时只显示括号前的短名（中英两种括号都要能拆）",
    hdrFull.includes(">Agentic</th>") && hdrHalf.includes(">Agentic</th>"),
    `全角：${hdrFull.includes(">Agentic</th>")} 半角：${hdrHalf.includes(">Agentic</th>")}`);
  check("家族表头：半角括号的长组名不会整串出现在表头里",
    !hdrHalf.includes(">Agentic (Web Search + Memory)</th>")
    && !hdrHalf.includes(">Agentic </th>"),
    "半角括号拆不掉（或拆完没 trim），96px 的表头会被撑爆");
}
// 基准卡片 + 家族卡片
await enSmoke("基准卡片", () => mkCard(asciify(plain)));
await enSmoke("家族卡片", () => new Function("fid", "bms", "selBenchmark", "familySel", "downloadStates",
  "esc", "pct", "statusTag", grab("statusTag") + "\n" + script.slice(g0, g1) + "\nreturn familyCard(fid, bms);"
)("BFCL v4", asciify(bms), "BFCL_v4_live_parallel", {},
  asciify({ BFCL_v4_simple_java: { status: "running", message: "下载中" } }),
  s2 => String(s2 ?? ""), w => Math.round(w * 100) + "%", s2 => `<span class="tag">${s2}</span>`));
// 评测提示
await enSmoke("评测提示", () => {
  const hintEls2 = { "e-model": { options: [{ text: asciify("模型甲") }], selectedIndex: 0 },
                     "start-btn": { style: {} }, "e-hint": { innerHTML: "" } };
  return new Function("benchMeta", "selBenchmark", "esc", "pct", "document",
    "sbxState", "probeSandbox", "loadBenchmarks",
    hintSrc + "\nreturn updateEvalHint().then(() => document.getElementById('e-hint').innerHTML);"
  )(asciify({ "BFCL_v4_live_parallel": bms[2] }), "BFCL_v4_live_parallel",
    s2 => String(s2 ?? ""), w => Math.round(w * 100) + "%",
    { getElementById: id => hintEls2[id] }, null,
    async () => ({ available: true, message: "" }), () => {});
});
// 模型管理表
await enSmoke("模型管理表", async () => {
  const els2 = {};
  await new Function("api", "document", "esc", "updateEvalHint", modelsSrc + "\nreturn loadModels();")(
    async () => asciify(modelList),
    { getElementById: id => els2[id] || (els2[id] = { innerHTML: "", value: "", style: {} }) },
    s2 => String(s2 ?? ""), () => {});
  return els2["models"].innerHTML;
});

// ---------------- i18n 的两条硬不变量 ----------------
// ① 代码里每个 t()/tf() 键都必须在英文表里 —— 少一条，英文模式下那一处就露出中文。
//    这就是「漏译会被自动抓出来」的机制（key 用中文原文，所以能直接比对）。
// 注意：要用 `new Function` 把捕获到的**源码原文**求值一遍再比 ——
// EN 表那边是求值后的字符串，带 \n 的键（如 "\n\n确定要改吗？"）直接比原文会假报缺失。
const usedKeys = [...script.matchAll(/\b(?:t|tf)\(\s*"((?:[^"\\]|\\.)*)"/g)]
  .map(m => new Function(`return "${m[1]}";`)());
const missingEn = [...new Set(usedKeys)].filter(k => !(k in EN));
check("i18n：代码里每个 t()/tf() 键都有英文（漏译会让英文界面露出中文）",
  missingEn.length === 0, `缺 ${missingEn.length} 条：${missingEn.slice(0, 8).join(" | ")}`);
// ② 渲染结果里不该出现没展开的 ${…}：把 ${t(…)} 插进**单引号字符串**里就会这样 ——
//    JS 不做插值，界面会原样显示 `${t("…")}`，而且不报任何语法错（实测踩过）。
const renderedAll = [ovHtml, boardHtml, cardHtml, familyHtml, hintHtml, modelHtml].join("\n");
check("i18n：渲染结果里没有未展开的 ${…}（单引号字符串不插值）",
  !/\$\{/.test(renderedAll),
  (renderedAll.match(/\$\{[^}]{0,40}/g) || []).slice(0, 5).join(" | "));
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
