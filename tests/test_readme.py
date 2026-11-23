# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""公開用の README の日本語版（README.md）と英語版（README.en.md）のテスト。どちらも全文で置き、直すときは同じ
コミットで両方を直すので、片方だけを直したときに気づけるように、見出しの数と階層を比べる"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def headings(text: str) -> list[int]:
    """コードブロックの外の、Markdown の見出しの階層（# の数）の並び"""
    levels, in_code = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code and (m := re.match(r"(#{1,6}) \S", line)):
            levels.append(len(m[1]))
    return levels


def test_headings_count_comment_lines_in_code_out():
    text = "# 題\n\n## 節\n\n```bash\n# 注\n```\n### 項\n#タグ\n"
    assert headings(text) == [1, 2, 3]


def test_japanese_and_english_readmes_have_same_headings():
    """README.md と README.en.md の見出しの数と、それぞれの階層の並びが同じ"""
    ja = (ROOT / "README.md").read_text("utf-8")
    en = (ROOT / "README.en.md").read_text("utf-8")
    assert headings(ja) and headings(ja) == headings(en)


def test_readmes_link_to_each_other_at_top():
    """日本語版の冒頭に英語版へのリンクを、英語版の冒頭に日本語版へのリンクを置く"""
    ja = (ROOT / "README.md").read_text("utf-8").splitlines()[:5]
    en = (ROOT / "README.en.md").read_text("utf-8").splitlines()[:5]
    assert any("](README.en.md)" in line for line in ja)
    assert any("](README.md)" in line for line in en)


def test_readmes_show_demo_at_top():
    """日本語版と英語版の題名の直後に、デモの動画（assets/demo.webp。アニメーションの WebP）を、何をするところかを書いた
    代わりの文と一緒に表示する"""
    for name in ("README.md", "README.en.md"):
        lines = (ROOT / name).read_text("utf-8").splitlines()
        assert lines[0].startswith("# ") and lines[1] == "", name
        assert lines[2].startswith("![") and lines[2].endswith("](assets/demo.webp)"), (
            name
        )
        assert len(lines[2]) > len("![](assets/demo.webp)") + 10, name
    data = (ROOT / "assets" / "demo.webp").read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
