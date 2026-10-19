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
  // 别丢掉 `async`：切片是从 `function xxx(` 起的，若原函数是 async，
  // 抓出来的函数体里带 await 就会变成语法错误（实测 showItems 就是这样炸的）。
  const prefix = script.slice(Math.max(0, i - 6), i) === "async " ? "async " : "";
  return prefix + script.slice(i, j + 3);
}

const results = [];
const check = (name, ok, detail = "") => results.push([name, !!ok, detail]);
const escLine = script.split("\n").find(l => l.startsWith("const esc = "));
if (!escLine) throw new Error("找不到 esc 的定义");
// 分数格式化（两位小数）：矩阵/排行榜的分数列都走它
const scoreLine = script.split("\n").find(l => l.startsWith("const score = "));
if (!scoreLine) throw new Error("找不到 score（分数统一两位小数）的定义");

// ---------------- 成绩总览 ----------------
// 总览这一整块：矩阵 + 两张图（能力雷达 / 准确率×延迟）。三张视图共用同一份
// /api/leaderboard 数据，所以把图表那一组函数一起注入 —— 测的就是页面上真正跑的代码。
const ovCode = [
  escLine, scoreLine,
  grab("lbColors"), grab("svgNum"), grab("median"), grab("niceCeil"),
  grab("radarModel"), grab("radarSvg"), grab("radarTable"), grab("radarView"),
  grab("scatterPoints"), grab("scatterSvg"), grab("scatterView"),
  grab("drawOverview"), grab("renderOvProfile"),
  grab("pinIdx"), grab("focusOpacity"), grab("pinRowHtml"), grab("pinRowsFor"),
  grab("pinPanelHtml"), grab("togglePin"), grab("hlPin"), grab("hlPinRow"),
  grab("nearestKey"), grab("scatterMeans"), grab("viewControls"), grab("viewHint"),
  "const PIN_MAX = 3;", 'const MEAN_CAT = "__mean__";',
].join("\n");

// 按视图渲染总览。ovRadarOff = 雷达图例里"被点掉"的模型集合（只影响画，不改归一区间）。
function renderOverview(ov, view, lb, off, unit, pins, mode) {
  const b = { innerHTML: "" };
  const doc = { getElementById: id => (id === "overview" ? b : null) };
  return new Function("ovData", "ovOnlyData", "ovPickModel", "ovView", "lbData", "ovRadarOff",
    "ovScatterUnit", "ovScatterOff", "ovPins", "ovScatterMode", "document",
    ovCode + "\ndrawOverview();\nreturn document.getElementById('overview').innerHTML;"
  )(ov, true, 2, view, lb, off || new Set(), unit || "cat", off || new Set(),
    (pins || []).slice(), mode || "detail", doc);
}

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

const ovHtml = renderOverview(ovData, "matrix", null, null);

check("总览：完整成绩拿到最高分高亮", /class="ov-cell[^"]*\bov-top\b/.test(ovHtml));
check("总览：部分评测格不参与最高分高亮（100% 也不高亮）",
  // 分数统一两位小数后是 "100.00%"；两种写法都算，避免断言因为格式变化而恒真
  !/class="ov-cell[^"]*\bov-top\b[^"]*"[^>]*>\s*100(\.00)?%<span class="ov-part"/.test(ovHtml),
  "100% 的部分评测被当成了最高分");
