# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""作業ログのテスト"""

import csv

from sumikeshi_studio import worklog


def test_log_not_readable_as_utf8_is_kept_and_new_one_started(tmp_path, monkeypatch):
    """作業ログを UTF-8 として読めない（Excel が Shift_JIS で保存し直したなど）ときは、日時を付けた名前で残し、
    新しいファイルに書き始める"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = worklog.log_dir() / worklog.LOG_NAME
    path.parent.mkdir(parents=True)
    # 見出しの行は英語なので、日本語の値の行を Shift_JIS（cp932）で書いて、UTF-8 として読めないファイルにする
    path.write_bytes(
        (",".join(worklog.FIELDS) + "\r\n" + "2026-11-08,利用者\r\n").encode("cp932")
    )
    worklog.append({worklog.INPUT_FILE: "a.pdf"})
    kept = [p for p in path.parent.iterdir() if p != path]
    assert len(kept) == 1 and kept[0].name.startswith("worklog_")
    assert kept[0].read_bytes().startswith(",".join(worklog.FIELDS[:2]).encode("cp932"))
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r[worklog.INPUT_FILE] for r in rows] == ["a.pdf"]
