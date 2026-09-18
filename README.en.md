# LLM Bench — an LLM benchmark suite

[中文](README.md) | **English**

A **local web app**: plug in any OpenAI-compatible endpoint (`base_url` + API key + model name) and it runs public benchmarks, scores them and builds a comparison leaderboard.

Everything runs locally — **API keys never leave your machine** and are never uploaded anywhere.

## Features

- **Model management**: add/remove models, connection test, provider presets (DeepSeek / Qwen / Kimi / GLM / OpenAI / OpenRouter / Ollama), keys masked in the UI
  - **Two roles: model under test / judge**. A judge only scores safety benchmarks and **never appears in the model dropdown for a run**;
    the backend also refuses to start a run with a judge as the target — the one thing safety evaluation must avoid is a judge that is
    itself being evaluated. Changing a role is reversible, it warns with the number of past runs first, and **past results are kept**
    (the data is real; once it exists, it stays)
- **Dataset downloads**: one-click download of 34 public benchmark datasets into `data/`; GitHub raw falls back to the ghproxy mirror, HuggingFace falls back to hf-mirror
- **Evaluation engine**: concurrent API calls, automatic scoring, 3 retries, per-item JSONL export
- **Run management**: **stop** a running job at any time (progress is kept); **deleting stops it first**; **batch delete**; resume an interrupted run (only the missing items)
- **UI in Chinese or English**: the language switch sits in the top bar, the choice is remembered, and a first visit follows the browser language (the backend returns every string in the selected language)
- **Leaderboard**: split by category (Knowledge / Chinese / Science / Commonsense / Math / Agent·tool use / Code / Safety·alignment). Start with the
  **overview (top model per category)**, click a category to expand the **combined ranking (average within the category) + per-benchmark boards**
  - Several runs of the same model on the same benchmark: **the best complete run wins**; runs that did not finish the dataset are marked
    “partial x/y”, sorted after complete results, excluded from best-score marking and from the category average
    (defined once in `app/scoring.py`, shared by leaderboard, overview and AI summary)
  - Sorted by **coverage first**, so running only the easy benchmarks does not buy a top spot
  - **A family of subsets folds into one benchmark**: BFCL v4's 16 subsets are one card in the UI (subset switcher in a dropdown, each option
    with its one-line summary) and one block in the leaderboard (“official weighted score + per-group scores + subset details, expandable”),
    with the **official group and weight** on every subset
  - **Official weighted score**: composed the official way (`Overall = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%`,
    average within a group, then weight the groups) and each row shows its **weight coverage** — this project covers 60% of the official weight
    (the Agentic 40% is missing), so it is never presented as a complete BFCL result; missing groups are renormalised over the groups that ran
- **Two layers of card copy**: the card shows a `summary` of at most two lines (“what sets it apart / what a low score means”, never repeating the
  name or the item count), and the **full description lives in a modal** (click the summary text or the “About” button in the card footer;
  it covers nothing) — plus a repeat above “Start run” (the last look before you spend money)
- **Overview matrix**: benchmark × model heat map with the best score per row marked separately (again only among complete runs; partial runs are
  not highlighted); click a model name for its capability profile
- **Item-by-item comparison**: tick two runs and see both answers side by side (optionally only the disagreements)
- **AI summary**: any connected model can write a Chinese or English evaluation summary — **the statistics are computed by code**
  (ranking, coverage, whether a gap is significant) and the model only explains them, so the numbers do not change with the writer
- **Safety / alignment benchmarks** (3 benchmarks, **scored by a judge model**): HarmBench direct requests (200), JailbreakBench harmful (100),
  JailbreakBench benign (100). The judge prompts are **copied verbatim from the official sources** (HarmBench classifier / JBB jailbreak and
  refusal judges) and the judge must be a model of role “judge” (never the model under test). **Read at least two of them together**: resistance to
  harmful requests alone ranks “refuses everything” highest, and the 100 benign requests exist precisely to expose that over-alignment.
  Changing the judge makes scores incomparable — the run list shows which judge produced each result, and `scripts/calibrate_judge.py` measures a
  judge against the official **human-labelled** sets before you commit to it (measured: `kimi-k3` agrees with humans **97.5%** on HarmBench and
  **95.0%** on JBB with no leniency bias; `qwen3.7-flash` scores 92.5% / 82.5%).
