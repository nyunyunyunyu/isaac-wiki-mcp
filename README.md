# Isaac API 文档查询

本地 Markdown 快照，查询类、方法、枚举和教程。首次需要 Python 3.11+、uv；无模型服务或游戏运行依赖。

## 最短用法

在本目录执行；从外部运行时给 uv 加 `--project 本目录路径`。首次运行自动安装依赖。

```powershell
uv run isaac-wiki search GetHearts --summary --top-k 3 --game rep --language en
uv run isaac-wiki read EntityPlayer --section GetHearts --game rep --language zh
```

搜索只列页面，按返回的名字或路径读一个方法；方法的参数、说明及子章节一起返回。
省略 `--section` 可读完整页；`--format json` 返回结构化结果。输入输出均为 UTF-8。
摘要搜索和章节阅读是可选项，原来的完整页查询保持兼容。

## 版本与来源

- GOG Repentance 使用 `--game rep`，不加依赖；其他环境可选 `rep+` 和 `--dependency rgon`。
- `--language en|zh` 显式选语言；`auto` 按查询文本判断。
- 显式版本/语言使用 `wiki/reference` 快照；不传配置时保留旧 `wiki/` 查询行为。
- RGON 区块默认隐藏。仍须读页面 DLC 标记：`include_incompatible` 目前仅回显，不代表已完成逐条兼容性过滤。
- `wiki_sources` 或 `wiki/reference/source-release.json` 给出来源和锁定修订；查询无需更新快照。
- `category` 过滤用于旧目录；版本化快照暂不按 category 二次过滤。

## MCP

启动命令：`uv run --project ABSOLUTE_PATH isaac-wiki-mcp`。协议走标准输入输出，日志走 stderr。

| 工具 | 用途 |
|---|---|
| wiki_search | query + summary=true + top_k：只列匹配页面 |
| wiki_read | page + 可选 section：读方法/章节；省略 section 读完整页 |
| wiki_list | 列页面元数据 |
| wiki_stats | 本地索引统计（包含旧目录） |
| wiki_sources | 来源、配置与快照修订 |

查询/阅读/列表传 `game="rep", dependencies=[], language="en"`（或 `"zh"`）。
未知页面或章节返回错误，不会退回其他语言或悄悄输出整页。

## 维护

```powershell
uv run isaac-wiki --help
uv run isaac-wiki list --game rep --language en --format json
uv run isaac-wiki stats --format json
uv run --extra dev pytest tests -q
```

`isaac-wiki build` 重建旧版页面；`isaac-wiki sync-reference SOURCE_ROOT` 从完整的
[Isaac API Edition](https://github.com/3113y/isaac-api-edition) 检出目录重建双语快照及来源记录，
默认写入 wiki/reference。仅更新资料时使用；不是查询的前置步骤。
uv 缓存不可写时，将 `UV_CACHE_DIR` 指向可写目录。许可证：MIT。
