# modules/selfupdate.py
"""程式本身（UI）的自動更新：從 GitHub Release 下載更新包，驗證後覆蓋外部檔案。

只更新 exe 旁邊的外部檔案（modules／web／locales／CHANGELOG.md／README.md）；入口（exe 或 .pyw）不在範圍內。
每一步失敗都會停下，在「套用」之前完全不會動到現有檔案：

1. fetch_release   查 releases/latest，找 yt-dlp-Python-UI-vX.Y.Z.zip 與 .sha256 兩個附件
2. download_file   只准 https、白名單網域（每次轉址都檢查）、有大小上限
3. 比對 SHA-256 → extract_verified：逐檔檢查路徑與白名單、對照 manifest.json 的雜湊，解到暫存資料夾
4. apply_update    先備份舊檔，再逐檔替換；任何一步失敗就從備份還原

更新包由 tools/make_release.py 產生。manifest 標示 requires_new_exe 的版本只引導使用者手動下載新 exe。
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter

from modules import updater

RELEASE_API_URL = "https://api.github.com/repos/Firefox0304/yt-dlp-Python-UI/releases/latest"
ASSET_PREFIX = "yt-dlp-Python-UI-v"          # 更新包：yt-dlp-Python-UI-v7.2.0.zip，校驗檔：同名加 .sha256
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
PACKAGE_NAME = "yt-dlp-Python-UI"

MAX_API_BYTES = 2 * 1024 * 1024
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_UNPACKED_BYTES = 60 * 1024 * 1024
MAX_FILES = 400
MAX_MANIFEST_BYTES = 1024 * 1024
NOTES_LIMIT = 1500
KEEP_BACKUPS = 2

ALLOWED_DIRS = ("modules/", "web/", "locales/")
ALLOWED_ROOT_FILES = ("CHANGELOG.md", "README.md")
ALLOWED_SUFFIXES = (".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".svg", ".png", ".ico")

_REPO_PATH = urllib.parse.urlparse(updater.PROJECT_URL).path          # /Firefox0304/yt-dlp-Python-UI
_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BACKUP_NAME = re.compile(r"^v[0-9.]+-(\d{8})-(\d{6})$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

# 每個代碼在 locales/*/strings.json 都要有 selfupdate.err.<代碼>（tests/test_selfupdate.py 會檢查）
ERROR_CODES = ("busy", "network", "no_release", "no_asset", "too_big", "bad_checksum", "bad_manifest", "bad_zip",
               "unsafe_path", "requires_exe", "not_newer", "apply_failed", "rollback_failed", "restart_failed",
               "no_pending", "unexpected")


class UpdateError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)


# --------------------------------------------------------------------------- #
# 路徑與清單驗證
# --------------------------------------------------------------------------- #

def normalize_path(name):
    """zip 內的名稱 → 安全的 POSIX 相對路徑；任何可疑的寫法回傳 None。"""
    if not isinstance(name, str) or not name or len(name) > 200:
        return None
    if name.startswith("/") or "\\" in name or ":" in name:
        return None
    parts = name.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith(".") or not _SEGMENT.match(part):
            return None  # 空段、.、..、結尾是點（Windows 會吃掉）、或有 ASCII 以外／特殊字元
        if part.split(".")[0].upper() in _RESERVED:
            return None  # CON、NUL、COM1… 這類 Windows 裝置名稱
    return "/".join(parts)


def is_allowed(rel):
    """只准更新白名單內的資料夾與檔案類型（不收 exe、dll、bat 等可執行檔）。"""
    if "__pycache__" in rel.split("/"):
        return False
    if os.path.splitext(rel)[1].lower() not in ALLOWED_SUFFIXES:
        return False
    return rel in ALLOWED_ROOT_FILES or rel.startswith(ALLOWED_DIRS)


def checked_path(name):
    rel = normalize_path(name)
    if rel is None or not is_allowed(rel):
        raise UpdateError("unsafe_path", str(name)[:80])
    return rel


def parse_manifest(raw, expected_version):
    """驗證 manifest.json（bytes），回傳 {"version", "files": {路徑: {sha256, size}}, "remove": [路徑], "requires_new_exe"}。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError("bad_manifest", exc) from exc
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT or data.get("name") != PACKAGE_NAME:
        raise UpdateError("bad_manifest", "format/name")
    if data.get("version") != expected_version:
        raise UpdateError("bad_manifest", f"version {data.get('version')!r} != {expected_version!r}")
    requires = data.get("requires_new_exe", False)
    if not isinstance(requires, bool):
        raise UpdateError("bad_manifest", "requires_new_exe")
    files = data.get("files")
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise UpdateError("bad_manifest", "files")
    clean, lowered = {}, set()
    for name, meta in files.items():
        rel = checked_path(name)
        sha = meta.get("sha256") if isinstance(meta, dict) else None
        size = meta.get("size") if isinstance(meta, dict) else None
        if not isinstance(sha, str) or not _HEX64.match(sha.lower()):
            raise UpdateError("bad_manifest", f"sha256 of {rel}")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_BYTES:
            raise UpdateError("bad_manifest", f"size of {rel}")
        if rel.lower() in lowered:  # Windows 不分大小寫：兩個只差大小寫的檔名會互相覆蓋
            raise UpdateError("bad_manifest", f"duplicate {rel}")
        lowered.add(rel.lower())
        clean[rel] = {"sha256": sha.lower(), "size": size}
    remove = data.get("remove", [])
    if not isinstance(remove, list) or len(remove) > MAX_FILES:
        raise UpdateError("bad_manifest", "remove")
    removal = []
    for name in remove:
        rel = checked_path(name)
        if rel in clean:
            raise UpdateError("bad_manifest", f"{rel} is both updated and removed")
        if rel not in removal:
            removal.append(rel)
    return {"version": expected_version, "files": clean, "remove": removal, "requires_new_exe": requires}