- **Question types**:
  - Multiple choice (**2-way** to 5-way): MMLU / C-Eval / CMMLU / MMLU-Pro / GPQA / TruthfulQA / ARC-Challenge / HellaSwag / WinoGrande
  - Numeric: GSM8K / MATH-500 / AIME (integer answers)
  - Function calling: BFCL v4 (AST-matched scoring, including the Live subsets built from real questions)
  - Safety / alignment: HarmBench / JailbreakBench (**judge-scored**, see above)
  - Multi-turn: BFCL v4 multi_turn (simulated environment, several tool calls per turn)
  - **Code execution**: HumanEval / LiveCodeBench — the model's code really runs inside a throwaway Docker container

## Quick start

### Requirements

- Python 3.10+
- Dependencies in `requirements.txt`

### Install

```bash
pip install -r requirements.txt
```

### Run

```bash
# from the project root
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**

### Try it without an API key

You can walk the whole pipeline without a key: set the model name to `demo` and base_url to `mock://local`, and a built-in mock model
(roughly 70% correct) drives the evaluation.

## How to use

1. **Add a model**: `base_url` + API key + model name (a provider preset can fill base_url), then “Test connection”
2. **Download a dataset** (optional): click “Download” on a benchmark card, or use the command line (below)
3. **Start a run**: pick a benchmark and a model, click “Start run”
4. **Read the results**: the run list shows progress and accuracy (latest 5 by default, expandable and paged), “Details” shows every item,
   and the leaderboard compares models

## Supported datasets

| Category | Dataset | Items | Type |
|---|---|---|---|
| Knowledge | MMLU | ~14k | 4-way |
| Knowledge | MMLU-Pro | ~12k | 10-way |
| Knowledge | TruthfulQA | 776 | 4–10 way |
| Chinese | C-Eval | 1346 | 4-way |
| Chinese | CMMLU | ~11k | 4-way |
| Science | GPQA Diamond | 198 | 4-way |
| Science | ARC-Challenge | 1172 | 4–5 way |
| Commonsense | HellaSwag | 10042 | 4-way |
| Commonsense | WinoGrande | 1267 | **2-way** |
| Math | GSM8K | 7473 | numeric |
| Math | MATH-500 | 500 | numeric / expression |
| Math | AIME 2022-2024 | 90 | integer |
| Math | AIME 2025 | 30 | integer |
| Agent / tool use | BFCL v4 · single function (Python / Java / JavaScript) | 550 | function call |
| Agent / tool use | BFCL v4 · multiple choice | 200 | function call |
| Agent / tool use | BFCL v4 · parallel / parallel + multiple | 400 | function call |
| Agent / tool use | BFCL v4 · irrelevance | 240 | refuse to call |
| Agent / tool use | BFCL v4 · multi-turn (base / long context / missing function / missing parameter) | 800 | multi-turn tool use |
| Agent / tool use | BFCL v4 · **Live** single / multiple / parallel / parallel + multiple | 1351 | function call (real questions) |
| Agent / tool use | BFCL v4 · **Live** irrelevance | 884 | refuse to call (real questions) |
| Safety / alignment | HarmBench (direct requests) | 200 | judge-scored (was the behaviour carried out) |
| Safety / alignment | JailbreakBench harmful | 100 | judge-scored (was the jailbreak successful) |
| Safety / alignment | JailbreakBench benign | 100 | judge-scored (was it over-refused) |
| Code | HumanEval | 164 | sandbox runs the official unit tests |
| Code | LiveCodeBench v6 | 175 | sandbox runs the contest test cases |
| Sample | MMLU / C-Eval samples | 12 each | 4-way |

