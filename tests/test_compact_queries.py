"""Small query results, explicit snapshot selection and UTF-8 transports."""
import json
import subprocess
import sys

from isaac_wiki.facade import WikiFacade


def test_summary_keeps_paths_without_page_bodies():
    result = WikiFacade().search("GetHearts", game="rep", language="en", top_k=3, summary=True)
    assert result["status"] == "ok"
    assert result["results"]
    assert result["results"][0]["title"] == "EntityPlayer"
    assert all("path" in row and "content" not in row for row in result["results"])


def test_search_does_not_match_hidden_rgon_content(tmp_path):
    directory = tmp_path / "reference/en"
    directory.mkdir(parents=True)
    (directory / "Demo.md").write_text(
        '# Demo\n<div class="rgon-extension" markdown="1">\n## RgonOnlyMethod\nDetails.\n</div>\n', encoding="utf-8")
    facade = WikiFacade(str(tmp_path))
    assert not facade.search("RgonOnlyMethod", game="rep", language="en")["results"]
    assert facade.search("RgonOnlyMethod", game="rep", language="en", dependencies=["rgon"])["results"]


def test_method_read_keeps_signature_and_conditions_without_nearby_methods():
    result = WikiFacade().read_page("EntityPlayer", game="rep", language="en", section="GetHearts")
    assert result["status"] == "ok"
    assert "int GetHearts" in result["content"]
    assert "1 unit is half a heart" in result["content"]
    assert "GetItemState" not in result["content"]
    assert len(result["content"]) < 1000
    assert result["word_count"] == len(result["content"].split())


def test_sections_keep_overloads_and_ignore_headings_in_code(tmp_path):
    (tmp_path / "Demo.md").write_text(
        "# Demo\n```md\n## Call\nFake.\n```\n"
        "## Call\nREP only.\n### Signature\nFirst overload.\n"
        "## Other\nUnrelated.\n## Call\nSecond overload.\n", encoding="utf-8")
    facade = WikiFacade(str(tmp_path))
    result = facade.read_page("Demo", section="Call")
    assert result["status"] == "ok"
    assert all(text in result["content"] for text in ("REP only", "First overload", "Second overload"))
    assert "Unrelated" not in result["content"] and "Fake" not in result["content"]
    assert facade.read_page("Demo", section="missing")["status"] == "error"
    assert facade.read_page("Demo", section="")["status"] == "error"


def test_profile_read_resolves_paths_inside_requested_language(tmp_path):
    for folder, content in (("enums", "Legacy"), ("reference/en/enums", "English"),
                            ("reference/zh/enums", "Chinese")):
        directory = tmp_path / folder
        directory.mkdir(parents=True)
        (directory / "EntityType.md").write_text(content, encoding="utf-8")
    facade = WikiFacade(str(tmp_path))
    result = facade.read_page("enums/EntityType", game="rep", language="en")
    assert result["content"] == "English"
    assert result["path"] == "reference/en/enums/EntityType.md"
    result = facade.read_page("reference/en/enums/EntityType.md", game="rep", language="zh")
    assert result["content"] == "Chinese"
    assert facade.read_page("enums/EntityType")["content"] == "Legacy"


def test_cli_chinese_section_is_utf8_with_legacy_stdout():
    command = ('import sys; sys.stdout.reconfigure(encoding="gbk"); '
               'from isaac_wiki.cli import main; main(sys.argv[1:])')
    result = subprocess.run([sys.executable, "-c", command, "read", "EntityPlayer", "--section", "GetHearts",
                             "--game", "rep", "--language", "zh", "--format", "json"], capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["profile"]["language"] == "zh"
    assert any("\u4e00" <= c <= "\u9fff" for c in payload["content"])


def test_mcp_stdio_compact_queries_use_utf8():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_search",
         "arguments": {"query": "GetHearts", "game": "rep", "language": "en", "summary": True, "top_k": 1}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wiki_read",
         "arguments": {"page": "EntityPlayer", "section": "GetHearts", "game": "rep", "language": "zh"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki_search",
         "arguments": {"query": "生命", "game": "rep", "language": "zh", "summary": True, "top_k": 1}}},
    ]
    command = ('import sys; sys.stdin.reconfigure(encoding="gbk"); sys.stdout.reconfigure(encoding="gbk"); '
               'from isaac_wiki.server import main; main()')
    result = subprocess.run([sys.executable, "-c", command], capture_output=True,
                            input=("\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n").encode("utf-8"))
    assert result.returncode == 0, result.stderr.decode("utf-8")
    replies = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    search = json.loads(replies[1]["result"]["content"][0]["text"])
    read = json.loads(replies[2]["result"]["content"][0]["text"])
    assert "content" not in search["results"][0]
    assert read["status"] == "ok" and len(read["content"]) < 1000
    chinese_search = json.loads(replies[3]["result"]["content"][0]["text"])
    assert chinese_search["status"] == "ok" and chinese_search["results"]