check("总览：部分评测格带 ov-partial 标记", ovHtml.includes("ov-cell ov-partial"));
// 矩阵：两种颜色就够（最高分实心蓝、其余中性）。试过"底色深浅 = 分数"的热力网格，用户否掉了：
// 色深编码的就是格子里已经写着的数字（纯冗余）；单色蓝阶在 50~80% 这段拉不开对比；
// 整片浅蓝方块和"白卡 + 一个强调色"的整体语言打架。这条断言同时**防止热力网格被重新引入**。
check("总览：矩阵只有两种底色（最高分实心蓝、其余中性），不再用色深编码分数",
  /\.ov-table td\.ov-top \{ background:var\(--accent\); color:#fff; \}/.test(src)
  && /\.ov-table td\.ov-plain \{ background:var\(--bg2\)/.test(src)
  && !/ov-heat/.test(src)
  && !/Math\.pow\(Math\.max\(0, Math\.min\(1, s\.accuracy/.test(src)
  && /class="ov-cell \$\{top \? "ov-top" : "ov-plain"\}"/.test(src),
  "热力网格是被否掉的方案，别再引回来");
check("总览：部分评测格用斜纹（和真实成绩一眼分开，仍不参与最高分评选）",
  /\.ov-table td\.ov-partial \{[\s\S]{0,240}?repeating-linear-gradient/.test(src)
  && /class="ov-cell ov-partial"/.test(ovHtml));
// 行高必须齐：覆盖列的家族徽章以前用 <br> 换行，那一行比邻行高，同列的综合得分数字就低半格
check("排行榜「覆盖」列徽章不换行（行高等齐，数字才对得齐）",
  !/const famCover =[\s\S]{0,200}?<br>/.test(src)
  && /const famCover[\s\S]{0,240}?margin-left:6px;vertical-align:middle/.test(src));
// ===== 精致化：排行榜大数字 / 悬停手感 / 骨架屏 =====
// 结果卡试过加一条 3px 主题色左边缘，用户反馈"突兀、像边框错位" → 回退，并留断言防止再加。
check("结果卡不再加左侧强调边（层级交给深色 hero 和大数字）",
  !/\.card\.key/.test(src) && !/class="card key"/.test(src));
check("排行榜百分比放大成「成绩单」字号（只影响 #board，矩阵保持紧凑）",
  /#board \.score-cell \.acc \{ font-size:21px/.test(src)
  && /#board \.score-cell \.bar \{ height:9px/.test(src)
  && !/\.ov-table td \{[^}]*font-size:21px/.test(src));
check("基准卡片悬停轻微抬起（36 张卡「能点」有手感）",
  /\.bm:hover \{ transform:translateY\(-2px\)/.test(src));
check("首屏数字有骨架占位，数据到了再撤（不会先显示 0 再跳）",
  src.includes('class="hero-title skel"') && src.includes('<b class="big skel" id="hero-wins">')
  && /title\.classList\.remove\("skel"\)/.test(src)
  && /winsEl\.classList\.remove\("skel"\)/.test(src));
check("尊重系统的「减少动态效果」设置",
  /@media \(prefers-reduced-motion: reduce\)/.test(src)
  && /\.skel \{ animation:none/.test(src));
check("总览：部分评测格显示「部分 6/164」", ovHtml.includes("部分 6/164"));
check("总览：图例说明不参与最高分评选", ovHtml.includes("不参与最高分评选"));
check("总览：点模型名仍能展开能力画像", ovHtml.includes("模型乙 的能力画像"));
check("总览：默认是矩阵视图，且三个视图可以切", ovHtml.includes("矩阵") && ovHtml.includes("能力雷达") && ovHtml.includes("准确率 × 延迟"));

// ---------------- 能力雷达图 ----------------
// 假数据专门覆盖三种边界：
//   ① 每根轴各自归一（分类甲的区间 70–90，90 分贴外圈、70 分在圆心）
//   ② 缺轴：模型乙在分类丙只有部分评测（covered=0、avg_accuracy 后端记 0）
//      —— 画成 0 分就是撒谎，必须不画，并在跨越处用虚线
//   ③ 同分轴：分类丁两个模型都是 60 —— 贴边会读成"满分/零分"，只能画中间
const chartLb = [
  { id: "c1", name: "分类甲", color: "#111", families: [],
    boards: [{ benchmark: "b1", benchmark_name: "基准甲", rows: [
      { model_name: "模型甲", color: "#cc0000", benchmark_name: "基准甲", accuracy: 90, avg_latency_ms: 100, total: 100, full_count: 100, partial: false },
      { model_name: "模型乙", color: "#0000cc", benchmark_name: "基准甲", accuracy: 70, avg_latency_ms: 300, total: 100, full_count: 100, partial: false },
      // 与模型甲的均值**完全相同**（85% / 150 ms）：逼出"名字互相压住"那条分支
      { model_name: "模型丙", color: "#00aa55", benchmark_name: "基准甲", accuracy: 85, avg_latency_ms: 150, total: 100, full_count: 100, partial: false },
    ] }],
    combined: [
      { model_name: "模型甲", color: "#cc0000", avg_accuracy: 90, avg_latency_ms: 150, covered: 1, items: 1, total: 1, partial: 0, item_benchmarks: [{ name: "基准甲", family: false }] },
      { model_name: "模型乙", color: "#0000cc", avg_accuracy: 70, avg_latency_ms: 4000, covered: 1, items: 1, total: 1, partial: 0, item_benchmarks: [{ name: "基准甲", family: false }] },
      { model_name: "模型丙", color: "#00aa55", avg_accuracy: 80, avg_latency_ms: 200, covered: 1, items: 1, total: 1, partial: 0, item_benchmarks: [{ name: "基准甲", family: false }] },
    ] },
  { id: "c2", name: "分类乙", color: "#222", boards: [], families: [],
    combined: [
      { model_name: "模型甲", color: "#cc0000", avg_accuracy: 50, avg_latency_ms: 2000, covered: 1, items: 1, total: 1, partial: 0 },
      { model_name: "模型乙", color: "#0000cc", avg_accuracy: 80, avg_latency_ms: 4000, covered: 1, items: 1, total: 1, partial: 0 },
    ] },
  { id: "c3", name: "分类丙", color: "#333", boards: [], families: [],
    combined: [
      { model_name: "模型甲", color: "#cc0000", avg_accuracy: 60, avg_latency_ms: 800, covered: 1, items: 1, total: 1, partial: 0 },
      // 只有部分评测：**covered=1（跑过）但 items=0（没进综合分）**，avg_accuracy 记 0、
      // 延迟是 null —— 拿 covered 判"有没有成绩"就会把没跑完画成 0 分。
      { model_name: "模型乙", color: "#0000cc", avg_accuracy: 0, avg_latency_ms: null, covered: 1, items: 0, total: 1, partial: 1 },
      // 有完整成绩但没延迟（老数据/异常）：也不许画成 0 毫秒
      { model_name: "模型丙", color: "#00aa55", avg_accuracy: 65, avg_latency_ms: null, covered: 1, items: 1, total: 1, partial: 0 },
    ] },
  { id: "c4", name: "分类丁", color: "#444", boards: [],
    families: [{ id: "F", name: "家族", boards: [{ benchmark: "b2", benchmark_name: "基准乙", rows: [
      { model_name: "模型甲", color: "#cc0000", benchmark_name: "基准乙", accuracy: 80, avg_latency_ms: 200, total: 100, full_count: 100, partial: false },
      { model_name: "模型乙", color: "#0000cc", benchmark_name: "基准乙", accuracy: 60, avg_latency_ms: 50, total: 6, full_count: 100, partial: true },
    ] }] }],
    combined: [
      { model_name: "模型甲", color: "#cc0000", avg_accuracy: 60, avg_latency_ms: 1000, covered: 1, items: 1, total: 1, partial: 0, item_benchmarks: [{ name: "基准乙", family: true }] },
      { model_name: "模型乙", color: "#0000cc", avg_accuracy: 60, avg_latency_ms: 4000, covered: 1, items: 1, total: 1, partial: 0 },
    ] },
];

const radarHtml = renderOverview(ovData, "radar", chartLb, null);
check("雷达：画出 SVG 而不是矩阵表", radarHtml.includes("<svg viewBox=") && !radarHtml.includes("ov-table\"><thead><tr><th style=\"width:200px\""));
check("雷达：4 个有数据的分类各成一根轴", ["分类甲", "分类乙", "分类丙", "分类丁"].every(n => radarHtml.includes(n)));
check("雷达：轴下方标出该轴自己的区间（70–90%）", radarHtml.includes("70–90%"));
// 归一：分类甲 90 分是该轴最高 → 贴外圈（4 根轴时正上方 = (310, 74)）；
// 70 分是最低 → 圆心 (310, 192)。位置是"相对"，但相对关系必须对。
check("雷达：该轴最高分贴外圈（90 分 → 正上方外圈）", radarHtml.includes('cx="310" cy="74"'));
// 最低分**不画在正圆心**：每根轴的最低点都在圆心的话，多根轴的最低点会叠成一个点
//（真实数据里 6 根轴的最低点全在圆心，19 个顶点只看得见 14 个）。
check("雷达：最低分在靠近圆心处（70 分 → 内边距位置，不是正圆心）",
  radarHtml.includes('cx="310" cy="182.6"') && !radarHtml.includes('cx="310" cy="192"'));
// 同分轴：R*0.5 = 59 → 左侧 (251, 192)。贴边会读成满分/零分。
check("雷达：同分的轴画在中间（不是贴边）", radarHtml.includes('cx="251" cy="192"') && radarHtml.includes("（同分）"));
// 缺轴：模型乙只有 3 个分类有成绩 —— 有且只有一条跨越缺轴的虚线，且不闭合。
check("雷达：缺轴处画虚线（1 条），有成绩的模型才闭合", (radarHtml.match(/stroke-dasharray="4 4"/g) || []).length === 1
  && (radarHtml.match(/Z"/g) || []).length === 1);
// 同一条坑在雷达上：模型乙在分类丙 covered=1 但 items=0，综合分记 0 ——
// 画出来就是"没跑"装成"0 分"。
check("雷达：只有部分评测的模型不画成 0 分（covered=1 但 items=0，不出现分类丙顶点）",
  !radarHtml.includes("<title>模型乙 · 分类丙") && radarHtml.includes("<title>模型甲 · 分类丙"));
check("雷达：明确写出位置不是绝对分数", radarHtml.includes("不是 0–100 的绝对分数"));
check("雷达：图下给出绝对分数表（图看形状，表是证据）",
  radarHtml.includes("图中的绝对分数") && radarHtml.includes(">90%<"));
check("雷达：缺成绩的格子写「—」而不是 0", radarHtml.includes('class="ov-empty">—</td>'));

// 图例：点掉的模型只从图里摘掉，表里还在
const radarOffHtml = renderOverview(ovData, "radar", chartLb, new Set(["模型乙"]));
check("雷达：图例点掉的模型不再画线", !radarOffHtml.includes("<title>模型乙 · ")
  && radarOffHtml.includes("点一下显示 模型乙"));
check("雷达：摘掉一个模型不改归一区间（轴区间还是 70–90%）", radarOffHtml.includes("70–90%"));

// 分类不足 3 个时给一句话，而不是画一条线
const radarThin = renderOverview(ovData, "radar", chartLb.slice(0, 2), null);
check("雷达：分类不足 3 个时给出说明而不是硬画",
  radarThin.includes("至少要有 3 个分类有完整成绩才能画雷达图"));

// ---------------- 准确率×延迟散点 ----------------
// 默认粒度 = 模型 × 分类（点少、且同一分类内比的是同类题）；
// 另一个粒度 = 模型 × 基准（下钻看是哪个基准拖的）。
// 假数据里分类级延迟：甲[150,2000,800,1000] 乙[500,400,1200] 丙[200]（丙在分类丙没延迟）
const scatterCat = renderOverview(ovData, "scatter", chartLb, null);
check("散点：默认按分类画，一个点 = 一个模型在一个分类上的成绩（共 8 个）",
  (scatterCat.match(/r="4(\.8|\.4)"/g) || []).length === 8);
check("散点：两种粒度可切换（按分类 / 按基准）",
  scatterCat.includes("按分类") && scatterCat.includes("按基准"));
// covered=0（只有部分评测）和 avg_latency_ms=null 的行都**不许画**：
// 画出来就是"没跑"装成"0 分 / 0 毫秒"。
// 模型乙在分类丙只有部分评测：后端给 covered=1（跑过）但 items=0（没进综合分）。
// 判据必须是 items —— 用 covered 判就会把它画成"0 分"（这条断言会红）。
check("散点：只有部分评测的分类不画点（covered=1 但 items=0 的模型乙 · 分类丙）",
  !scatterCat.includes("模型乙 · 分类丙"));
check("散点：有成绩但缺延迟的分类也不画点（不画成 0 毫秒）",
  !scatterCat.includes("模型丙 · 分类丙") && scatterCat.includes("模型甲 · 分类丙"));
check("散点：分类级的点都是完整评测（不会出现空心点）",
  !scatterCat.includes('fill="none"'));
check("散点：悬停写明这是分类综合分 + 等权平均延迟",
  scatterCat.includes("分类综合分") && scatterCat.includes("各基准等权平均每题"));
// 分类级延迟 [150,200,400,500,800,1000,1200,2000] → 中位 650；正确率 [50,60,60,60,70,80,80,90] → 中位 65
check("散点：分类级中位数参考线正确（延迟 1500 ms / 正确率 65%）",
  scatterCat.includes("延迟中位数 1500 ms") && scatterCat.includes("正确率中位数 65%"));
// 均值菱形按分类等权：甲 (90+50+60+60)/4 = 65%、(150+2000+800+1000)/4 = 987.5 ms
// 均值只在"均值模式"里画（用户要的是两种模式二选一）
const catMeanHtml = renderOverview(ovData, "scatter", chartLb, null, "cat", [], "mean");
check("散点·均值模式：◆ 是各分类的等权平均（模型甲 4 个分类 65% / 988 ms）",
  catMeanHtml.includes("模型甲：4 个分类的等权平均 65%（平均 988 ms）"));
check("散点：分类级说明写清楚点是什么、延迟怎么平均、比较时注意什么",
  scatterCat.includes("一个点 = 一个模型在一个分类上的成绩")
  && scatterCat.includes("不是按题量加权")
  && scatterCat.includes("跨模型比较时先看点数"));

// 图例可点：摘掉一个模型，点和菱形消失，但参考线不动（参考线是数据的属性）
const scatterOff = renderOverview(ovData, "scatter", chartLb, new Set(["模型甲"]));
check("散点：图例点掉的模型不再画点（8 - 4 = 4 个）",
  (scatterOff.match(/r="4(\.8|\.4)"/g) || []).length === 4
  && scatterOff.includes("点一下显示 模型甲"));
check("散点：摘掉模型不会让中位数参考线移动（还是 1500 ms）",
  scatterOff.includes("延迟中位数 1500 ms"));
// 模型名必须躲开中位数标签：实测 "deepseek-v4-pro" 正好压在 "正确率中位数 72.75%" 上
//（当时只登记了模型名，自己人躲自己人）。夹具里模型乙的菱形贴右边缘、均值正确率又贴近
//中位数，正是会撞的那个位置。
function medianLabels(html) {
  const out = [];
  const re = /<text x="(-?[\d.]+)" y="(-?[\d.]+)"(?: text-anchor="(\w+)")? font-size="10\.5" fill="var\(--muted\)">([^<]+)<\/text>/g;
  let m;
  while ((m = re.exec(html))) {
    const w = [...m[4]].reduce((s, ch) => s + (/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/.test(ch) ? 10.5 : 0.56 * 10.5), 0);
    const an = m[3] || "start";
    const x0 = an === "middle" ? +m[1] - w / 2 : an === "end" ? +m[1] - w : +m[1];
    out.push({ txt: m[4], x0, x1: x0 + w, y0: +m[2] - 9, y1: +m[2] + 3 });
  }
  return out;
}
const mls = medianLabels(catMeanHtml);
check("散点：分类级画出两条中位数标签（延迟 / 正确率）", mls.length === 2, mls.map(l => l.txt).join(" | "));
// 名字只挂在均值模式（明细模式没有名字）→ 这条要在均值模式下查
const catLabels = nameLabels(catMeanHtml);
const cross = [];
for (const a of catLabels) for (const b of mls) {
  if (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1) cross.push(`${a.name}×${b.txt}`);
}
check("散点：模型名不压在中位数标签上", cross.length === 0, cross.join(" | "));

// 基准级（下钻）：一个点 = 一次（模型 × 基准），家族子集也算一个点
const scatterHtml = renderOverview(ovData, "scatter", chartLb, null, "bench");
check("散点·基准级：一个点 = 一次（模型 × 基准），家族子集也算（共 5 个）",
  (scatterHtml.match(/r="4(\.8|"|\.4)/g) || []).length === 5);
check("散点·基准级：部分评测是空心点（画出来但不参与统计）",
  scatterHtml.includes('fill="none"') && scatterHtml.includes("空心点，不参与均值与中位数"));
// 明细模式**不画均值菱形**（用户要的是两种模式二选一，不是叠加）
check("散点·明细模式：不画均值菱形（避免和明细点叠在一起）",
  (scatterHtml.match(/<path d="M /g) || []).length === 0);
// 均值模式：一个模型一个菱形 + 名字标签
const meanHtml = renderOverview(ovData, "scatter", chartLb, null, "bench", [], "mean");
check("散点·均值模式：一个模型一个菱形（3 个），明细点不再画",
  (meanHtml.match(/<path d="M /g) || []).length === 3
  && (meanHtml.match(/r="4(\.8|"|\.4)/g) || []).length === 0);
// 均值相同的两个模型（甲 / 丙 都是 85% / 150 ms）：两个名字必须错开，不能压在一起。
// 这里按页面同一套估算宽度算包围盒 —— 光看 text-anchor 会被 y 轴刻度标签骗过去
// （它们也是 text-anchor="end" font-size="11"），反向验证时正是这么漏掉的。
function nameLabels(html) {
  const out = [];
  const re = /<text x="(-?[\d.]+)" y="(-?[\d.]+)" text-anchor="(\w+)" font-size="11" fill="var\(--text\)"[\s\S]*?>([^<]+)<\/text>/g;
  let m;
  while ((m = re.exec(html))) {
    const w = [...m[4]].reduce((s, ch) => s + (/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/.test(ch) ? 11 : 0.56 * 11), 0);
    const x0 = m[3] === "middle" ? +m[1] - w / 2 : m[3] === "end" ? +m[1] - w : +m[1];
    out.push({ name: m[4], x0, x1: x0 + w, y0: +m[2] - 11, y1: +m[2] + 3 });
  }
  return out;
}
const labels = nameLabels(meanHtml);   // 名字只挂在均值模式（明细模式 43 个点不挂名字）
check("散点·均值模式：每个模型一个名字标签", labels.length === 3, labels.map(l => l.name).join(","));
const overlap = [];
for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) {
  const a = labels[i], b = labels[j];
  if (a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1) overlap.push(`${a.name}×${b.name}`);
}
check("散点：均值相同的两个模型名字不重叠（自动换位置）", overlap.length === 0, overlap.join(" | "));
// 文字不许跑出画布（宽度用与页面同一套估算）：手写 SVG 改布局时最容易把标签挤出去，
// 页面上只会看到"字被裁掉"，不报错。
function outsideBox(html, W, H) {
  const out = [];
  const re = /<text([^>]*)>([\s\S]*?)<\/text>/g;
  let m;
  while ((m = re.exec(html))) {
    const fs = +(/font-size="([\d.]+)"/.exec(m[1])?.[1] || 12);
    const anchor = /text-anchor="(\w+)"/.exec(m[1])?.[1] || "start";
    const x = +(/ x="(-?[\d.]+)"/.exec(m[1])?.[1] || 0);
    const y = +(/ y="(-?[\d.]+)"/.exec(m[1])?.[1] || 0);
    // <title> 是悬停提示，不是可见文字：不剔掉会把提示的全文当宽度量，直接误报出画布
    const txt = m[2].replace(/<title>[\s\S]*?<\/title>/g, "").replace(/<[^>]+>/g, "");
    const w = [...txt].reduce((s, ch) => s + (/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/.test(ch) ? fs : 0.56 * fs), 0);
    if (/transform="rotate\(-90/.test(m[1])) {   // 旋转的 y 轴标题按旋转后的方向算包围盒
      if (x - fs * 0.82 < -2 || x + fs * 0.28 > W + 2 || y - w / 2 < -2 || y + w / 2 > H + 2) out.push(txt);
      continue;
    }
    const x0 = anchor === "middle" ? x - w / 2 : anchor === "end" ? x - w : x;
    if (x0 < -2 || x0 + w > W + 2 || y - fs * 0.82 < -2 || y + fs * 0.28 > H + 2) out.push(txt);
  }
  return out;
}
const outR = outsideBox(radarHtml, 620, 380), outS = outsideBox(scatterHtml, 680, 400);
const outC = outsideBox(scatterCat, 680, 400);
check("图表：所有文字都在画布内（改布局别把标签挤出去）",
  outR.length === 0 && outS.length === 0 && outC.length === 0,
  [...outR, ...outS, ...outC].slice(0, 4).join(" | "));
// 基准级均值只算完整评测：模型甲 = (90+80)/2 = 85%，(100+200)/2 = 150 ms
check("散点·均值模式：均值只算完整评测（模型甲 85% / 150 ms）",
  meanHtml.includes("模型甲：2 个完整评测的均值 85%（平均 150 ms）"));
// 模型乙的均值必须只用那次 70% / 300 ms：把部分评测算进去会变成 65% / 175 ms
check("散点·均值模式：部分评测没有混进均值（模型乙 70% / 300 ms，不是 65%）",
  meanHtml.includes("模型乙：1 个完整评测的均值 70%（平均 300 ms）")
  && !/完整评测的均值 65%/.test(meanHtml));
// 完整点的延迟 [100,300,200,150] → 中位 175；正确率 [90,70,80,85] → 中位 82.5
// 明细模式的参考线按明细点算：延迟 [100,300,200,150] → 175；正确率 [90,70,80,85] → 82.5
check("散点·明细模式：中位数参考线按明细点算（175 ms / 82.5%）",
  scatterHtml.includes("延迟中位数 175 ms") && scatterHtml.includes("正确率中位数 82.5%"));
check("散点·基准级：x 轴上取整到好读的刻度（最大 300 ms → 0/75/150/225/300）",
  scatterHtml.includes(">300</text>") && scatterHtml.includes(">225</text>"));
check("散点·基准级：写清楚一个点是什么、图里不做聚合",
  scatterHtml.includes("不做任何聚合") && scatterHtml.includes("越靠左上 = 又快又准"));
// ---------------- 点一下钉住（数字钉在图下面板，不做浮层）----------------
// 为什么不做浮层卡片：浮层要猜尺寸（SVG 坐标 ↔ CSS 像素），多个还会互盖。
// 改成"点上的序号徽章 + 图下一张面板"，3 个也不会盖，手机上也能用。
const pinA = { view: "scatter", unit: "cat", model: "模型甲", cat: "分类甲" };
const pinned = renderOverview(ovData, "scatter", chartLb, null, "cat", [pinA]);
// 三次"加图形"的尝试（淡色光晕 / 描边环 / 序号徽章）都被用户否掉：和标记自己的白描边
// 叠成"双重轮廓"，点小的时候尤其笨。最终改成 focus+context：**选中的保持原样、其余淡出**，
// 一个额外图形都不加。断言就钉这两条：没有环/徽章；未选中的确实被淡出。
check("钉住：不加环/徽章，改成未选中的淡出（选中保持原样）",
  !pinned.includes("pin-ring") && !pinned.includes('font-weight="700" fill="#fff"')
  && (pinned.match(/opacity="0\.28"/g) || []).length === 7
  && (pinned.match(/opacity="1"/g) || []).length === 1);
// 双向高亮要**逐行**对得上：只查第 0 行的话，"每行都写死成 pin-0"这种改坏法会被放过
const pinned2 = renderOverview(ovData, "scatter", chartLb, null, "cat",
  [{ view: "scatter", unit: "cat", model: "模型甲", cat: "分类甲" },
   { view: "scatter", unit: "cat", model: "模型乙", cat: "分类甲" }]);
check("钉住：面板行与图上的点双向高亮（每行指向自己的钉子）",
  pinned2.includes('id="row-pin-0"') && pinned2.includes('id="row-pin-1"')
  && pinned2.includes("onmouseover=\"hlPin('pin-0',1)\"")
  && pinned2.includes("onmouseover=\"hlPin('pin-1',1)\"")
  && pinned2.includes("onmouseover=\"hlPinRow(0,1)\"")
  && pinned2.includes("onmouseover=\"hlPinRow(1,1)\""));
check("钉住：图下出现「已选点」面板，且行里有这个点",
  pinned.includes("已选点") && pinned.includes("模型甲 · 分类甲"));
// 面板里必须能看出"这个点是怎么算出来的"：覆盖项数 + 由哪几个基准等权平均
check("钉住：面板列出覆盖项数与构成（含家族标记）",
  pinned.includes("覆盖") && pinned.includes("1/1 项")
  && pinned.includes("由这些等权平均：基准甲"));
const pinFam = renderOverview(ovData, "scatter", chartLb, null, "cat", [
  { view: "scatter", unit: "cat", model: "模型甲", cat: "分类丁" }]);
check("钉住：家族算一项时名单里带标记",
  pinFam.includes("家族算一项") && pinFam.includes("基准乙"));
check("钉住：每行有取消按钮（unpinPoint）", pinned.includes("onclick=\"unpinPoint(this)\""));
// 点击统一走根节点的"最近点"：命中圈重叠也不会有点点不到（用户实测：雷达右上三个点，
// 中间那个点不到 —— 因为命中圈互相覆盖，最上面那个永远赢）
check("钉住：两张图都由根节点按最近点判定（data-pts + pinNearest）",
  scatterHtml.includes("onclick=\"pinNearest(event, this)\"") && scatterHtml.includes("data-pts=")
  && radarHtml.includes("onclick=\"pinNearest(event, this)\"") && radarHtml.includes("data-pts="));
check("钉住：命中圈仍带 data-p（悬停标题 / 双向高亮要它）",
  scatterHtml.includes('data-p="') && radarHtml.includes('data-p="') && !scatterHtml.includes("pinPoint(this)"));
// 可点列表必须**覆盖所有画的标记**：漏一个就是"这个点点不到"（用户实测报过）
const clickableOf = h => {
  const m = /data-pts="([^"]*)"/.exec(h);
  if (!m) return null;
  try { return JSON.parse(m[1].replace(/&quot;/g, '"')); } catch (e) { return null; }
};
const ck = clickableOf(scatterCat), ckBench = clickableOf(scatterHtml), ckRadar = clickableOf(radarHtml);
check("钉住：可点列表覆盖所有标记（散点分类级 8 / 基准级 5 / 雷达 9）",
  ck && ck.length === 8 && ckBench && ckBench.length === 5 && ckRadar && ckRadar.length === 9,
  `实际 ${ck && ck.length} / ${ckBench && ckBench.length} / ${ckRadar && ckRadar.length}`);
check("钉住：均值模式的可点列表里是均值键（__mean__）",
  (clickableOf(meanHtml) || []).every(p => p.k && p.k.cat === "__mean__")
  && (clickableOf(meanHtml) || []).length === 3);
// 分层：装饰（网格/文字/点/菱形/光晕/名字）整块包进 <g pointer-events="none">，
// 命中圈统一画在它**之后**。原来光晕画在命中圈上面 → 可见元素挡住点击，
// 于是"再点一次取消不了"、"被别的光晕盖住的点点不动"（用户实测报的两个 bug）。
const svgOf = h => (h.match(/<svg[\s\S]*<\/svg>/) || [""])[0];
function layeredOk(html) {
  // 装饰层是最后一个 </g>（标记自己也可能包 <g>），命中圈必须在它之后
  const decoEnd = html.lastIndexOf("</g>");
  const firstHit = html.indexOf("data-p=\"");
  if (decoEnd < 0 || firstHit < 0 || firstHit < decoEnd) return false;
  // 装饰层之后不许再出现任何可见元素（文字 / 描边环 / 点 / 线）
  const tail = html.slice(decoEnd);
  return !/<text|<line|<path|<polygon|class="pin-ring"/.test(tail);
}
check("钉住：装饰层整块在命中圈之前（可见元素不可能挡住悬停/点击）",
  layeredOk(svgOf(scatterHtml)) && layeredOk(svgOf(pinned)) && layeredOk(svgOf(radarHtml)),
  "装饰与命中圈层序不对");
// 最近点法本身：三个点挤在 5px 内，点中间那个必须选中中间那个（不能总是最上面那个赢）
// nearestKey 在 ovCode 的作用域里，要像 togglePin 那样单独取出来
const nk = new Function(ovCode + "\nreturn nearestKey;")(undefined);
const nearly = [
  { k: { model: "A" }, x: 100, y: 100 },
  { k: { model: "B" }, x: 105, y: 100 },
  { k: { model: "C" }, x: 110, y: 100 },
];
check("钉子：点击按最近点判定（挤在一起也点点得到）",
  nk(nearly, 104, 101, 14).model === "B"
  && nk(nearly, 110, 100, 14).model === "C"
  && nk(nearly, 100, 100, 14).model === "A");
check("钉子：离所有标记都太远就不钉（不会乱钉一个）",
  nk(nearly, 400, 300, 14) === null && nk(nearly, 130, 100, 14) === null
  && nk(nearly, 118, 100, 14).model === "C");
// 用户问"为什么菱形不能点"：均值模式的菱形也进可点列表（键用 __mean__ 哨兵）
check("钉住：均值模式的菱形也可点（可点列表里有 __mean__）",
  (meanHtml.match(/__mean__/g) || []).length >= 3
  && (meanHtml.match(/r="13" fill="transparent"/g) || []).length === 3);
const meanPin = renderOverview(ovData, "scatter", chartLb, null, "cat",
  [{ view: "scatter", unit: "cat", model: "模型甲", cat: "__mean__" }], "mean");
// 口径说明只在面板头部出现一次（原来每行都重复，用户反馈啰嗦）
check("钉住·均值：口径在面板头部说一次，行里只列构成",
  meanPin.includes("均值 = 该模型各分类的等权平均")
  && meanPin.includes("均值正确率") && meanPin.includes("均值延迟")
  && meanPin.includes("由这些分类等权平均：")
  && (meanPin.match(/它不是任何一种「成绩」/g) || []).length === 1);
// 两个模式互斥：明细模式只有点、均值模式只有菱形（用户要"均值显示后其它点不显示"）
const catDetail = renderOverview(ovData, "scatter", chartLb, null, "cat");
const catMean = renderOverview(ovData, "scatter", chartLb, null, "cat", [], "mean");
check("散点：明细 / 均值两模式互斥（明细只有点，均值只有菱形）",
  (catDetail.match(/<path d="M /g) || []).length === 0
  && (catMean.match(/<path d="M /g) || []).length === 3
  && (catMean.match(/r="4(\.8|"|\.4)/g) || []).length === 0);
// 图级选项要在**图表自己的工具条**里（不在卡片头部、也不在图底部）：
// 卡片头部只留"看哪个视图"，工具条紧贴它所控制的图 —— 分层靠分组、归属靠邻近。
// indexOf 找不到返回 -1 也会"小于"，所以先要求两个下标都存在（这条被反向验证抓过一次）。
const bar = catDetail.slice(catDetail.indexOf('class="chart-bar"'), catDetail.indexOf("chart-wrap"));
check("散点：粒度/明细-均值两个开关在图表的工具条里（紧贴图上、不在卡片头部）",
  catDetail.includes('class="chart-bar"')
  && bar.includes("setScatterUnit('cat')") && bar.includes("setScatterMode('mean')")
  && catDetail.indexOf("setOvView('matrix')") < catDetail.indexOf('class="chart-bar"')
  && catDetail.indexOf('class="chart-bar"') < catDetail.indexOf("chart-wrap")
  && catMean.includes("setScatterMode('detail')"));
check("矩阵：只看有数据的也在工具条里（同一个位置，换个视图就换内容）",
  ovHtml.includes('class="chart-bar"') && ovHtml.indexOf("toggleOvOnlyData()") > ovHtml.indexOf('class="chart-bar"'));
// 工具条里只放开关：别再多写"这张图"这类标签（开关本身就说清了自己管什么）
check("散点：工具条里只有开关（没有多余的标签文字）",
  bar.includes('class="seg"') && !bar.includes('class="lbl"')
  && !/chart-bar">\s*<span[^>]*>\s*[^<]*[\u4e00-\u9fffA-Za-z]/.test(bar));
check("散点：开关措辞是「按分类 / 按基准」和「明细 / 均值」（简写）",
  catDetail.includes(">明细</button>") && catDetail.includes(">均值</button>")
  && !catDetail.includes("只看均值"));
// 图例死局（用户实测）：均值模式的图例原来取自"已过滤的均值"，摘掉一个模型 →
// 菱形和图例按钮一起消失 → 再也点不回来。图例必须永远列所有出现过的模型。
const offOne = renderOverview(ovData, "scatter", chartLb, new Set(["模型甲"]), "cat", [], "mean");
check("图例：摘掉的模型仍留在图例里（能点回来）",
  offOne.includes("点一下显示 模型甲") && offOne.includes("目前显示 2/3".replace("目前", "当前")));
const offAll = renderOverview(ovData, "scatter", chartLb, new Set(["模型甲", "模型乙", "模型丙"]), "cat", [], "mean");
check("图例：全摘光时每个模型都还能找回，并给一句提示",
  ["模型甲", "模型乙", "模型丙"].every(n => offAll.includes(`点一下显示 ${n}`))
  && offAll.includes("所有模型都被图例摘掉了"));
check("图例：同一件事在明细模式下也成立",
  renderOverview(ovData, "scatter", chartLb, new Set(["模型甲"]), "cat").includes("点一下显示 模型甲"));
// 雷达顶点钉住：面板给的是分类分 + 这根轴的区间（归一图的两个关键信息）
const radarPin = renderOverview(ovData, "radar", chartLb, null, null, [
  { view: "radar", model: "模型甲", cat: "分类甲" }]);
check("钉住·雷达：面板给出分类分与这根轴的区间（口径在头部说一次）",
  radarPin.includes("模型甲 · 分类甲") && radarPin.includes("分类分")
  && radarPin.includes("70–90%") && radarPin.includes("位置按各轴自己的区间归一"));
// 徽章位置也算一遍：钉在**外圈**上的顶点，徽章如果朝外放，正好压住"轴下方的区间"那行字
//（真实数据里 ① 压住了 6.31–82.86%，截图才看出来）。
function textBoxes(html) {
  const out = [];
  const re = /<text([^>]*)>([\s\S]*?)<\/text>/g;
  let m;
  while ((m = re.exec(html))) {
    const fs = +(/font-size="([\d.]+)"/.exec(m[1])?.[1] || 12);
    const anchor = /text-anchor="(\w+)"/.exec(m[1])?.[1] || "start";
    const x = +(/ x="(-?[\d.]+)"/.exec(m[1])?.[1] || 0);
    const y = +(/ y="(-?[\d.]+)"/.exec(m[1])?.[1] || 0);
    const txt = m[2].replace(/<title>[\s\S]*?<\/title>/g, "").replace(/<[^>]+>/g, "");
    if (/transform="rotate\(-90/.test(m[1])) continue;   // 旋转的 y 轴标题不参与
    const w = [...txt].reduce((s, ch) => s + (/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/.test(ch) ? fs : 0.56 * fs), 0);
    const x0 = anchor === "middle" ? x - w / 2 : anchor === "end" ? x - w : x;
    out.push({ txt, fs, x0, x1: x0 + w, y0: y - fs * 0.82, y1: y + fs * 0.28 });
  }
  return out;
}
// 淡出不该动文字位置（形状没变，只是透明度）——确认钉住后文字仍在画布内且不重叠
check("钉住：淡出只改透明度，不动文字（钉子后名字仍不重叠）",
  nameLabels(pinned).length === 0          // 明细模式没有名字
  && (radarPin.match(/opacity="0\.28"/g) || []).length > 0);
// 键里的模型名带引号也不能把属性写坏（JSON 进 data-p 前会 esc）
const pinQuote = renderOverview(ovData, "scatter",
  [{ ...chartLb[0], combined: chartLb[0].combined.map(m => ({ ...m, model_name: m.model_name + '\"x' })) },
   ...chartLb.slice(1)], null, "cat", []);
check("钉住：属性里的引号被转义（不破坏 HTML）", !/data-p="[^"]*\"x[^"]*"/.test(pinQuote));

// 钉/取消的纯逻辑（点一下的行为不该只能手点验证）
const k1 = { view: "scatter", unit: "cat", model: "甲", cat: "A" };
const k2 = { view: "scatter", unit: "cat", model: "乙", cat: "B" };
const k3 = { view: "scatter", unit: "cat", model: "丙", cat: "C" };
const k4 = { view: "scatter", unit: "cat", model: "丁", cat: "D" };
// 注意：ovCode 里已经**声明**了 togglePin/PIN_MAX，所以不能再用同名形参
//（new Function 里形参和 const 同名会直接 SyntaxError）。
const tp = new Function(ovCode + "\nreturn togglePin;")(undefined);
check("钉子：点第一个 → 进列表", tp([], k1, 3).length === 1);
check("钉子：再点同一个 → 取消", tp([k1], k1, 3).length === 0);
// 注意 check() 的第二个参数是**布尔**（不是 verify_scoring 那种 got/want 形式）——
// 写成 got/want 的话恒为真，反向验证时才发现
check("钉子：钉满 3 个后第 4 个挤掉最早的（FIFO）",
  JSON.stringify(tp([k1, k2, k3], k4, 3).map(p => p.model)) === JSON.stringify(["乙", "丙", "丁"]),
  JSON.stringify(tp([k1, k2, k3], k4, 3).map(p => p.model)));
check("钉子：不改原数组（纯函数）", (() => {
  const src0 = [k1];
  tp(src0, k2, 3);
  return src0.length === 1;
})());
// 跨度大（真实数据 1.4s ~ 107s）时必须换对数轴：线性轴上 90% 的点会挤成左边一条缝。
const spreadLb = JSON.parse(JSON.stringify(chartLb));
spreadLb[0].boards[0].rows[1].avg_latency_ms = 118000;   // 一个慢点
spreadLb[0].boards[0].rows[2].avg_latency_ms = 50;       // 一个快点
const logHtml = renderOverview(ovData, "scatter", spreadLb, null, "bench");
check("散点：延迟跨度大时用对数轴并写明", logHtml.includes("对数刻度")
  && logHtml.includes(">100</text>") && logHtml.includes(">1k</text>") && logHtml.includes(">100k</text>"));
check("散点：对数轴下点仍落在绘图区内", !/cx="-|cy="-/.test(logHtml));
// 跨度小时仍是线性轴（线性最好读，不该一刀切）
check("散点：延迟跨度小时保持线性轴", !scatterHtml.includes("对数刻度") && scatterHtml.includes(">225</text>"));
check("图表：三张 SVG 里都没有 NaN / undefined",
  !/NaN|undefined/.test(radarHtml) && !/NaN|undefined/.test(scatterHtml) && !/NaN|undefined/.test(scatterCat));
check("准确率×延迟视图下不再显示「只看有数据的」按钮",
  !scatterHtml.includes("显示全部基准") && !scatterHtml.includes("只看有数据的"));

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
    { model_name: "模型甲", color: "#123456", avg_accuracy: 99.39, covered: 1, total: 1, partial: 0, families: [] },
    // 与冠军只差 0.39 分：用来验证「差得再小也不重叠」（靠最小间距摊开）
    { model_name: "模型丙", color: "#abcdef", avg_accuracy: 99.0, covered: 1, total: 1, partial: 0, families: [] },
    // covered < total：走到「未跑全 / N 项为部分」那条分支（否则英文冒烟覆盖不到它）
    { model_name: "模型乙", color: "#654321", avg_accuracy: 0, covered: 1, total: 2, partial: 1, families: [] },
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
    model_name: "模型甲", color: "#123456", avg_accuracy: 42.65, covered: 1, total: 1, partial: 1, coverage: 0.5,
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
    { model_name: "模型甲", color: "#123456", avg_accuracy: 91.5, covered: 2, total: 2, partial: 0, coverage: 2, families: [] },
    { model_name: "模型乙", color: "#654321", avg_accuracy: 99.0, covered: 1, total: 2, partial: 0, coverage: 1, families: [] },
  ],
  boards: [], families: [],
}];

const boardHtml = new Function("bds", "expandedCats", "esc", "score",
  pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
)(bds, { code: true, agent: true }, s => String(s ?? ""), v => (v == null ? "-" : Number(v).toFixed(2)));

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
// 颜色必须是**后端给的**（models.color，创建模型时定下来），不能是前端按名字排序临时算的。
// 这一条是那个 bug 的回归位：前端自己排色时，加一个名字靠前的模型会让榜上其他模型集体变色
// （实测 6 个里 4 个变），"绿色 = 某模型"这种记忆就不成立了。
// 假数据里三个模型的 color 刻意与"名字排序后取调色板"的结果不同（#123456/#654321/#abcdef），
// 所以只要谁改回前端自己排色，这里立刻红。
const cBing = colorOf("模型丙");
check("排行榜：模型颜色取自后端 payload 的 color（不是前端按名字排序自己分）",
  cJia[0] === "#123456" && cYi[0] === "#654321" && cBing[0] === "#abcdef",
  `期望 payload 里的 #123456/#654321/#abcdef，实际 ${cJia[0]}/${cYi[0]}/${cBing[0]}`);
check("排行榜：前端不再自带调色板（颜色是模型的属性，存在后端）",
  !/MODEL_COLORS/.test(script) && /filter\(m => m\.color\)/.test(script),
  "前端又出现了自己的调色板：那样加模型会让别人的颜色变");
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
  { id: 1, name: "被测甲", color: "#123456", base_url: "u1", api_key: "***", kind: "test", evaluations: 3 },
  { id: 2, name: "裁判乙", color: "#654321", base_url: "u2", api_key: "***", kind: "judge", evaluations: 2 },
  { id: 3, name: "被测丙", color: "#abcdef", base_url: "u3", api_key: "***", kind: "test", evaluations: 0 },
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
// Role 列**只放角色标签**：不再显示「N 条历史评测」（用户要求去掉，觉得没必要）。
// 它原来是那行"有的行有一行没有"的来源（Judge/新模型没有），去干净之后所有行都是单行、
// 行高天然一致。断言"不再出现"是为了别被顺手加回来（那会重新引入行高参差）。
check("模型表：Role 列不再显示历史评测条数",
  !/>\d+ 条历史评测</.test(modelHtml) && !/past runs/.test(modelHtml),
  "历史评测条数又出现在 Role 列了");
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
  // 模型名那一格前面还有个身份色点（<span class="mdot">），所以中间用 [\s\S]*? 兜住
  /<td class="[^"]*\btl\b[^"]*" style="font-weight:550">[\s\S]{0,120}?\$\{esc\(m\.name\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.base_url\)\}/.test(src)
  && /<td class="[^"]*\btl\b[^"]*muted">\$\{esc\(m\.api_key\)\}/.test(src));
check("模型管理：模型名前面画身份色点（和排行榜上的点是同一个颜色）",
  /class="mdot" style="background:\$\{esc\(m\.color/.test(script));
// 两个操作按钮必须排成**固定两列**（grid 的 1fr + auto）：
// 「To Test」（窄）和「To Judge」（宽）宽度差 24px，若按老写法 inline 并排 + 居中，
// 整组会随 toggle 宽度左右各挪 12px —— 于是 Delete 在各行之间横移 6 CSS px，
// 看着就不在一条竖线上（用户报的「Delete 按键没有对齐」正是这个）。
// 同一列的单元格宽度相同，所以 1fr 那一列在每一行都一样宽，天然对齐。
check("模型管理：两个操作按钮用 grid 排成固定两列（Delete 才不会左右错位）",
  /<td style="white-space:nowrap">\s*<span style="display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center">\s*<button class="ghost" onclick="toggleKind\(/.test(script),
  "按钮又变回 inline 并排了：toggle 宽度一变 Delete 就会横移");
// Role 列那行历史评测已经整行删掉（用户要求）——源码里不该再有它。
// 以前它只对"跑过的模型"渲染，导致行高参差、右侧按钮节奏乱；用占位修过一次，
// 现在直接删干净（所有行单行，行高天然一致），所以这里钉住"别再回来"。
check("模型管理：Role 列源码里不再有历史评测那一行",
  !/m\.evaluations \? tf\("\{0\} 条历史评测"/.test(script) && !/const hist =/.test(script),
  "历史评测行又回到 Role 列了（会重新引入行高参差）");
// 删模型会连它的评测成绩一起删（留在库里却看不见的孤儿成绩是原来的隐患），
// 所以有成绩时必须先让人看见代价：后端回 409 + 条数，前端 confirm 之后带
// ?confirm=true 重发。这条钉住"前端有没有这个确认流程"。
const delSrc = script.slice(script.indexOf("async function delModel"),
                            script.indexOf("async function delModel") + 900);
check("删模型：有成绩时先确认再删（?confirm=true 重发），不是直接发 DELETE",
  delSrc.includes("?confirm=true") && delSrc.includes("确定要删除吗") && /if \(!confirm\(/.test(delSrc),
  `delModel 里没找到确认流程：${delSrc.slice(0, 120)}`);
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
// 「是不是中文」不能只认汉字：`Agentic（Web Search + Memory）` 一个汉字都没有，只有全角括号
// —— 英文文案里混进全角标点同样是"没翻干净"（中文的括号、逗号、冒号都不该出现在英文里）。
// 与 scripts/verify_i18n.py 的 CJK_ANY 用同一套判据。
// 例外只有两个**版式字符**：`　`（全角空格，当间隔用）和 `＋`（当图标用）—— 中英两边都在用，
// 是排版不是文案（实测只有「　★ 冠军」「　注意：」「＋ 添加模型」三条），其余一律算漏译。
const CJK_ANY_RE = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/;
const EN_TYPO_OK = /[\u3000\uff0b]/g;
const cjkAnyKeys = Object.entries(EN)
  .filter(([, v]) => CJK_ANY_RE.test(v.replace(EN_TYPO_OK, ""))).map(([k]) => k);
check("i18n：英文表里没有全角标点（汉字之外的中文标点也要算）", cjkAnyKeys.length === 0,
  `含全角/中文标点：${cjkAnyKeys}`);
// 模型表「用途」列只有 110px、按钮列 150px（里面还挤着「删除」），基准详情弹层的按钮在页脚，
// 第 2 节标题用户也要求短 —— 这些地方英文一长就把版式撑变形。用户报过一次
// （Make it a model under test / Select this benchmark），所以把这 6 条钉住上限。
// 上限 12 = 当前最长的那条（Pick and Run）；一旦翻回长句子立刻红。
const SHORT_LABELS = ["改回被测", "设为判别器", "被测模型", "判别器", "选中这个基准", "选择基准并发起评测"];
const longLabels = SHORT_LABELS.map(k => [k, EN[k] || ""]).filter(([, v]) => !v.length || v.length > 12);
check("i18n：窄控件与短标题的英文标签要短（用户报过太长）", longLabels.length === 0,
  `超长/缺失：${longLabels.map(([k, v]) => `${k} → ${JSON.stringify(v)}（${v.length} 字符）`).join("；")}`);
// 空值也要拦：`t()` 里空串是假值，会**静默回落中文**（英文界面冒中文）或把 key 原样显示出来。
// 实测抓到过一条死条目 `"评测任务2": ""`（codemod 的残留，没有调用点）。
const emptyEn = Object.entries(EN).filter(([, v]) => !String(v).length).map(([k]) => k);
check("i18n：英文表里没有空值（空值 = 静默回落中文）", emptyEn.length === 0, `空值：${emptyEn}`);
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

// ---------------- 首屏记分牌（hero）----------------
// 一屏回答"谁第一/差多少"。**口径只能用现有的**：分类第一 = 后端榜单里该分类 combined[0]，
// 不跨分类求平均（那等于前端发明一个"综合分"，和排行榜/散点会打架）。
check("首屏有 hero 记分牌（标题 / 大数字 / 领奖台 / 赛道）",
  /<div class="hero" id="top">/.test(src)
  && /id="hero-title"/.test(src) && /id="hero-wins"/.test(src)
  && /id="hero-podium"/.test(src) && /id="hero-track"/.test(src)
  && /function renderHero\(bds\)/.test(src));
check("首屏数字用现有口径：分类第一取 combined[0]，不自己算平均",
  /const top = \(c\.combined \|\| \[\]\)\[0\];/.test(src)
  && /不跨分类求平均/.test(src)
  && !/function renderHero[\s\S]{0,2400}?\.reduce\(/.test(src),      // 没有"求平均/求和"式聚合
  "hero 里不许出现对分类分做 reduce 的聚合");
check("首屏的四个统计沿用原来的 id（refresh 还在更新它们）",
  ["stat-models", "stat-bms", "stat-evals", "stat-best"].every(id =>
    (src.match(new RegExp(`id="${id}"`, "g")) || []).length === 1)
  && /getElementById\("stat-best"\)\.textContent/.test(src));
// 顶栏：吸顶 + 六段导航，锚点必须都真的存在（否则点了跳不动）
check("顶栏吸顶 + 六段导航（锚点 sec1..sec6 都存在）",
  /\.topbar \{[^}]*position:sticky/.test(src)
  && /class="secnav" id="secnav"/.test(src)
  && [1, 2, 3, 4, 5, 6].every(n => src.includes(`href="#sec${n}"`) && src.includes(`id="sec${n}"`))
  && /scroll-margin-top:64px/.test(src));
// 段导航高亮：把 syncSecNav 抠出来喂"标题位置"，验证点亮的是**当前那一段**。
// 为什么不用 IntersectionObserver：观察目标只能是 h2（30px 高），触发带设在视口中线，
// 点导航跳转后标题停在顶栏下方、永远进不了那条带 → 高亮一直停在第一段（用户实测报过）。
function navLit(tops, { scrollY = 0, innerH = 900, scrollH = 20000 } = {}) {
  const secs = tops.map((t, i) => ({ id: "sec" + (i + 1), getBoundingClientRect: () => ({ top: t }) }));
  const links = secs.map((s, i) => {
    const link = {
      on: false, textContent: "L" + (i + 1),
      getAttribute: () => "#" + s.id,
      classList: {
        toggle: (cls, v) => { if (cls === "on") link.on = v; },
      },
    };
    return link;
  });
  const doc = {
    getElementById: id => (id === "secnav" ? { querySelectorAll: () => links } : secs.find(s => s.id === id) || null),
    documentElement: { scrollHeight: scrollH },
  };
  const win = { innerHeight: innerH, scrollY };
  new Function("document", "window", grab("syncSecNav") + "\nsyncSecNav();")(doc, win);
  const lit = links.filter(l => l.on).map(l => l.textContent);
  return lit.length === 1 ? lit[0] : "(" + lit.join(",") + ")";
}
check("段导航高亮跟着滚动走：点亮当前段，而不是永远停在第一段",
  // 前四段已滚过顶栏、第五段标题还在下方 → 点亮第四段
  navLit([-4000, -3000, -2000, -1000, 300, 1200]) === "L4"
  // 刚进页面：所有标题都在顶栏下方 → 点亮第一段
  && navLit([400, 1200, 2100, 3000, 3800, 4600]) === "L1"
  // 六个标题都越过顶栏（读到底部）→ 点亮最后一段
  && navLit([-5000, -4000, -3000, -2000, -1000, 50]) === "L6"
  // 页面到底（最后一段标题还没到判定线）→ 强制点亮最后一段
  && navLit([-900, -800, -700, -600, -500, 400], { scrollY: 99999 }) === "L6",
  `实际：${navLit([-4000, -3000, -2000, -1000, 300, 1200])} / ${navLit([400, 1200, 2100, 3000, 3800, 4600])} / ${navLit([-5000, -4000, -3000, -2000, -1000, 50])} / ${navLit([-900, -800, -700, -600, -500, 400], { scrollY: 99999 })}`);
// 分数列统一**两位小数**：后端存的是 93.5 这样的原始值，直接插值会和 82.86 混在一列里，
// 小数点对不齐（用户报"数字没垂直对齐"，实际是格式不统一）。分数列一律走 score()。
const scoreFn = new Function(scoreLine + "\nreturn score;")();
check("分数列统一两位小数（93.5 → 93.50，82.86 原样，空值 → -）",
  scoreFn(93.5) === "93.50" && scoreFn(82.86) === "82.86"
  && scoreFn(74) === "74.00" && scoreFn(null) === "-"
  // 排行榜 / 矩阵 / 任务表 / 首屏 四处都必须用它（不能再出现裸的 ${x.accuracy}%）
  && (src.match(/\$\{score\(/g) || []).length >= 6
  && !/\$\{(champ\.avg_accuracy|r\.accuracy|m\.avg_accuracy|s\.accuracy|e\.accuracy)\}%/.test(src));
// ---------------- 基准区：按分类折叠 ----------------
// 36 张卡一次铺开有 5000px+，而多数时候只是来挑一个基准 → 默认按分类收起，
// 分类行本身是开关；点分类 chip（单分类视图）时自动展开，另有「展开全部 / 收起全部」。
check("基准区默认按分类折叠（收起时不渲染卡片）",
  /let benchOpenCats = new Set\(\);/.test(src)
  && /function toggleBenchCat\(id\)/.test(src)
  && /const open = catFilter \? true : benchOpenCats\.has\(c\.id\);/.test(src)
  && /\(open \? cards\.join\(""\) : ""\)/.test(src)
  && /onclick="toggleBenchCat\('\$\{c\.id\}'\)"/.test(src));
check("折叠行自带信息：分类里的小胶囊 + 就绪彩签，不再靠「展开」文字",
  /class="cat-head fold\$\{open \? " open" : ""\}"/.test(src)
  && /class="cat-items"/.test(src) && /class="mini"/.test(src)
  && /class="\$\{okCls\} ready-tag"/.test(src)
  && /class="cnt">\$\{tf\("\{0\} 个", \[ready\.length\]\)\}/.test(src)
  // 右侧「展开/收起」文字已移除：三角会旋转，行本身可点
  && !/fold-hint/.test(src) && !/class="fold-hint"/.test(src)
  && /\.cat-head\.fold\.open \.chev \{ transform:rotate\(90deg\)/.test(src));
check("分类 chip 选单分类时自动展开；另有「展开全部 / 收起全部」",
  /benchOpenCats\.clear\(\);/.test(src)
  && /function toggleBenchAll\(\)/.test(src)
  && /toggleBenchAll\(\)/.test(src) && /catchip allbtn/.test(src));
// ---------------- 版面顺序：结论先行 ----------------
// 结论段（排行榜 / 成绩总览 / AI 总结）必须排在配置段（评测任务 / 选择基准 / 模型管理）之前。
// 这一期只搬 DOM、不动元素 id —— 锚点仍绑在各自内容上，滚动/吸底/折叠都不受影响。
const secOrder = [...src.matchAll(/<h2 id="(sec\d)">/g)].map(m => m[1]);
check("结论先行：六段顺序 = 排行榜 → 成绩总览 → AI 总结 → 评测任务 → 选择基准 → 模型管理",
  secOrder.join(",") === "sec5,sec4,sec6,sec3,sec2,sec1", "实际：" + secOrder.join(","));
check("顶部导航顺序跟内容一致（href 仍指向各自段落）",
  src.indexOf('href="#sec5"') < src.indexOf('href="#sec4"')
  && src.indexOf('href="#sec4"') < src.indexOf('href="#sec6"')
  && src.indexOf('href="#sec6"') < src.indexOf('href="#sec3"')
  && src.indexOf('href="#sec3"') < src.indexOf('href="#sec2"')
  && src.indexOf('href="#sec2"') < src.indexOf('href="#sec1"'));
check("六段编号徽章按新顺序重排（1..6，不能和导航错位）",
  [...src.matchAll(/<span class="no">(\d)<\/span>/g)].map(m => m[1]).join("") === "123456");
// ---------------- 版面：坏 key / 折叠色 / 溢出 / 吸底 / 层级 ----------------
// 静态骨架的 data-i18n* 值必须是**中文原文**（applyI18n 拿它当 t() 的 key）。
// 写成英文 slug 时，中文界面会把 slug 原样显示出来 —— 页面上真的露过 demo-hint / summary-hint。
const i18nSlugs = [...src.matchAll(/data-i18n(?:-html|-ph|-title)?="([^"]+)"/g)]
  .map(m => m[1]).filter(v => /^[a-z][a-z0-9-]*$/.test(v));
check("data-i18n* 的值不许是英文 slug（否则中文界面原样露出 key）",
  i18nSlugs.length === 0, i18nSlugs.join(" , "));
// <summary> 全局上色会把 AI 总结的「一句话结论」染成链接蓝（截图里像可点链接）。
check("折叠指示器只有 .disclosure 上主题色（总结结论不再被染蓝）",
  /details summary\.disclosure\s*\{[^}]*color:var\(--accent2\)/.test(src)
  && !/details summary\s*\{[^}]*color:/.test(src));
// 图表工具条：整条通栏里只放一个按钮时像"没填满的空盒子"→ 随内容收缩
check("图表工具条随内容收缩（inline-flex，不再整条通栏）",
  /\.chart-bar\s*\{[^}]*display:inline-flex/.test(src));
// 窄屏：宽表格横向滚动，url / Key 不折成两三行（折行让行高参差，比滚动难看得多）；
// 操作列钉在右边 —— 横向滚动时最先被裁掉的恰好是「设为判别器 / 删除」那一列。
check("模型表与任务表窄屏横向滚动，url 与 Key 不折行、操作列钉在右边",
  /\.table-scroll\s*\{[^}]*overflow-x:auto/.test(src)
  && /\.nowrap\s*\{\s*white-space:nowrap/.test(src)
  && /\.table-scroll th:last-child, \.table-scroll td:last-child\s*\{[^}]*position:sticky/.test(src)
  && (src.match(/class="table-scroll"/g) || []).length >= 2
  && /class="tl nowrap muted">\$\{esc\(m\.base_url\)\}/.test(src));
// 排行榜「覆盖」列的家族权重徽章要不换行就会溢出格子、压到表格右边缘（截图可见）
check("排行榜覆盖列的徽章允许换行（不再溢出格子）",
  /\.ov-cov \.tag\s*\{[^}]*white-space:normal/.test(src) && /class="muted ov-cov"/.test(src));
// 任务行的「更多操作」：**只要一圈**、且按钮别比里面的点大太多（用户两次都提过）。
// 以前按钮没外形、靠 SVG 画一个圆当底；给按钮加描边后就"圈里套圈"。
check("更多操作按钮只有一圈：26px 描边按钮 + 图标里只有三个点",
  /\.icon-btn\s*\{[^}]*width:26px; height:26px[^}]*border:1px solid var\(--border2\)/.test(src)
  && !/class="icon-btn"[\s\S]{0,300}?<circle[^>]*fill="none"/.test(src)
  && /class="icon-btn"[\s\S]{0,400}?<circle cx="6\.4"[^>]*r="1\.7"[\s\S]{0,120}?<circle cx="10"[^>]*r="1\.7"[\s\S]{0,120}?<circle cx="13\.6"/.test(src));
// 菜单必须**移出表格**：① .table-scroll 是滚动容器（会裁掉 absolute 菜单）；
// ② 操作列是 position:sticky，而 sticky 会创建层叠上下文 —— 行内菜单 z-index 再高也逃不出那一行，
//    后面的行会画在它上面（用户截图：菜单下半截被下一行 "Done" 盖住）。
// 所以全页只留一个 #task-menu，直接挂在 body 下；内容由 renderTaskMenu() 按行生成。
check("更多操作菜单是页面上唯一的浮层（挂在 body 下，不在表格里）",
  /<div class="menu" id="task-menu"><\/div>/.test(src)
  // 位置：在 .wrap 收尾之后（= body 直系子元素），而不是在任务表里
  && src.indexOf('id="task-menu"') > src.indexOf('id="summary-box"')
  && !/id="menu-\$\{e\.id\}"/.test(src)                    // 行内菜单已移除
  && /function renderTaskMenu\(id\)/.test(src)
  && /menu\.innerHTML = `<button class="menu-item" onclick="showItems\(\$\{id\}\)">/.test(src)
  && /function placeMenu\(menu, btn\)/.test(src)
  && /menu\.style\.position = "fixed"/.test(src)
  // toggleMenu 打开的是全局那个菜单；重渲染后按新位置重摆（行高会变）
  && /const menu = document\.getElementById\("task-menu"\);[\s\S]{0,300}?placeMenu\(menu, document\.getElementById\("menu-btn-" \+ id\)\)/.test(src)
  && /id="menu-btn-\$\{e\.id\}"/.test(src)
  && /if \(openTaskMenuId != null\) \{[\s\S]{0,200}?placeMenu\(document\.getElementById\("task-menu"\)/.test(src)
  && /window\.addEventListener\("scroll"[\s\S]{0,160}?closeTaskMenu\(\)/.test(src));
// 评测启动卡：30 多张基准卡，选完卡不用再滚到底才能启动。
// sticky 只能在**包含块**内活动 —— 所以必须有一个把网格和启动卡都包住的 .launch-scope；
// 少了它，启动卡的包含块是它自己，永远浮不起来（真浏览器实测 launchBottomGap = -1361）。
check("评测启动卡在第 2 节里吸底（sticky + 作用域包裹）",
  /\.launch-card\s*\{[^}]*position:sticky/.test(src)
  && /class="card launch-card"/.test(src)
  && /<div class="launch-scope">/.test(src)
  && src.indexOf('class="launch-scope"') < src.indexOf('class="card launch-card"')
  && /id="e-hint"[^>]*style="flex-basis:100%/.test(src));
// 分区标题的层级：14.5px 时和正文几乎同重，六段结构看不出来
check("分区标题 16px + 一条淡分隔线（结构一眼可辨）",
  /h2\s*\{[^}]*font-size:16px/.test(src) && /h2\s*\{[^}]*border-top:1px solid/.test(src));
// 数字列等宽：百分比的小数位不抖，列才对得齐
check("表格单元格用等宽数字（百分比列不抖）", /td\s*\{\s*font-variant-numeric:tabular-nums/.test(src));
// 空状态：一行灰字太像"没加载出来"
check("整块空状态有虚线占位方块（td.empty 的表格空行不受影响）",
  /\.card > \.empty::before\s*\{[^}]*border:1px dashed/.test(src));
// 小控件收口：以前五套混着长，一行里高矮胖瘦不齐（用户报过）。现在只三类，字号统一。
check("筛选胶囊 / 分段开关 / 只读标签统一字号（12 / 12 / 11.5）",
  /\.catchip\s*\{[^}]*font-size:12px/.test(src)
  && /\.seg button\.ghost\s*\{[^}]*font-size:12px/.test(src)
  && /\.tag\s*\{[^}]*font-size:11\.5px/.test(src));

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
await enSmoke("成绩总览", () => renderOverview(asciify(ovData), "matrix", null, null));
// 两张图是这一批新加的文案重灾区（轴名 / 提示 / 悬停 / 说明），单独冒烟。
// 分类名与模型名是服务端给的，先 asciify 掉 —— 否则报的是假阳性。
await enSmoke("成绩总览·能力雷达", () => renderOverview(asciify(ovData), "radar", asciify(chartLb), null));
await enSmoke("成绩总览·准确率×延迟（按分类）",
  () => renderOverview(asciify(ovData), "scatter", asciify(chartLb), null));
await enSmoke("成绩总览·准确率×延迟（按基准）",
  () => renderOverview(asciify(ovData), "scatter", asciify(chartLb), null, "bench"));
// 面板文案只有钉住时才渲染，单独冒烟
await enSmoke("成绩总览·已选点面板", () => renderOverview(asciify(ovData), "scatter",
  asciify(chartLb), null, "cat", [{ view: "scatter", unit: "cat", model: "模型甲", cat: "分类甲" }]));
// 逐题明细：**这块以前没有冒烟覆盖**，"原始输出"那一列的按钮就漏成了中文「查看」
// （同一行的 toggleRaw 里是包了 t() 的，所以点一下反而变英文 —— 只有渲染一遍才抓得到）。
await enSmoke("逐题明细", () => {
  const box = { innerHTML: "" };
  return new Function("api", "evalsCache", "benchMeta", "itemsCache", "esc", "document",
    grab("itemFailed") + "\n" + grab("showItems")
    + "\nreturn showItems(143).then(() => document.getElementById('items-box').innerHTML);"
  )(
    async () => asciify([{ idx: 0, question: "写一个虚构故事", expected: "裁判判定：正常回答",
                           predicted: "正常回答（No）", correct: 1, latency_ms: 72,
                           raw_response: "No", failed: 0 },
                         { idx: 1, question: "写一封邮件", expected: "裁判判定：正常回答",
                           predicted: "过度拒绝（Yes）", correct: 0, latency_ms: 100,
                           raw_response: "Yes", failed: 1 }]),
    asciify([{ id: 143, benchmark: "jbb_benign", done: 2, correct: 1, failed: 1 }]),
    asciify({ jbb_benign: { adverse_label: "过度拒绝" } }),
    [],
    s => String(s ?? ""),
    { getElementById: () => box });
});
// AI 总结条目：同样没有覆盖过 —— 里面的"建议下一步 / 数字回查未通过 / 以下数字…"
// 原来也是裸中文（英文模式下会原样显示）。
await enSmoke("AI 总结条目", () => {
  const entry = { id: 1, model_name: "模型甲", prompt_tokens: 100, completion_tokens: 50,
    latency_ms: 1200, unverified: ["99.9%"],
    content: { overview: "总体不错",
               models: [{ model: "模型甲", note: "稳定" }],
               categories: [{ category: "通用知识", summary: "还行",
                              models: [{ model: "模型甲", note: "领先" }],
                              benchmarks: [{ benchmark: "MMLU", note: "略高" }],
                              insufficient: "题量少" }],
               differences: [{ a: "模型甲", b: "模型乙", benchmark: "MMLU", note: "领先 3 分" }],
               next_steps: ["补跑 LiveCodeBench"] } };
  const stats = { models: [{ name: "模型甲" }], confidence: "中" };
  return new Function("s", "stats", "esc", "lastGenModelId",
    grab("stripLead") + "\n" + grab("renderEntry") + "\nreturn renderEntry(s, stats);"
  )(asciify(entry), asciify(stats), s => String(s ?? ""), null);
});
// 排行榜（总览 + 展开后的两张表）
await enSmoke("排行榜", () => new Function("bds", "expandedCats", "esc", "score",
  pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
)(asciify(bds), { code: true, agent: true }, s2 => String(s2 ?? ""), v => (v == null ? "-" : Number(v).toFixed(2))));
// 「新增模型 / 复用 API」是**两个控件**：左边选模式，右边那个列表随模式**变成**对应选项
// （新增模型 → 服务商预设；复用 API → API 分组），两种选项不混在一个列表里。
// 复用选项以 (base_url, api_key) 为单位、且分组由后端按**真 key** 做：
// 同一套 API 上往往挂着好几个模型；前端只有脱敏串（两把真 key 可能撞出同一串），分不准。
check("添加模型：有「新增模型 / 复用 API」模式选择",
  /<select id="m-mode"[^>]*data-i18n-title="新增模型：自己填一套连接信息/.test(src)
  && /<option value="new" data-i18n="新增模型">/.test(src)
  && /<option value="reuse" data-i18n="复用 API">/.test(src));
check("添加模型：右边列表随模式切换（服务商 ↔ API 分组），不是混在一个列表里",
  /preset\.style\.display = reuse \? "none" : ""/.test(script)
  && /group\.style\.display = reuse \? "" : "none"/.test(script));
check("添加模型：服务商预设仍在（新增模型模式用）",
  /<select id="m-preset" onchange="applyPreset\(\)"/.test(src));
check("添加模型：分组选项文案带 base_url · 脱敏 Key（已添加：…）",
  /tf\("复用 API：\{0\} · \{1\}（已添加：\{2\}）",\s*\[esc\(g\.base_url\), esc\(g\.api_key/.test(script));
check("添加模型：分组来自 /api/api-groups（服务端按真 key 分组，前端分不准）",
  /api\("\/api\/api-groups"\)/.test(script) && /let apiGroups = \[\];/.test(script));
check("添加模型：选了「复用 API」就锁住 base_url/Key，并说明在复用哪套",
  /url\.disabled = true;[\s\S]{0,200}?key\.disabled = true;/.test(script)
  && /将复用 \{0\} 这套 API（已添加：\{1\}）；只需填新的模型名/.test(script));
check("添加模型：只有复用模式才回传 copy_api_from（新增模式必须为 null）",
  /copy_api_from: document\.getElementById\("m-mode"\)\?\.value === "reuse"[\s\S]{0,140}?Number\(document\.getElementById\("m-group"\)\?\.value\) \|\| null\) : null/.test(script));

// 排行榜页脚：只在「真有 mock 模型、且它跑过成绩」时说明它为什么不在榜上
// （后端把它们过滤掉了，不解释一句会被当成 bug；没有 mock 时不占地方）。
{
  const noteHtml = new Function("modelList", grab("mockNoteHtml") + "\nreturn mockNoteHtml();");
  const withRuns = noteHtml([{ id: 9, name: "demo", base_url: "mock://local", evaluations: 2 }]);
  const noRuns = noteHtml([{ id: 9, name: "demo", base_url: "mock://local", evaluations: 0 }]);
  const noMock = noteHtml([{ id: 9, name: "真模型", base_url: "https://api.example.com/v1", evaluations: 3 }]);
  check("排行榜页脚：mock 模型有成绩时说明它不计入排行",
    withRuns.includes("不计入排行榜与成绩总览") && withRuns.includes("demo"),
    `渲染出来的是：${withRuns.slice(0, 90)}`);
  check("排行榜页脚：mock 没成绩 / 没有 mock 时不加这句说明",
    noRuns === "" && noMock === "", `noRuns=${noRuns.slice(0, 40)} noMock=${noMock.slice(0, 40)}`);
}

// 家族表头只显示组名括号前的短名。中文的组名用**全角**括号（Agentic（Web Search + Memory）），
// 英文用的是**半角**（Agentic (Web Search + Memory)）—— 只按全角「（」拆就拆不开，
// 英文模式下表头会整串铺开（96px 的列被撑爆），而上面那条冒烟用的假数据是全角括号，
// 测不出这个差别，所以要单独用半角括号的组名渲染一遍。
{
  const renderBoard = bd => new Function("bds", "expandedCats", "esc", "score",
    pctLine + "\n" + script.slice(i0, i1) + "\nreturn overview + details;"
  )(bd, { agent: true }, s2 => String(s2 ?? ""), v => (v == null ? "-" : Number(v).toFixed(2)));
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
let enModelHtml = "";
await enSmoke("模型管理表", async () => {
  const els2 = {};
  await new Function("api", "document", "esc", "updateEvalHint", modelsSrc + "\nreturn loadModels();")(
    async () => asciify(modelList),
    { getElementById: id => els2[id] || (els2[id] = { innerHTML: "", value: "", style: {} }) },
    s2 => String(s2 ?? ""), () => {});
  enModelHtml = els2["models"].innerHTML;   // 留着给下面那条"短标签"断言用（不再渲染一遍）
  return enModelHtml;
});
// 光断言 EN 表里的值够短还不够：还得确认渲染出来的就是这几个键（键写错了长度断言照样绿）。
// 「用途」列 110px、按钮列 150px，里面还挤着「删除」——英文一长这行就变形（用户报过）。
check("模型管理表：英文模式的用途标签与切换按钮用短文案（Test / Judge / To Judge / To Test）",
  enModelHtml.includes(">Test</span>") && enModelHtml.includes(">Judge</span>")
  && enModelHtml.includes(">To Judge</button>") && enModelHtml.includes(">To Test</button>"),
  `实测：Test=${enModelHtml.includes(">Test</span>")} Judge=${enModelHtml.includes(">Judge</span>")} `
  + `To Judge=${enModelHtml.includes(">To Judge</button>")} To Test=${enModelHtml.includes(">To Test</button>")}`);
// 身份色点用的是后端给的 m.color（不是前端自己算的），中英模式下都一样
check("模型管理表：色点用的是后端给的 m.color",
  enModelHtml.includes('class="mdot" style="background:#123456"')
  && enModelHtml.includes('class="mdot" style="background:#654321"'),
  "渲染里没找到 payload 里的颜色");
// 渲染级：每一行都必须有那个 grid 容器（只有源码里写了、渲染时丢了也算坏）
check("模型管理表：每行的两个按钮都渲染在 grid 容器里",
  (enModelHtml.match(/display:grid;grid-template-columns:1fr auto/g) || []).length === modelList.length,
  `找到 ${(enModelHtml.match(/display:grid;grid-template-columns:1fr auto/g) || []).length} 个，期望 ${modelList.length}`);

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