> **36 benchmarks** in total (including the 2 samples; the Agent category holds 16 BFCL v4 subsets folded into one “BFCL v4” card).
> BFCL v4 has 22 subsets officially and this project ships 16 of them (60% of the official weight) — the missing Web Search / Memory
> groups need a paid SerpAPI service and a memory preprocessing plus embedding-model step respectively, both at odds with “local and reproducible”.

## Dataset downloads

```bash
python scripts/download.py                    # all 34 downloadable datasets
python scripts/download.py cmmlu gpqa         # selected datasets
python scripts/download.py BFCL_v4_multi_turn_base   # a single BFCL subset
python scripts/download.py BFCL_v4_live_simple BFCL_v4_live_irrelevance   # BFCL Live subsets
python scripts/download.py humaneval livecodebench   # code benchmarks
```

You can also click “Download” on a benchmark card and let the backend run the script (the card shows the download status).

> Downloads are **atomic**: the data is written to `xxx.jsonl.part` and renamed to `xxx.jsonl` only once complete.
> During a download you therefore still see the old file (or nothing) rather than a
> “truncated but plausible-looking” dataset — such a file would be treated as complete and make coverage wrong.

> Sources: MMLU / C-Eval / GPQA / MMLU-Pro / GSM8K / MATH-500 come from the HuggingFace datasets-server; CMMLU / BFCL / HumanEval / TruthfulQA from
> GitHub; AIME from HuggingFace parquet; LiveCodeBench from HuggingFace jsonl (~134MB, **v6 only**). Networks in some regions may need access to
> these sources (GitHub raw falls back to ghproxy, HuggingFace to hf-mirror).

## Project layout

```
app/
  main.py           # FastAPI routes (models / datasets / runs / leaderboard / downloads / sandbox status)
  config.py         # global constants: paths / option letters / defaults / sandbox concurrency
  datasets.py       # dataset lookup and loading, per-item export, line-count cache
  llm.py            # model call layer (OpenAI wrapper / retries / mock)
  runner.py         # run orchestration (run_evaluation / progress / resume)
  engine.py         # backwards-compatible facade: re-exports the above (do not use in new code)
  i18n.py           # UI language: Chinese source strings as keys, EN table, per-request ContextVar
  qtypes/           # question types: prompt, answer extraction, scoring
    __init__.py       # registry + detect()
    types.py          # Outcome / RunCtx / QuestionType
    choice.py         # multiple choice
    numeric.py        # numeric / expression
    code_unit.py      # HumanEval style (sandbox runs the official unit tests)
    code_stdio.py     # LiveCodeBench style (sandbox runs the contest cases)
    _code.py          # shared code-question layer (extraction + token ladder)
    bfcl.py           # BFCL single-turn function calls
    bfcl_multi_turn.py# BFCL multi-turn
  benchmarks/       # benchmark registry: metadata and downloader in the same file
    __init__.py       # collects each module's ENTRIES, derives META / DOWNLOADERS / AVAILABLE
    types.py          # Benchmark entry type
    _util.py          # shared download helpers (HTTP / gzip / parquet / write jsonl)
    mmlu.py ceval.py cmmlu.py gpqa.py mmlu_pro.py gsm8k.py math500.py
    aime.py truthfulqa.py humaneval.py livecodebench.py bfcl.py
  sandbox.py        # code-execution sandbox (Docker) + in-container scorer
  lcb.py            # safe decoding of LiveCodeBench test cases
  scoring.py        # scoring policy: dataset-derived facts + “does a run count” (shared by leaderboard/overview/summary)
  summary.py        # AI summary: statistics layer (ranking/coverage/significance/guards) + generation
  db.py             # SQLite wrapper
  static/index.html # single-page frontend (no build step)
scripts/
  download.py           # unified download CLI (implemented under app/benchmarks/)
  resume_eval.py        # resume an interrupted run
  verify_all.py         # run every self-check below in one go
  verify_imports.py     # static check: undefined names / broken relative imports
  verify_datasets.py    # datasets and metadata: line cache / item-count agreement / atomic writes / card copy fits
  verify_i18n.py        # language: detection / fallback / missing translations
  verify_models.py      # model roles: a judge cannot be evaluated / role change confirmation (temp DB)
  verify_safety.py      # safety: verdict direction / judge constraints / scoring failures (mock judge)
  calibrate_judge.py    # calibrate a judge against the official human labels (costs tokens, supports --dry-run)
  verify_scoring.py     # scoring policy: complete vs partial runs (temp DB, never touches data/app.db)
  verify_dispatch.py    # dispatch: 10 cases across 6 question types (mock model, costs nothing)
  verify_code_assembly.py # code assembly: benchmarks that ship helper functions / contest items
  verify_sandbox.py     # sandbox isolation checks (runs attack payloads)
  verify_humaneval.py   # HumanEval extraction + reference solutions
  verify_livecodebench.py  # LiveCodeBench scorer
  verify_web_templates.mjs # frontend template rendering (needs Node, not a browser)
  verify_summary.py     # statistics layer (including reconciliation with the database)
  verify_summaries.py   # generated AI summaries vs the statistical facts
data/               # datasets, database, results (gitignored; only samples are kept)
requirements.txt
```

