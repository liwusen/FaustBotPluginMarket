"""fpm 发布脚本：解析插件提交 issue、校验 zip、重打包、更新索引。

由 .github/workflows/publish-plugin.yml 调用。issue body 通过环境变量
ISSUE_BODY 传入（避免 shell 注入）。仅依赖标准库。

子命令:
  prepare  解析+校验+下载+重打包，输出 release 参数（写入 GITHUB_OUTPUT）
  update-index  将条目 upsert 进 plugins.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "liwusen/FaustBotPluginMarket")
SAFE_PLUGIN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
MAX_ZIP_BYTES = 30 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60

# issue form 字段 label -> 内部键（与 submit-plugin.yml 的 label 严格一致）
FIELD_LABELS = {
    "插件 ID (Plugin ID)": "id",
    "插件名称 (Name)": "name",
    "版本 (Version)": "version",
    "作者 (Author)": "author",
    "插件包下载链接 (Zip URL)": "zip_url",
    "主页 (Homepage)": "homepage",
    "标签 (Tags)": "tags",
    "插件描述 (Description)": "description",
}
REQUIRED_FIELDS = ("id", "name", "version", "author", "zip_url", "description")


class PublishError(Exception):
    pass


def parse_issue_body(body: str) -> dict:
    """解析 GitHub issue form 渲染出的 Markdown（### <label>\n\n<value> 分段）。"""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = m.group(1)
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()

    meta: dict = {}
    for label, key in FIELD_LABELS.items():
        value = sections.get(label, "").strip()
        if value == "_No response_":
            value = ""
        meta[key] = value

    missing = [k for k in REQUIRED_FIELDS if not meta.get(k)]
    if missing:
        raise PublishError(f"issue 缺少必填字段: {', '.join(missing)}")

    if not SAFE_PLUGIN_ID.match(meta["id"]):
        raise PublishError(f"非法插件 ID: {meta['id']!r}")
    if not VERSION_RE.match(meta["version"]):
        raise PublishError(f"非法版本号: {meta['version']!r}（要求形如 1.0.0）")
    if not meta["zip_url"].startswith("https://"):
        raise PublishError("zip_url 必须是 https:// 直链")

    meta["tags"] = [t.strip() for t in meta["tags"].split(",") if t.strip()] if meta["tags"] else []
    return meta


def version_tuple(version: str) -> tuple:
    return tuple(int(p) for p in version.lstrip("vV").split("."))


def is_newer_version(new: str, old: str) -> bool:
    try:
        a, b = version_tuple(new), version_tuple(old)
    except ValueError:
        return new != old
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


def check_against_index(meta: dict, index_path: Path) -> None:
    if not index_path.exists():
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index.get("plugins", []):
        if entry.get("id") == meta["id"]:
            old = str(entry.get("version") or "0")
            if not is_newer_version(meta["version"], old):
                raise PublishError(
                    f"插件 {meta['id']} 已发布版本 {old}，提交版本 {meta['version']} 未提升"
                )
            return


