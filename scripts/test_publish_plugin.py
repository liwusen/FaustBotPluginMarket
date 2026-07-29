"""publish_plugin.py 本地单测（stdlib unittest）。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from publish_plugin import (  # noqa: E402
    PublishError,
    check_against_index,
    is_newer_version,
    parse_issue_body,
    repackage,
    upsert_index,
    validate_and_extract,
)

SAMPLE_BODY = """### 插件 ID (Plugin ID)

hello_world

### 插件名称 (Name)

Hello World

### 版本 (Version)

1.0.0

### 作者 (Author)

liwusen

### 插件包下载链接 (Zip URL)

https://example.com/hello_world.zip

### 主页 (Homepage)

_No response_

### 标签 (Tags)

example, demo

### 插件描述 (Description)

启动时打印 Hello World。
"""


def make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def hello_manifest(pid="hello_world", version="1.0.0", entry="main.py") -> str:
    return json.dumps({"id": pid, "version": version, "entry": entry})


class TestParseIssueBody(unittest.TestCase):
    def test_parse_ok(self):
        meta = parse_issue_body(SAMPLE_BODY)
        self.assertEqual(meta["id"], "hello_world")
        self.assertEqual(meta["name"], "Hello World")
        self.assertEqual(meta["version"], "1.0.0")
        self.assertEqual(meta["zip_url"], "https://example.com/hello_world.zip")
        self.assertEqual(meta["homepage"], "")
        self.assertEqual(meta["tags"], ["example", "demo"])
        self.assertIn("Hello World", meta["description"])

    def test_missing_required(self):
        body = SAMPLE_BODY.replace("hello_world.zip", "").replace(
            "https://example.com/", ""
        )
        with self.assertRaises(PublishError):
            parse_issue_body(body)

    def test_bad_id(self):
        with self.assertRaises(PublishError):
            parse_issue_body(SAMPLE_BODY.replace("hello_world\n", "../evil\n", 1))

    def test_bad_version(self):
        with self.assertRaises(PublishError):
            parse_issue_body(SAMPLE_BODY.replace("1.0.0", "abc"))

    def test_http_url_rejected(self):
        with self.assertRaises(PublishError):
            parse_issue_body(SAMPLE_BODY.replace("https://", "http://"))


class TestVersion(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(is_newer_version("1.10.0", "1.9.0"))
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("0.9.0", "1.0.0"))
        self.assertTrue(is_newer_version("1.0.0.1", "1.0.0"))

    def test_check_against_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "plugins.json"
            index.write_text(
                json.dumps({"plugins": [{"id": "hello_world", "version": "1.0.0"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(PublishError):
                check_against_index({"id": "hello_world", "version": "1.0.0"}, index)
            check_against_index({"id": "hello_world", "version": "1.0.1"}, index)
            check_against_index({"id": "other", "version": "0.1.0"}, index)


class TestValidateZip(unittest.TestCase):
    META = {"id": "hello_world", "version": "1.0.0"}

    def test_valid_zip(self):
        data = make_zip(
            {
                "hello_world/plugin.json": hello_manifest(),
                "hello_world/main.py": "print('hi')",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = validate_and_extract(data, self.META, Path(tmp))
            self.assertTrue((root / "plugin.json").exists())

    def test_zip_slip_rejected(self):
        data = make_zip({"../evil.py": "x", "hello_world/plugin.json": hello_manifest()})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublishError):
                validate_and_extract(data, self.META, Path(tmp))

    def test_id_mismatch(self):
        data = make_zip(
            {
                "other/plugin.json": hello_manifest(pid="other"),
                "other/main.py": "x",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublishError):
                validate_and_extract(data, self.META, Path(tmp))

    def test_version_mismatch(self):
        data = make_zip(
            {
                "hello_world/plugin.json": hello_manifest(version="2.0.0"),
                "hello_world/main.py": "x",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublishError):
                validate_and_extract(data, self.META, Path(tmp))

    def test_missing_entry(self):
        data = make_zip({"hello_world/plugin.json": hello_manifest()})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublishError):
                validate_and_extract(data, self.META, Path(tmp))

    def test_no_manifest(self):
        data = make_zip({"hello_world/main.py": "x"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublishError):
                validate_and_extract(data, self.META, Path(tmp))


class TestRepackageAndIndex(unittest.TestCase):
    def test_repackage_and_upsert(self):
        meta = {
            "id": "hello_world",
            "name": "Hello World",
            "description": "d",
            "author": "a",
            "version": "1.0.0",
            "homepage": "",
            "tags": ["example"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "hello_world"
            (plugin_root / "__pycache__").mkdir(parents=True)
            (plugin_root / "__pycache__" / "x.pyc").write_text("x")
            (plugin_root / "plugin.json").write_text(hello_manifest())
            (plugin_root / "main.py").write_text("print('hi')")

            out = repackage(plugin_root, meta, tmp_path)
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            self.assertIn("hello_world/plugin.json", names)
            self.assertIn("hello_world/main.py", names)
            self.assertFalse(any("__pycache__" in n for n in names))

            index = tmp_path / "plugins.json"
            upsert_index(meta, index, issue_number=7)
            data = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(len(data["plugins"]), 1)
            entry = data["plugins"][0]
            self.assertEqual(entry["source_issue"], 7)
            self.assertIn(
                "releases/download/plugin-hello_world-v1.0.0/hello_world.zip",
                entry["download_url"],
            )
            # upsert 覆盖
            meta2 = dict(meta, version="1.1.0")
            upsert_index(meta2, index, issue_number=8)
            data = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(len(data["plugins"]), 1)
            self.assertEqual(data["plugins"][0]["version"], "1.1.0")


if __name__ == "__main__":
    unittest.main()
