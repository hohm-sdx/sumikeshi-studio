# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""tests/regression.py をスクリプトとして動かしたとき（記録を作り直す・比べる）の、見本のスキャンがない場合の扱い

解析は、決まった結果を返す関数に置き換える（65件を解析すると数分かかるため）。
"""

import json

import pytest
import regression

SAMPLE_CASE = "見本 1ページ"
OTHER_CASE = "2027bun_01 記入なし +0.8度"


def entry(form: str) -> dict:
    """記録と同じ形の1件"""
    return {
        "form": form,
        "note": "",
        "quarter_turns": 0,
        "confident": True,
        "skew": 0.0,
        "work": [1654, 2339],
        "boxes": [],
        "unframed": [],
        "crossed": [],
        "used": [],
        "similar": [],
    }


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """見本の1件とほかの1件だけを解析する regression。今の記録には、2件とも「記録」の結果がある"""
    record = tmp_path / "regression_rapidocr.json"
    old = {SAMPLE_CASE: entry("記録"), OTHER_CASE: entry("記録")}
    record.write_text(json.dumps({"engine": "rapidocr", "version": "", "cases": old}))
    analyzed = []

    def analyze_case(spec, folder, engine):
        analyzed.append(spec)
        return entry("解析")

    monkeypatch.setattr(regression, "record_path", lambda engine: record)
    monkeypatch.setattr(
        regression,
        "CASES",
        [
            (SAMPLE_CASE, {"sample_page": 1}),
            (OTHER_CASE, {"form": "2027bun_01", "angle": 0.8}),
        ],
    )
    monkeypatch.setattr(regression, "analyze_case", analyze_case)
    monkeypatch.setattr(regression, "SAMPLE_SCAN", tmp_path / "ない.pdf")
    return record, analyzed


def test_record_keeps_sample_cases_without_sample(tool, capsys):
    """見本がなければ、記録を作り直すときに見本の件は解析せず、今の記録の値を同じ順で書く"""
    record, analyzed = tool
    regression.main("rapidocr")
    cases = json.loads(record.read_text("utf-8"))["cases"]
    assert list(cases) == [SAMPLE_CASE, OTHER_CASE]
    assert cases[SAMPLE_CASE]["form"] == "記録"
    assert cases[OTHER_CASE]["form"] == "解析"
    assert analyzed == [{"form": "2027bun_01", "angle": 0.8}]
    assert "見本の1件は解析せず、記録のまま残しました" in capsys.readouterr().out


def test_compare_skips_sample_cases_without_sample(tool, tmp_path, capsys):
    """見本がなければ、比べるときに見本の件は解析も比較もせず、比べなかった件数を出す。記録は変えない"""
    record, analyzed = tool
    before = record.read_text("utf-8")
    out = tmp_path / "compare.json"
    regression.main("rapidocr", out)
    assert list(json.loads(out.read_text("utf-8"))["cases"]) == [OTHER_CASE]
    assert record.read_text("utf-8") == before
    assert len(analyzed) == 1
    text = capsys.readouterr().out
    assert "見本の1件は解析せず、比べませんでした" in text
    assert "記録にだけある件" not in text
    assert f"変わった：{OTHER_CASE}（判別。" in text


def test_sample_cases_are_analyzed_when_sample_exists(tool, tmp_path, monkeypatch):
    """見本があれば、見本の件も解析する"""
    record, analyzed = tool
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"")
    monkeypatch.setattr(regression, "SAMPLE_SCAN", sample)
    regression.main("rapidocr")
    cases = json.loads(record.read_text("utf-8"))["cases"]
    assert [c["form"] for c in cases.values()] == ["解析", "解析"]
    assert len(analyzed) == 2