def download_zip(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fpm-publish"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        length = resp.headers.get("Content-Length")
        if length and int(length) > MAX_ZIP_BYTES:
            raise PublishError(f"zip 超过大小上限 {MAX_ZIP_BYTES} 字节")
        data = resp.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise PublishError(f"zip 超过大小上限 {MAX_ZIP_BYTES} 字节")
    return data


def validate_and_extract(zip_bytes: bytes, meta: dict, workdir: Path) -> Path:
    """校验 zip 并解压，返回插件根目录（包含 plugin.json）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise PublishError(f"无效的 zip 文件: {e}") from e

    extract_dir = workdir / "extracted"
    extract_dir.mkdir(parents=True)
    for info in zf.infolist():
        name = info.filename
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise PublishError(f"zip 含非法路径成员: {name}")
    zf.extractall(extract_dir)

    manifests = [
        p for p in extract_dir.rglob("plugin.json") if "__MACOSX" not in p.parts
    ]
    if not manifests:
        raise PublishError("zip 内未找到 plugin.json")
    if len(manifests) > 1:
        matched = [p for p in manifests if p.parent.name == meta["id"]]
        if len(matched) != 1:
            raise PublishError("zip 内存在多个 plugin.json，无法确定插件根目录")
        manifests = matched

    plugin_root = manifests[0].parent
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    manifest_id = str(manifest.get("id") or plugin_root.name)
    if manifest_id != meta["id"]:
        raise PublishError(f"plugin.json 的 id ({manifest_id}) 与 issue 声明 ({meta['id']}) 不一致")
    manifest_version = str(manifest.get("version") or "")
    if manifest_version != meta["version"]:
        raise PublishError(
            f"plugin.json 的 version ({manifest_version}) 与 issue 声明 ({meta['version']}) 不一致"
        )
    entry = str(manifest.get("entry") or "main.py")
    if not (plugin_root / entry).is_file():
        raise PublishError(f"入口文件不存在: {entry}")
    return plugin_root


def repackage(plugin_root: Path, meta: dict, out_dir: Path) -> Path:
    """重打包为规范 zip：顶层目录 <id>/，剔除 __pycache__。"""
    out_path = out_dir / f"{meta['id']}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plugin_root.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.is_file():
                arcname = f"{meta['id']}/{path.relative_to(plugin_root).as_posix()}"
                zf.write(path, arcname)
    return out_path


def upsert_index(meta: dict, index_path: Path, issue_number: int) -> None:
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"updated_at": "", "plugins": []}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag = f"plugin-{meta['id']}-v{meta['version']}"
    entry = {
        "id": meta["id"],
        "name": meta["name"],
        "description": meta["description"],
        "author": meta["author"],
        "version": meta["version"],
        "download_url": f"https://github.com/{REPO}/releases/download/{tag}/{meta['id']}.zip",
        "asset_name": f"{meta['id']}.zip",
        "homepage": meta.get("homepage") or "",
        "tags": meta.get("tags") or [],
        "source_issue": issue_number,
        "published_at": now,
    }
    plugins = [p for p in index.get("plugins", []) if p.get("id") != meta["id"]]
    plugins.append(entry)
    plugins.sort(key=lambda p: p.get("id", ""))
    index["plugins"] = plugins
    index["updated_at"] = now
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_github_output(values: dict) -> None:
    out_file = os.environ.get("GITHUB_OUTPUT")
    if not out_file:
        for k, v in values.items():
            print(f"{k}={v}")
        return
    with open(out_file, "a", encoding="utf-8") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def cmd_prepare(args: argparse.Namespace) -> None:
    body = os.environ.get("ISSUE_BODY", "")
    if not body.strip():
        raise PublishError("ISSUE_BODY 为空")
    meta = parse_issue_body(body)
    index_path = Path(args.index)
    check_against_index(meta, index_path)

    zip_bytes = download_zip(meta["zip_url"])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        plugin_root = validate_and_extract(zip_bytes, meta, Path(tmp))
        asset_path = repackage(plugin_root, meta, out_dir)

    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    tag = f"plugin-{meta['id']}-v{meta['version']}"
    write_github_output(
        {
            "plugin_id": meta["id"],
            "plugin_name": meta["name"],
            "version": meta["version"],
            "tag": tag,
            "asset_path": str(asset_path),
        }
    )
    print(f"[prepare] ok: {meta['id']} v{meta['version']} -> {asset_path}")


def cmd_update_index(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    upsert_index(meta, Path(args.index), int(args.issue_number))
    print(f"[update-index] ok: {meta['id']} v{meta['version']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="fpm plugin publish helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--index", default="plugins.json")
    p_prepare.add_argument("--out-dir", default="_publish")
    p_prepare.set_defaults(func=cmd_prepare)

    p_update = sub.add_parser("update-index")
    p_update.add_argument("--index", default="plugins.json")
    p_update.add_argument("--out-dir", default="_publish")
    p_update.add_argument("--issue-number", required=True)
    p_update.set_defaults(func=cmd_update_index)

    args = parser.parse_args()
    try:
        args.func(args)
    except PublishError as e:
        print(f"::error::{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
