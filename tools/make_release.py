# tools/make_release.py
"""產生要上傳到 GitHub Release 的更新包與校驗檔。

用法（在專案根目錄）：
    python tools/make_release.py                       # 產生 dist/yt-dlp-Python-UI-v<版本>.zip 與 .zip.sha256
    python tools/make_release.py --remove modules/old.py   # 這版要刪掉的舊檔（可重複）
    python tools/make_release.py --requires-new-exe        # 這版必須換新的 exe：使用者端只會引導手動下載

版本號取自 modules/version.py；CHANGELOG.md 最上方必須是同版本的標題。
產生後會用使用者端同一套驗證程式（modules/selfupdate.py）檢查一次，確認客戶端一定收得下。
發佈步驟：在 GitHub 打上 tag（v<版本>）→ 建立 Release → 上傳 dist/ 裡的兩個檔案。
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
SKIP_DIRS = {"__pycache__"}


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


def collect_files(base_dir):
    """更新包要放的檔案（相對路徑，POSIX 斜線）；不在白名單內的檔案一律不收。"""
    found = []
    for top in ("modules", "web", "locales"):
        for folder, dirs, names in os.walk(os.path.join(base_dir, top)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(folder, name), base_dir).replace(os.sep, "/")
                if selfupdate.normalize_path(rel) and selfupdate.is_allowed(rel):
                    found.append(rel)
    for name in selfupdate.ALLOWED_ROOT_FILES:
        if os.path.isfile(os.path.join(base_dir, name)):
            found.append(name)
    return sorted(found)


def build_release(base_dir, out_dir, remove=(), requires_new_exe=False):
    """回傳 (zip 路徑, 校驗檔路徑, manifest)。"""
    version = read_version(base_dir)
    check_changelog(base_dir, version)
    files = collect_files(base_dir)
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
    parser.add_argument("--requires-new-exe", action="store_true", help="這版必須換新的 exe")
    args = parser.parse_args(argv)
    try:
        zip_path, sha_path, manifest = build_release(ROOT, args.out, args.remove, args.requires_new_exe)
    except (ReleaseError, selfupdate.UpdateError, OSError) as exc:
        print(f"失敗：{exc}", file=sys.stderr)
        return 1
    print(f"版本 v{manifest['version']}：{len(manifest['files'])} 個檔案，刪除 {len(manifest['remove'])} 個"
          f"{'，需要新 exe' if manifest['requires_new_exe'] else ''}")
    print(f"  {zip_path}  ({os.path.getsize(zip_path) / 1024:.0f} KB)")
    print(f"  {sha_path}")
    print(f"請在 GitHub 打上 tag v{manifest['version']}、建立 Release，並上傳這兩個檔案。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