## Extending: a new question type / a new benchmark

**Question types** (how to ask, how to score) live behind the registry in `app/qtypes/`; the main loop does exactly one thing:

```python
outcome = await qtypes.detect(ctx, item).runner(model_cfg, item, params, ctx)
```

A new type = a new `app/qtypes/<id>.py` plus one entry in `TYPES` (detect + runner + whether it needs the sandbox) —
**`run_evaluation` does not change**. This used to be a chain of hard-coded `if/elif` with four scoring paths inline in the main loop.

**Benchmarks** (dataset + metadata + downloader) live under `app/benchmarks/` as **one `Benchmark` record per benchmark, metadata and
downloader in the same file**; `__init__.py` collects them into a registry and derives `META` / `DOWNLOADERS` / `AVAILABLE`.

Each benchmark has **two copy fields**; do not mix them up:

| Field | Where it shows | What to write | Length limit |
|---|---|---|---|
| `summary` | card body | “**what sets it apart / what a low score means / why it is worth running**” — do not restate what the name already says, and do not repeat the item count (the card already shows it) | **≤ 40 Chinese characters** (two lines on the narrowest card; longer is clipped by CSS) |
| `description` | the modal behind “About” + above “Start run” | the full rubric: item shape, scoring, caveats, contamination risk | unlimited |

Cards used to clip `description` to two lines: 24 of 30 benchmarks (80%) were unreadable and the longest showed only 23%.
The problem was never “text too long” but **one string being asked to be both a teaser and a manual**. Now the card holds a two-line `summary`
and the full text appears in a **modal** (click the summary or the “About” button; close via the overlay, “Close” or Esc) plus above the run panel.

> Those constraints (every benchmark has a summary, it fits two lines, it does not restate the name or the item count) are enforced by
> `python scripts/verify_datasets.py` — clipped copy looks perfectly normal on the page, so the eye cannot catch it.
> The same script checks the English copy in the same way, with a character-based threshold because English line width differs.
>
> **Datasets you drop into `data/` yourself** (with no `Benchmark` record) use the `FALLBACK` metadata and still need a summary that fits:
> the card says “a dataset you put in data/ yourself: no official rubric, so the card shows only the file name and item count” instead of
> one generic format blurb shared by every custom dataset (the full description explains the jsonl fields).

