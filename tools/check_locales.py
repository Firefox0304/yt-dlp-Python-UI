# tools/check_locales.py
"""檢查 locales/<語言>/*.json：檔案組成、缺字、多字、重複、放錯檔案、占位符不一致，
以及程式用到但沒定義的文字代號。

用法（在專案根目錄）：
    python tools/check_locales.py

有「錯誤」時結束代碼為 1；「警告」（定義了卻沒用到的代號）不影響結束代碼。
基準語言是 zh-TW：其他語言必須有一模一樣的檔案與 key，且每個 key 的 {占位符} 要相同。
檔案分工：errors.json 只放 err.*、logs.json 只放 log.*，其餘文字放在 strings.json（或其他檔案）。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "zh-TW"
LANGS = ("zh-TW", "zh-CN", "en", "ja")
# 指定檔案只能放這些前綴的 key；其他檔案則不能放這些前綴的 key
FILE_PREFIXES = {"errors.json": ("err.",), "logs.json": ("log.",)}

# 掃描哪些檔案裡的 t("...") 與 data-i18n*="..."
SOURCE_GLOBS = (("modules", (".py",)), ("web", (".js", ".html")), (".", (".pyw",)))

_KEY_RE = re.compile(r"^[a-z][\w]*(?:\.[\w]+)+\.?$")  # 例如 dl.msg.ffmpeg；結尾是點表示「前綴」
_CALL_RE = re.compile(r"""\bt\(\s*["']([\w.]+)["']""")
_ATTR_RE = re.compile(r"""data-i18n(?:-\w+)?=["']([\w.]+)["']""")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _iter_sources(root):
    for folder, exts in SOURCE_GLOBS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.isfile(path) and name.endswith(exts):
                yield path


def load_locales(root=ROOT):
    """回傳 ({語言: {key: 文字}}, {語言: {檔名: [key, ...]}}, errors)。同一語言的多個檔案會合併。"""
    data, layout, errors = {}, {}, []
    for lang in LANGS:
        folder = os.path.join(root, "locales", lang)
        try:
            names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".json"))
        except OSError as exc:
            errors.append(f"[{lang}] 讀不到語言資料夾 locales/{lang}/：{exc}")
            continue
        if not names:
            errors.append(f"[{lang}] locales/{lang}/ 裡沒有任何 .json 檔")
            continue
        merged, files, owner = {}, {}, {}
        for name in names:
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    loaded = json.load(f)
            except (OSError, ValueError) as exc:
                errors.append(f"[{lang}/{name}] 讀不到或不是有效的 JSON：{exc}")
                continue
            if not isinstance(loaded, dict) or not all(isinstance(v, str) for v in loaded.values()):
                errors.append(f"[{lang}/{name}] 內容必須是「key → 字串」的物件")
                continue
            files[name] = list(loaded)
            for key, value in loaded.items():
                if key in owner:
                    errors.append(f"[{lang}] {key} 同時出現在 {owner[key]} 和 {name}")
                owner[key] = name
                merged[key] = value
        data[lang], layout[lang] = merged, files
    return data, layout, errors


def _misfiled(layout, lang):
    """key 放錯檔案的清單（只檢查有定義規則的前綴）。"""
    problems = []
    for name, keys in layout.get(lang, {}).items():
        allowed = FILE_PREFIXES.get(name)
        for key in keys:
            if allowed is not None and not key.startswith(allowed):
                problems.append(f"[{lang}/{name}] {key} 不該放在這個檔案（只能放 {', '.join(allowed)} 開頭的 key）")
            elif allowed is None and key.startswith(tuple(p for ps in FILE_PREFIXES.values() for p in ps)):
                dest = next(f for f, ps in FILE_PREFIXES.items() if key.startswith(ps))
                problems.append(f"[{lang}/{name}] {key} 應該放在 {dest}")
    return problems


def find_used_keys(root=ROOT):
    """回傳 (完整的代號集合, 動態前綴集合, 所有原始碼合併文字)。

    t("dl.msg." + code) 這類寫法只認得前綴；合併文字讓「程式裡有沒有出現這個字串」可以另外查
    （例如代號放在 COMMAND_NOTES 這種表裡，再由別處呼叫 t(...)）。
    """
    keys, prefixes, chunks = set(), set(), []
    for path in _iter_sources(root):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        chunks.append(text)
        for key in _CALL_RE.findall(text) + _ATTR_RE.findall(text):
            if not _KEY_RE.match(key):
                continue  # 例如說明文字裡的 t("代號")
            (prefixes if key.endswith(".") else keys).add(key)
    return keys, prefixes, "\n".join(chunks)


def run(root=ROOT):
    """回傳 (errors, warnings)，都是字串清單。"""
    data, layout, errors = load_locales(root)
    warnings = []
    base = data.get(BASE)
    if base is None:
        return errors, warnings

    base_files = sorted(layout.get(BASE, {}))
    for lang in layout:
        errors.extend(_misfiled(layout, lang))
        if lang != BASE and sorted(layout[lang]) != base_files:
            errors.append(f"[{lang}] 檔案組成和 {BASE} 不同：{sorted(layout[lang])}（{BASE} 是 {base_files}）")
        elif lang != BASE:  # 檔案組成相同時，連每個 key 放在哪個檔案也要一樣
            for name in base_files:
                if set(layout[lang][name]) != set(layout[BASE][name]):
                    errors.append(f"[{lang}/{name}] 和 {BASE}/{name} 收的 key 不同")

    for lang, table in data.items():
        for key, value in table.items():
            if not value.strip():
                errors.append(f"[{lang}] {key} 是空字串")
        if lang == BASE:
            continue
        missing = sorted(set(base) - set(table))
        extra = sorted(set(table) - set(base))
        if missing:
            errors.append(f"[{lang}] 缺少 {len(missing)} 個 key：{', '.join(missing)}")
        if extra:
            errors.append(f"[{lang}] 多出 {len(extra)} 個 {BASE} 沒有的 key：{', '.join(extra)}")
        for key in sorted(set(base) & set(table)):
            want, got = set(_PLACEHOLDER_RE.findall(base[key])), set(_PLACEHOLDER_RE.findall(table[key]))
            if want != got:
                errors.append(f"[{lang}] {key} 的占位符不一致：{BASE} 是 {sorted(want)}，這裡是 {sorted(got)}")

    used, prefixes, source_text = find_used_keys(root)
    undefined = sorted(k for k in used if k not in base)
    if undefined:
        errors.append(f"程式用到但 {BASE} 沒有定義的代號：{', '.join(undefined)}")

    def mentioned(key):  # 原始碼任何地方出現 "key" 或 'key' 就算有用到
        return f'"{key}"' in source_text or f"'{key}'" in source_text

    unused = sorted(k for k in base
                    if k not in used and not any(k.startswith(p) for p in prefixes) and not mentioned(k))
    if unused:
        warnings.append(f"{BASE} 定義了但程式裡找不到用法的代號（{len(unused)} 個，可能是動態組出來的）：{', '.join(unused)}")
    return errors, warnings


def main():
    errors, warnings = run()
    for line in warnings:
        print("警告：" + line)
    for line in errors:
        print("錯誤：" + line)
    if errors:
        print(f"\n檢查失敗：{len(errors)} 個錯誤。")
        return 1
    print("語言檔檢查通過。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