# --------------------------------------------------------------------------- #
# 網路：查 Release、下載
# --------------------------------------------------------------------------- #

def check_download_url(url, initial=False):
    """只准 https 與白名單網域；附件的第一個網址必須在本專案的 releases/download/ 底下。"""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or (parsed.hostname or "") not in _DOWNLOAD_HOSTS or parsed.username or parsed.password:
        raise UpdateError("network", "blocked url")
    if initial and (parsed.hostname != "github.com" or not parsed.path.startswith(_REPO_PATH + "/releases/download/")):
        raise UpdateError("network", "blocked url")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_download_url(newurl)  # 轉址到白名單以外的地方就中止
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect())


def _open(url, timeout=15, accept="application/octet-stream"):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-dlp-python-UI", "Accept": accept})
    return _OPENER.open(req, timeout=timeout)


def _read_limited(resp, limit):
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("too_big")
    return data


def fetch_release(open_url=None):
    """查 releases/latest，回傳 {"version", "notes", "zip_name", "zip_url", "sha_url", "zip_size"}。"""
    open_url = open_url or _open
    try:
        with open_url(RELEASE_API_URL, accept="application/vnd.github+json") as resp:
            data = json.loads(_read_limited(resp, MAX_API_BYTES).decode("utf-8"))
    except UpdateError:
        raise
    except urllib.error.HTTPError as exc:
        raise UpdateError("no_release" if exc.code == 404 else "network", f"HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise UpdateError("network", exc) from exc
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise UpdateError("no_release")
    version = updater.parse_ui_tag(data.get("tag_name"))
    if not version:
        raise UpdateError("no_release", "tag")
    zip_name = f"{ASSET_PREFIX}{version}.zip"
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    if zip_name not in assets or zip_name + ".sha256" not in assets:
        raise UpdateError("no_asset", zip_name)
    zip_url = check_download_url(assets[zip_name].get("browser_download_url"), initial=True)
    sha_url = check_download_url(assets[zip_name + ".sha256"].get("browser_download_url"), initial=True)
    size = assets[zip_name].get("size")
    if isinstance(size, int) and size > MAX_ZIP_BYTES:
        raise UpdateError("too_big")
    return {"version": version, "notes": str(data.get("body") or "")[:NOTES_LIMIT], "zip_name": zip_name,
            "zip_url": zip_url, "sha_url": sha_url, "zip_size": size if isinstance(size, int) else 0}


def download_file(url, dest, max_bytes, on_progress=None, open_url=None):
    """串流下載到 dest；超過 max_bytes 就中止。回傳實際大小。"""
    open_url = open_url or _open
    done = 0
    try:
        with open_url(url) as resp, open(dest, "wb") as out:
            total = int((getattr(resp, "headers", None) or {}).get("Content-Length") or 0)
            if total > max_bytes:
                raise UpdateError("too_big")
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                done += len(chunk)
                if done > max_bytes:
                    raise UpdateError("too_big")
                out.write(chunk)
                if on_progress:
                    on_progress(done, total)
    except UpdateError:
        raise
    except (OSError, ValueError) as exc:
        raise UpdateError("network", exc) from exc
    return done


def parse_sha256_file(text, expected_name):
    """支援 'hash  檔名'、'hash *檔名'，或只有 hash 的內容。"""
    for line in str(text).splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if len(parts) > 1 and parts[-1].lstrip("*") != expected_name:
            continue
        if _HEX64.match(parts[0].lower()):
            return parts[0].lower()
    raise UpdateError("bad_checksum", "checksum file")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 解壓並逐檔驗證（解到暫存資料夾，不碰現有檔案）
# --------------------------------------------------------------------------- #

def _read_member(zf, info, limit):
    if info.flag_bits & 0x1:
        raise UpdateError("bad_zip", "encrypted entry")
    try:
        with zf.open(info) as f:
            data = f.read(limit + 1)  # 以實際讀到的位元組為準，不信任標頭上寫的大小（防壓縮炸彈）
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise UpdateError("bad_zip", exc) from exc
    if len(data) > limit:
        raise UpdateError("too_big")
    return data


def extract_verified(zip_path, staging_dir, expected_version):
    """驗證並解壓；回傳 manifest。zip 裡只能有 manifest.json 與清單內的檔案，雜湊與大小都要相符。"""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError("bad_zip", exc) from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES + 50:
            raise UpdateError("bad_zip", "too many entries")
        dupes = [n for n, c in Counter(i.filename for i in infos).items() if c > 1]
        if dupes:
            raise UpdateError("bad_zip", f"duplicate entry {dupes[0][:80]}")
        manifest_info = next((i for i in infos if i.filename == MANIFEST_NAME), None)
        if manifest_info is None:
            raise UpdateError("bad_manifest", "missing manifest.json")
        manifest = parse_manifest(_read_member(zf, manifest_info, MAX_MANIFEST_BYTES), expected_version)
        seen, total = set(), 0
        for info in infos:
            if info.is_dir() or info.filename == MANIFEST_NAME:
                continue
            rel = checked_path(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UpdateError("unsafe_path", f"symlink {rel}")
            meta = manifest["files"].get(rel)
            if meta is None:
                raise UpdateError("unsafe_path", f"{rel} not in manifest")
            data = _read_member(zf, info, MAX_FILE_BYTES)
            total += len(data)
            if total > MAX_UNPACKED_BYTES:
                raise UpdateError("too_big")
            if len(data) != meta["size"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
                raise UpdateError("bad_checksum", rel)
            target = os.path.join(staging_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as out:
                out.write(data)
            seen.add(rel)
        missing = sorted(set(manifest["files"]) - seen)
        if missing:
            raise UpdateError("bad_manifest", f"zip is missing {missing[0]}")
    return manifest


def cleanup(work_dir):
    if work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)


def prepare_update(current_version, on_progress=None, open_url=None, work_dir=None):
    """查 Release → 下載 → 驗證 → 解到暫存資料夾。回傳 {"version", "notes", "staging", "work_dir", "manifest"}。

    on_progress(stage, pct)：stage 是 checking / downloading / verifying；pct 是 0–100 或 None。
    任何失敗都會清掉暫存資料夾並丟出 UpdateError；需要換新 exe 的版本丟 requires_exe。
    """
    emit = on_progress or (lambda stage, pct: None)
    open_url = open_url or _open
    emit("checking", None)
    release = fetch_release(open_url)
    if not updater.is_newer(release["version"], current_version):
        raise UpdateError("not_newer")
    work = work_dir or tempfile.mkdtemp(prefix="ytdlp-ui-update-")
    try:
        os.makedirs(work, exist_ok=True)
        try:
            with open_url(release["sha_url"]) as resp:
                sha_text = _read_limited(resp, 4096).decode("utf-8", errors="replace")
        except UpdateError:
            raise
        except OSError as exc:
            raise UpdateError("network", exc) from exc
        expected = parse_sha256_file(sha_text, release["zip_name"])
        zip_path = os.path.join(work, release["zip_name"])
        expected_size = release["zip_size"]
        emit("downloading", 0)

        def on_chunk(done, total):
            size = total or expected_size
            emit("downloading", min(100, done * 100 / size) if size else None)

        download_file(release["zip_url"], zip_path, MAX_ZIP_BYTES, on_chunk, open_url)
        emit("verifying", None)
        if sha256_file(zip_path) != expected:
            raise UpdateError("bad_checksum", "zip")
        staging = os.path.join(work, "staging")
        manifest = extract_verified(zip_path, staging, release["version"])
        if manifest["requires_new_exe"]:
            raise UpdateError("requires_exe", release["version"])
    except BaseException:
        cleanup(work)
        raise
    return {"version": release["version"], "notes": release["notes"], "staging": staging, "work_dir": work,
            "manifest": manifest}


# --------------------------------------------------------------------------- #
# 套用：備份 → 暫存檔 → 逐檔替換；失敗就還原
# --------------------------------------------------------------------------- #

def _dest_path(base, rel):
    """rel 對應到程式資料夾內的實際路徑；解析符號連結後跑出資料夾外就拒絕。"""
    checked_path(rel)
    path = os.path.realpath(os.path.join(base, *rel.split("/")))
    try:
        inside = os.path.commonpath([base, path]) == base
    except ValueError:  # 不同磁碟機
        inside = False
    if not inside:
        raise UpdateError("unsafe_path", rel)
    return path


def _rollback(base, backup_dir, backed, replaced, removed, temps):
    for rel in replaced + removed:
        dest = _dest_path(base, rel)
        if rel in backed:
            shutil.copy2(os.path.join(backup_dir, *rel.split("/")), dest)
        elif os.path.isfile(dest):
            os.remove(dest)  # 這是新增的檔案，更新前並不存在
    for tmp in temps:
        if os.path.isfile(tmp):
            os.remove(tmp)


def apply_update(base_dir, staging_dir, manifest, current_version, replace=os.replace):
    """把 staging_dir 裡驗證過的檔案套用到 base_dir。成功回傳 {"backup", "replaced", "removed"}。

    套用前先把會被動到的舊檔備份到 backup/v<舊版>-<時間>/；中途任何錯誤都會從備份還原，
    還原也失敗才丟 rollback_failed（此時請使用者從 backup 資料夾手動還原）。
    """
    base = os.path.realpath(base_dir)
    files, removals = list(manifest["files"]), list(manifest.get("remove", []))
    targets = {rel: _dest_path(base, rel) for rel in files + removals}  # 先全部驗證，不通過就一個檔案都不碰
    backup_dir = os.path.join(base, "backup", f"v{current_version}-{time.strftime('%Y%m%d-%H%M%S')}")
    backed, replaced, removed, temps = set(), [], [], []
    try:
        os.makedirs(backup_dir)
        for rel, dest in targets.items():  # 1. 備份
            if os.path.isfile(dest):
                saved = os.path.join(backup_dir, *rel.split("/"))
                os.makedirs(os.path.dirname(saved), exist_ok=True)
                shutil.copy2(dest, saved)
                backed.add(rel)
        for rel in files:  # 2. 新檔先寫成同資料夾的暫存檔（同一個磁碟，替換才是原子的）
            os.makedirs(os.path.dirname(targets[rel]), exist_ok=True)
            tmp = targets[rel] + ".new"
            temps.append(tmp)
            shutil.copyfile(os.path.join(staging_dir, *rel.split("/")), tmp)
        for rel in files:  # 3. 逐檔替換
            replace(targets[rel] + ".new", targets[rel])
            replaced.append(rel)
        for rel in removals:  # 4. 刪掉這版不要的舊檔
            if os.path.isfile(targets[rel]):
                os.remove(targets[rel])
                removed.append(rel)
    except Exception as exc:  # noqa: BLE001 — 任何錯誤都要走還原
        try:
            _rollback(base, backup_dir, backed, replaced, removed, temps)
        except Exception as rollback_exc:  # noqa: BLE001
            raise UpdateError("rollback_failed", f"{exc}; {rollback_exc}") from exc
        raise UpdateError("apply_failed", exc) from exc
    prune_backups(base_dir)
    return {"backup": backup_dir, "replaced": len(replaced), "removed": len(removed)}


def list_backups(base_dir):
    """backup/ 底下的備份資料夾，由舊到新。"""
    root = os.path.join(base_dir, "backup")
    try:
        names = [n for n in os.listdir(root) if _BACKUP_NAME.match(n) and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return []
    return [os.path.join(root, n) for n in sorted(names, key=lambda n: _BACKUP_NAME.match(n).groups())]


def prune_backups(base_dir, keep=KEEP_BACKUPS):
    for old in list_backups(base_dir)[:-keep] if keep > 0 else list_backups(base_dir):
        shutil.rmtree(old, ignore_errors=True)


def restore_backup(base_dir, backup_dir=None):
    """把備份蓋回程式資料夾（預設用最新的備份）。回傳還原的檔案數；沒有備份回傳 0。"""
    backup_dir = backup_dir or (list_backups(base_dir) or [None])[-1]
    if not backup_dir or not os.path.isdir(backup_dir):
        return 0
    base, count = os.path.realpath(base_dir), 0
    for folder, _dirs, names in os.walk(backup_dir):
        for name in names:
            source = os.path.join(folder, name)
            rel = os.path.relpath(source, backup_dir).replace(os.sep, "/")
            dest = _dest_path(base, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(source, dest)
            count += 1
    return count
