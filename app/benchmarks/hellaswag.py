"""HellaSwag：给一段情境，选出最合理的后续，4 选 1。

常识推理的经典基准（2019）：干扰项由对抗式方法生成 —— 语法通顺、看着像，
但常识上不对。所以它测的不是语言流畅度，而是「这段话接下来合不合理」。
"""
from .types import Benchmark
import sys
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl


# 四个选项：显式写出来，别用 _util.LETTERS（那是 A~D 的巧合，语义上不是「本基准的选项」）
CHOICES = ["A", "B", "C", "D"]


DS = "Rowan/hellaswag"
CONFIG = "default"
SPLIT = "validation"    # test 划分的 label 官方是空的（答案未公开）


def map_hellaswag(row: dict):
    """一行 HellaSwag → 统一题目格式：题干是 ``ctx``（情境前缀），四个选项是四种后续。"""
    ctx = str(row.get("ctx", "")).strip()
    endings = [str(e).strip() for e in (row.get("endings") or [])]
    label = str(row.get("label", "")).strip()
    if not ctx or len(endings) != 4 or label not in {"0", "1", "2", "3"}:
        return None
    if not all(endings):
        return None
    item = {"question": ctx, "answer": CHOICES[int(label)],
            "subject": str(row.get("activity_label", ""))}
    for i, e in enumerate(endings):
        item[CHOICES[i]] = e
    return item


def download_hellaswag():
    path = DATA_DIR / "hellaswag.jsonl"
    rows = fetch_ds_rows(DS, CONFIG, SPLIT)
    items, failed = [], 0
    for row in rows:
        item = map_hellaswag(row)
        if not item:
            failed += 1
            print(f"  解析失败，跳过: {row.get('ind')}", file=sys.stderr)
            continue
        items.append(item)
    write_jsonl(path, items)
    print(f"完成：{path}（共 {len(items)} 题" + (f"，解析失败 {failed}" if failed else "") + "）")


ENTRIES = [
    Benchmark(order=29, id='hellaswag',
        name='HellaSwag',
        summary='给一段情境选最合理的后续：干扰项语法通顺但常识上不对',
        category='常识推理',
        lang='英文',
        status='已饱和',
        description='10042 道常识续写题：题干是情境前缀（如「一个人坐在屋顶上。他…」），'
                    '四个候选后续里只有一个是常识上合理的，其余三个由对抗式方法生成 —— 读起来通顺、'
                    '但明显不合常理。因此它测的是「常识判断」而不是语言流畅度。前沿模型已超过 95%，'
                    '主要价值是确认模型没有基本的常识缺陷（低分通常意味着接口或抽取有问题，而不是「不懂常识」）。'
                    '注意：题干与选项按官方口径拼（题干用原始 ctx，选项就是四个后续），没有额外改写。',
        source='https://arxiv.org/abs/1905.07830',
        label='HellaSwag（10042 题）',
        download=download_hellaswag),
]
