# tools/make_release.py
"""產生要上傳到 GitHub Release 的更新包與校驗檔。

用法（在專案根目錄）：
    python tools/make_release.py                           # 產生 dist/yt-dlp-Python-UI-v<版本>.zip 與 .zip.sha256
    python tools/make_release.py --remove modules/old.py   # 這版要刪掉的舊檔（可重複）
    python tools/make_release.py --exclude icon.ico        # 額外排除某個檔案或資料夾（可重複）
    python tools/make_release.py --requires-new-exe        # 這版必須換新的 exe：使用者端只會引導手動下載

打包內容是「專案裡除了入口與使用者資料以外的所有東西」：
  不打包：.git、config、Download、example、logs、backup、dist、build 等資料夾，
          根目錄的 .exe／.pyw（入口、yt-dlp.exe、ffmpeg.exe）、.zip／.sha256（更新包本身）、
          根目錄的 manifest.json（更新包的清單檔，把更新包解壓到專案資料夾時會留下）、__pycache__、*.pyc
  其餘全部打包（modules、web、locales、fonts、tests、tools、README.md、CHANGELOG.md、requirements.txt、.gitignore…）
如果有檔案不在使用者端的白名單內（modules/selfupdate.py 的 ALLOWED_*），會直接報錯並列出來，
請用 --exclude 排除，或把它加進白名單；不會默默漏掉。

版本號取自 modules/version.py；CHANGELOG.md 最上方必須是同版本的標題。
產生後會用使用者端同一套驗證程式（modules/selfupdate.py）檢查一次，確認客戶端一定收得下。
發佈步驟：在 GitHub 打上 tag（v<版本>）→ 建立 Release → 上傳 dist/ 裡的兩個檔案（檔名不能改）。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import selfupdate  # noqa: E402

FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # 固定時間戳：同樣的內容永遠產生同樣的 zip（雜湊才能重現）

# 根目錄底下不打包的資料夾／檔案（完全相符的名稱）
DEFAULT_EXCLUDES = (
    ".git", ".github", ".vscode", ".idea", "build", "dist", "venv", ".venv",   # 開發與建置用
    "config", "Download", "example", "logs", "backup", "Python Download",       # 執行時自動產生的使用者資料
)
ROOT_SKIP_SUFFIXES = (".exe", ".pyw", ".zip", ".sha256")  # 根目錄的入口（exe／.pyw）、yt-dlp.exe、ffmpeg.exe、更新包本身：不在更新範圍
ROOT_SKIP_NAMES = (selfupdate.MANIFEST_NAME,)  # manifest.json 是更新包自己的清單檔，不是程式的一部分（只忽略根目錄的）
SKIP_DIR_NAMES = ("__pycache__",)            # 任何位置都不打包
SKIP_FILE_SUFFIXES = (".pyc", ".pyo", ".part", ".ytdl")


class ReleaseError(Exception):
    pass


def read_version(base_dir):
    with open(os.path.join(base_dir, "modules", "version.py"), encoding="utf-8") as f:
        match = re.search(r'^APP_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){1,3})"', f.read(), re.M)
    if not match:
        raise ReleaseError("modules/version.py 找不到 APP_VERSION")
    return match.group(1)


def check_changelog(base_dir, version):
    with open(os.path.join(base_dir, "CHANGELOG.md"), encoding="utf-8") as f:
        first = f.readline().strip()
    if first != f"# v{version}":
        raise ReleaseError(f"CHANGELOG.md 最上方是 {first!r}，應該是 '# v{version}'")


def _is_excluded(rel, extra):
    for item in extra:
        item = item.strip().replace("\\", "/").strip("/")
        if item and (rel == item or rel.startswith(item + "/")):
            return True
    return False


def collect_files(base_dir, exclude=()):
    """回傳 (要打包的檔案, 無法打包的檔案)，都是相對路徑（POSIX 斜線）。

    無法打包 = 沒被排除、但不在使用者端白名單內（例如 modules/ 裡的 .bak、根目錄多出來的 icon.ico）。
    """
    found, rejected = [], []
    for folder, dirs, names in os.walk(base_dir):
        at_root = os.path.abspath(folder) == os.path.abspath(base_dir)
        rel_folder = "" if at_root else os.path.relpath(folder, base_dir).replace(os.sep, "/")
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIR_NAMES and not (at_root and d in DEFAULT_EXCLUDES)
                         and not _is_excluded(f"{rel_folder}/{d}".lstrip("/"), exclude))
        for name in sorted(names):
            rel = f"{rel_folder}/{name}".lstrip("/")
            if name.lower().endswith(SKIP_FILE_SUFFIXES) or _is_excluded(rel, exclude):
                continue
            if at_root and (name in DEFAULT_EXCLUDES or name in ROOT_SKIP_NAMES or name.lower().endswith(ROOT_SKIP_SUFFIXES)):
                continue
            if selfupdate.normalize_path(rel) and selfupdate.is_allowed(rel):
                found.append(rel)
            else:
                rejected.append(rel)
    return sorted(found), sorted(rejected)


def build_release(base_dir, out_dir, remove=(), requires_new_exe=False, exclude=()):
    """回傳 (zip 路徑, 校驗檔路徑, manifest)。"""
    version = read_version(base_dir)
    check_changelog(base_dir, version)
    files, rejected = collect_files(base_dir, exclude)
    if rejected:
        shown = "\n  ".join(rejected[:15]) + (f"\n  …還有 {len(rejected) - 15} 個" if len(rejected) > 15 else "")
        raise ReleaseError("這些檔案不能放進更新包（不在使用者端的白名單內）：\n  " + shown +
                           "\n請用 --exclude <路徑> 排除，或把它加進 modules/selfupdate.py 的白名單。")
    if not files:
        raise ReleaseError("沒有找到可以打包的檔案")
    remove = [selfupdate.checked_path(r) for r in remove]
    overlap = sorted(set(remove) & set(files))
    if overlap:
        raise ReleaseError(f"{overlap[0]} 同時要更新與刪除")

    entries = {}
    for rel in files:
        with open(os.path.join(base_dir, *rel.split("/")), "rb") as f:
            entries[rel] = f.read()
    manifest = {"format": selfupdate.MANIFEST_FORMAT, "name": selfupdate.PACKAGE_NAME, "version": version,
                "requires_new_exe": bool(requires_new_exe),
                "files": {rel: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                          for rel, data in entries.items()},
                "remove": sorted(remove)}

    os.makedirs(out_dir, exist_ok=True)
    zip_name = f"{selfupdate.ASSET_PREFIX}{version}.zip"
    zip_path = os.path.join(out_dir, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in [(selfupdate.MANIFEST_NAME,
                           json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))] + \
                         sorted(entries.items()):
            info = zipfile.ZipInfo(rel, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    digest = selfupdate.sha256_file(zip_path)
    sha_path = zip_path + ".sha256"
    with open(sha_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{digest}  {zip_name}\n")

    # 用使用者端同一套驗證程式檢查產出的更新包：過不了就不要上傳
    if os.path.getsize(zip_path) > selfupdate.MAX_ZIP_BYTES:
        raise ReleaseError("更新包超過使用者端的大小上限")
    with tempfile.TemporaryDirectory() as staging:
        selfupdate.extract_verified(zip_path, staging, version)
    return zip_path, sha_path, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="產生 GitHub Release 用的更新包")
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"), help="輸出資料夾（預設 dist/）")
    parser.add_argument("--remove", action="append", default=[], metavar="路徑", help="這版要刪掉的舊檔，可重複")
    parser.add_argument("--exclude", action="append", default=[], metavar="路徑", help="額外排除的檔案或資料夾，可重複")
    parser.add_argument("--requires-new-exe", action="store_true", help="這版必須換新的 exe")
    args = parser.parse_args(argv)
    try:
        zip_path, sha_path, manifest = build_release(ROOT, args.out, args.remove, args.requires_new_exe, args.exclude)
    except (ReleaseError, selfupdate.UpdateError, OSError) as exc:
        print(f"失敗：{exc}", file=sys.stderr)
        return 1
    total = sum(m["size"] for m in manifest["files"].values())
    print(f"版本 v{manifest['version']}：{len(manifest['files'])} 個檔案（{total / 1024 / 1024:.1f} MB），"
          f"刪除 {len(manifest['remove'])} 個{'，需要新 exe' if manifest['requires_new_exe'] else ''}")
    print(f"  {zip_path}  ({os.path.getsize(zip_path) / 1024 / 1024:.1f} MB)")
    print(f"  {sha_path}")
    print(f"請在 GitHub 打上 tag v{manifest['version']}、建立 Release，並上傳這兩個檔案。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
