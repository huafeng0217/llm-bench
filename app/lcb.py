"""LiveCodeBench 专属逻辑：测试用例的安全解码。

官方把私有测试用例压成 ``base64(zlib(pickle(json字符串)))`` 存进一个字段里，
这里负责把它解回来。
"""
import base64
import io
import json
import pickle
import zlib


class _SafeUnpickler(pickle.Unpickler):
    """受限反序列化器：禁掉一切全局对象查找。

    为什么要多此一举：官方加载器（lcb_runner 与 HF 的 code_generation_lite.py）
    用的是裸 ``pickle.loads``，而 **pickle 反序列化会在本进程里执行任意代码** ——
    ``__reduce__`` 就能构造出 ``os.system(...)``。这份数据来自公网数据集，
    一旦被替换成恶意内容，裸反序列化等于把服务器交出去。

    实测官方数据里 pickle 出来的只是一个 str / list / dict，压根不需要任何全局对象，
    所以禁掉 find_class 完全不影响正常加载。
    """

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"拒绝反序列化全局对象 {module}.{name}")


def decode_tests(encoded) -> list:
    """把 LCB 的 private_test_cases 字段解成 list[dict]。

    返回形如 ``[{"input": "...", "output": "...", "testtype": "stdin"}, ...]``。
    数据损坏或含全局对象时抛异常，由调用方决定如何处理。
    """
    if not encoded:
        return []
    raw = zlib.decompress(base64.b64decode(encoded.encode("utf-8")))
    obj = _SafeUnpickler(io.BytesIO(raw)).load()
    if isinstance(obj, str):  # 官方常见形态：pickle 出来是个 JSON 字符串
        obj = json.loads(obj)
    return obj or []


def all_tests(item: dict) -> list:
    """公开用例 + 私有用例（公开的很小，已直接存成列表；私有的保持官方编码）。"""
    tests = list(item.get("public_tests") or [])
    tests.extend(decode_tests(item.get("private_tests")))
    return tests
