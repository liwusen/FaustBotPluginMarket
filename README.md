# FaustBot Plugin Market

FaustBot 插件市场仓库。托管插件索引、插件包与GitHub Pages页面。

- 市场网页: https://liwusen.github.io/FaustBotPluginMarket/
- 插件索引: https://raw.githubusercontent.com/liwusen/FaustBotPluginMarket/main/plugins.json
- 安装方式: 在市场网页点击「安装」

## 发布插件

0. 按照[FaustBot文档](faustbot.allenlee.xyz)编写插件
1. 准备插件 zip 包：zip 内为一个插件目录，目录内必须包含 `plugin.json`（`id`、`version` 必填），入口文件默认 `main.py`。可用 FaustBot 的「打包为 ZIP」功能生成。
2. 将 zip 上传到任意 https 可直链下载的位置（30MB 以内）。
3. 使用 [提交插件 issue 模板](../../issues/new?template=submit-plugin.yml) 提交，填写插件元数据与 zip 链接。`plugin.json` 中的 `id`/`version` 必须与 issue 填写一致。
4. 维护者审核后为 issue 打上 `approved` 标签，自动化流程将：
   - 下载并校验 zip（结构、`plugin.json` 一致性、路径安全）
   - 重打包并发布到本仓库 Release（tag: `plugin-<id>-v<version>`）
   - 更新 `plugins.json`，下载链接指向 Release 资产
   - 评论并关闭 issue
5. 更新插件：提交新 issue，版本号必须高于已发布版本。已发布的 Release 不可变。

## plugins.json 条目格式

```json
{
  "id": "hello_world",
  "name": "Hello World",
  "description": "...",
  "author": "...",
  "version": "1.0.0",
  "download_url": "https://github.com/liwusen/FaustBotPluginMarket/releases/download/plugin-hello_world-v1.0.0/hello_world.zip",
  "asset_name": "hello_world.zip",
  "homepage": "",
  "tags": ["example"],
  "source_issue": 1,
  "published_at": "2026-01-01T00:00:00Z"
}
```

