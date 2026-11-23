# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""結果が変わっていないことを確かめる（regression.py の記録と比べる）

65件で数分かかるため、ふだんのテストからは外してある。
    uv run pytest -m regression
"""

import json

import pytest
from conftest import cached_analyze, needs_sample_scan
from regression import CASES, analyze_case, record_path

from sumikeshi_studio import ocr

pytestmark = pytest.mark.regression


def _load(engine: str) -> dict | None:
    path = record_path(engine)
    return json.loads(path.read_text("utf-8")) if path.exists() else None


RECORDS = {e: _load(e) for e in (ocr.Engine.RAPIDOCR,)}


def _skip_reason(engine: str) -> str:
    return "記録がない" if RECORDS[engine] is None else ""


@pytest.mark.parametrize("engine", [ocr.Engine.RAPIDOCR])
@pytest.mark.parametrize(
    ("name", "spec"),
    [
        pytest.param(n, s, id=n, marks=needs_sample_scan if "sample_page" in s else ())
        for n, s in CASES
    ],
)
def test_same_as_record(engine, name, spec, tmp_path):
    reason = _skip_reason(engine)
    if reason:
        pytest.skip(reason)
    # 同じコードで同じ入力を解析した、ほかのテストの結果を使い回す
    assert (
        analyze_case(spec, tmp_path, engine, cached_analyze)
        == RECORDS[engine]["cases"][name]
    )