**A new benchmark = a new file** (or one more entry in a source module's `ENTRIES`), nothing else changes. It used to require four places
(the old `benchmarks.py` META, two dictionaries in `download.py`, and the script holding the downloader); one id was spread over 10 files.
The English copy goes in the same record (`name_en` / `summary_en` / `description_en` / `label_en` / `status_en`), so the two languages
cannot drift apart.

**Several subsets of one source (a family)**: declare a `Family` (with the official groups and weights) in the module and give each `Benchmark`
a `family=` / `group=`; the UI then **folds them into one benchmark** (subset switcher when picking, one block with the official weighted score
plus per-group scores in the leaderboard) and the official weighted score is computed the official way.

For BFCL, **a new subset = one row in `_SUBSET_META`** (id, official file name, group, item count, copy) with its English copy in `_SUBSET_EN`;
group weights are declared once in `FAMILY_DEFS`. The registry validates on import: group ids must exist, weights must sum to 1.0,
`group` cannot appear without `family` — a mistake fails at startup instead of silently dropping a block in the UI.

## AI summary (optional)

Pick a connected model in the “AI summary” section and click “Generate summary”; it writes an evaluation summary **organised by capability
category** in the UI language.

**Core design: Python computes, the AI only explains.** Every statistic (rankings per benchmark, coverage, whether a gap is significant, data
problems) is computed in code and handed to the model, which only puts it into words. Even a weak model therefore cannot get the numbers wrong.

- **The top level is capability category**, not a per-model dump of benchmarks — that is what makes “which model is strong on which kind of
  benchmark” visible
- **A narrow definition of strong/weak**: strong means “rank 1 **and** at least 2 comparable models **and** a significant lead”; weak means last
  place with a significant gap. A gap below significance is only “no clear advantage”, never “clearly stronger”
- **Number reconciliation**: every number in the summary is looked up in the input data; anything not found is flagged for you to check
- **Data problems are for the terminal only**: a cell below the random baseline (a hint of an incompatible API), a best score coming from a
  partial run, and so on are **our own quality signals** and are deliberately kept off the page (the summary is meant to be shareable).
  Run `python scripts/verify_summary.py` to see them
- Regenerating with the same model **overwrites** the entry; different models each keep one, so they can be compared
- Generation is cached by a fingerprint of the input matrix: unchanged data does not burn tokens twice

> Generation takes tens of seconds (longer for reasoning models) and the page shows a simulated progress bar; the percentage is an **estimate**
> (the model returns everything at once), so the real elapsed seconds are shown next to it.

## Code execution sandbox

HumanEval and LiveCodeBench need the model's code to **actually run**, so a Docker sandbox is used. Each run gets a throwaway container
(`--rm`) with isolation entirely expressed on `docker run`:

```
--network none                    no network
--memory 256m (512m for contests) memory cap, OOM-killed beyond it
--cpus 0.5                        CPU cap
--pids-limit 64                   fork-bomb protection
--read-only                       read-only root filesystem
--tmpfs /tmp:size=64m             a small writable scratch area
-v <files generated by this run>:<container path>:ro   read-only mount, **never the project directory**
```

The point: the container only sees the few files generated by the current run — no `data/app.db`, no `data/models.json` (API keys), no other
host path — and with no network there is no way to leak data.

**Requirements**: Docker Desktop installed and **running**. Selecting a code benchmark shows the sandbox status, and if it is unavailable the
reason is stated up front (engine not running / image missing, with the `docker pull` command) rather than failing halfway.

To confirm the isolation really holds, run the attack checks (they really try to read host files, reach the network, write system files,
fork-bomb, loop forever and exhaust memory — all should be blocked):

```bash
python scripts/verify_all.py            # every self-check in one go (recommended)
```

Or individually:

```bash
python scripts/verify_dispatch.py       # dispatch across 6 question types + the full path (mock model, no API cost)
python scripts/verify_imports.py        # static check: undefined names / broken relative imports
python scripts/verify_datasets.py       # datasets and metadata: line cache / atomic writes / card copy fits
python scripts/verify_i18n.py           # language detection / fallback / missing translations
python scripts/verify_models.py         # model roles: a judge cannot be evaluated / role change confirmation (temp DB)
python scripts/verify_safety.py         # safety: verdict direction / judge constraints / scoring failures (free)
python scripts/calibrate_judge.py --dry-run   # see what a judge calibration would cost before running it
python scripts/verify_scoring.py        # scoring policy: complete vs partial runs, consistent in all three places (temp DB)
python scripts/verify_code_assembly.py  # code assembly: helper functions / contest items (no Docker)
python scripts/verify_sandbox.py        # 8 isolation checks + 3 scoring-path checks
python scripts/verify_humaneval.py      # code extraction (8) + 164 official reference solutions
python scripts/verify_livecodebench.py  # scorer (10 cases: two item shapes + four failure modes)
python scripts/verify_summary.py        # statistics layer: guards / significance / reconciliation with the database
python scripts/verify_summaries.py      # generated AI summaries vs the statistical facts
node   scripts/verify_web_templates.mjs # frontend templates: partial-run marking and best-score highlighting (no browser)
```

Only `verify_dispatch` / `verify_sandbox` / `verify_humaneval` / `verify_livecodebench` need Docker; `verify_web_templates.mjs` needs Node
(the rest is plain Python). `verify_dispatch.py` / `verify_scoring.py` / `verify_models.py` use a **temporary database** and never write to
`data/app.db`, so they can be run repeatedly (`verify_models.py` also redirects `models.json` to a temp directory so your real key file is safe).

## Known limitations

- **Safety / alignment scores come from a judge model, not from code**: this is the one place where the project does not follow “Python computes,
  the AI only explains”. Three things to know: (1) **changing the judge makes scores incomparable** (the run list shows which judge produced each
  result); (2) this project only sends **direct requests**, while the official HarmBench board reports ASR *after 18 attack methods* and the JBB
  board uses a Llama-3-70B judge, so **do not compare against the official boards directly**; (3) the official contextual (two-turn) and copyright
  (hash-matched) subsets are not included. Before switching judges, run `scripts/calibrate_judge.py` (measures agreement against the official
  human labels; `--dry-run` shows the cost first).
- **The judge may refuse to answer**: a strongly aligned model often declines to score harmful content, and that item is recorded as a
  **scoring failure** (not counted in the denominator; the `failed` column shows it) — it is **never silently treated as “safe”**, which would
  systematically understate jailbreak rates. If every item fails, the run is flagged red and suggests another judge.
- **A model that returns no visible content is also a failure, not “safe”**: for reasoning models (DeepSeek V4 and friends) the reasoning tokens
  count against `max_tokens`, so too small a budget leaves the visible content empty — and an empty reply reads to the judge as “nothing harmful”,
  i.e. 100% safe. This actually happened (20/20 empty, 100% shown). Safety benchmarks therefore have a **floor of 1024** on `max_tokens`, and
  “empty content / only a truncation marker” is recorded as a scoring failure. Judge quota is saved by **truncating the content handed to the
  judge** (first 2000 characters) rather than by squeezing the model under test.
- **The provider may block harmful requests at its own gateway**: in one 100-item run a provider returned
  `400 InternalError.Algo.Data...` (looks like content moderation) and the request never reached the model. Such items are recorded as
  **scoring failures** (neither a refusal nor a jailbreak) — conservative but honest, since we cannot know what the model would have done.
  Scores should therefore be read as “**on the items the provider allowed to run**”, and the details show exactly which items those were.
- **Safety scores look high by design**: this tool sends **direct requests** (no attack injection), and aligned models mostly refuse outright,
  so “accuracy” on benchmarks like HarmBench is often 80–100%. That is exactly why the official evaluation uses 18 attack methods; telling models
  apart requires attack injection, which is not implemented yet.
- **BFCL multi_turn is a simplification**: tools are not really executed (no filesystem or API state); the initial environment state is described
  to the model and execution feedback is simulated neutrally. It therefore only judges “which function each turn should call” and **does not verify
  the final environment state**, and it is strict: every turn must be right for the item to pass.
- **BFCL covers only 60% of the official weight**: the official total is Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%;
  this project provides the latter four groups (16 subsets) and **misses the Agentic 40%** (Web Search + Memory) — the first needs a paid SerpAPI
  service and the second a memory preprocessing plus embedding-model step, both at odds with “local and reproducible”. The “official weighted score”
  on the page is therefore **renormalised over the groups that ran**, every row states its weight coverage, and it should not be compared directly
  with the official Overall. `live_relevance` is not included either (officially unscored, different rubric).
- **A few Live subsets are tiny**: `live_parallel` has 16 items and `live_parallel_multiple` 24, so one item is worth 4–6 points — read them for
  trends, not for ranking (the small-sample hint and Wilson interval still apply).
- **WinoGrande is 2-way, so guessing scores 50%**: its score is only meaningful against that random baseline (the 60% vs 90% gap matters far more
  than in a four-way task). The official test split has **no public answers**, so the validation split is used (1,267 items); the same applies to
  HellaSwag (10,042 items).
- **ARC-Challenge options are not all four-way**: in the official data 22 items label their options 1/2/3/4 and 3 items offer five options (A–E);
  the downloader aligns answer letters by the **index** of `choices.label`, and the page shows A–D or A–E. If the data source ever changes, this
  step must be re-checked (assuming “labels are ABCD” would get those 22 items wrong while lowering the score only slightly — impossible to spot).
- **HellaSwag context is a situation prefix and the options are four continuations**: assembled exactly the official way (raw `ctx`, no rewriting),
  so the model must read it as “pick the most plausible continuation”. The distractors are fluent but nonsensical, so a low score usually means
  commonsense judgement is off.
- **MATH-500 scoring is approximate**: answers are often LaTeX expressions and only “normalised string comparison plus numeric tolerance” is done,
  so complex expressions with different formatting can be misjudged.
- **Watch `max_tokens` with reasoning models**: DeepSeek V4 and similar think by default and the reasoning tokens count against the output budget;
  use ≥4096 for hard items or the final answer may be truncated. Code benchmarks automatically raise the floor to 8192 and, when truncated,
  ask again with 8192 → 32768 → 131072.
- **A few HumanEval items are internally inconsistent**: HumanEval/47's docstring example (`median([-10,4,6,1000,10,20]) = 15.0`) contradicts the
  official test (which asserts `== 8.0`), so following the docstring fails.
- **LiveCodeBench ships v6 only**: 175 items, all new in 2025, so almost certainly unseen in training data. 6s per case and a 90s per-item budget;
  cases beyond that count as failures.
- **LiveCodeBench scoring follows the official implementation**: stdin style compares line count, then line strings, then per-token `Decimal`;
  function style compares return values with `==` (treating only the outermost tuple as a list), and the official judge environment's
  `import_string` is reproduced.
- **The sandbox needs Docker Desktop running**: otherwise the evaluation fails before it starts and states why, instead of wasting API calls.
- **The AI summary is written by a model, not judged**: the statistics are computed, the wording is generated. It can be imprecise or miss detail —
  the page shows “data problems found” and “the data behind this summary” next to it for checking, and the summary states which model wrote it
  (with an extra note if that model is itself under evaluation).
- **“Best score” is only decided among complete runs**: when a model has several runs on one benchmark, the best **complete** run (coverage ≥90%)
  wins; partial runs are only shown when no complete run exists, and then they are marked “partial x/y”, excluded from best-score marking and from
  the category average. Leaderboard, overview and AI summary share this one rule (`app/scoring.py`) so they cannot drift — **a high score from a few
  items never reaches the board**; keeping a result means running the full dataset.
- **A family counts as one item in the category average**: BFCL's 16 subsets are not 16 votes (which would turn the Agent category into “BFCL
  internals”); the **official weighted score** counts once, and ordering uses “effective coverage” (a standalone benchmark counts 1.0, a family is
  scaled by its official weight coverage), so “one subset only” ranks below “all four groups” — the same coverage-first policy as elsewhere.
- **`.vendor/` is only needed when re-downloading datasets that require parquet parsing (AIME, for example)**: about 82MB unpacked, gitignored,
  and it heals itself if deleted (the next download reinstalls it).

## Security notes

- **API keys stay local** in `data/app.db` and `data/models.json`; nothing is uploaded anywhere
- The `data/` directory is excluded by `.gitignore` (only two harmless samples are kept), so **pushing code never carries your keys**
- Keys returned by the API are always masked

## License

For personal study and evaluation. The benchmark datasets belong to their original authors; check their licences before commercial use.
