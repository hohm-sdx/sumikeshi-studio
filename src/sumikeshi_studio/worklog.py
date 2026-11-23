# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""作業ログ。墨消しした文字そのものは記録しない"""

import csv
import getpass
from datetime import datetime
from pathlib import Path

from . import datadir

LOG_NAME = "worklog.csv"
# 列の名前。exporttext.worklog_record も、この名前を使う
DATE = "Date/time"
USER = "User"
INPUT_FILE = "Input file"
OUTPUT_FILE = "Output file"
PAGES = "Pages"
FORM_FIELDS = "Form fields"  # 様式で決めた欄の数
FIELDS_OFF = "Fields turned off"  # そのうち、墨消ししないにした欄
MANUAL_AREAS = "Manually added areas"
PAGES_TO_REVIEW = "Pages to review"  # 要確認のページ
PAGES_ORIENTATION = "Pages to check orientation"  # 向きを確かめるページ（⚠ 向き）
PAGES_NO_REDACTION = (
    "Pages without redaction areas"  # 墨消しする範囲のないページ（墨消しなし）
)
PAGES_NOT_ANALYZED = "Pages not analyzed"  # 解析が失敗し、墨消しせずに入れたページ（コマンドラインからの出力だけ）
PAGES_NOT_VIEWED = "Pages not viewed"  # 表示しないまま出力したページ
DELETED_PAGES = "Deleted pages"  # 元のファイルでのページ番号。墨消しPDFに入れていない
REDACTED_AREAS = "Redacted areas"
OCR = "OCR"
FORMS_USED = "Forms used"  # 墨消しする範囲を使った様式と、その版
# 見本との位置合わせで判別したページの、位置合わせの1段目の整合した点の数（「ページの番号: 点」。帳票が2枚以上写った
# ページは点を「/」でつなぐ）
ALIGNMENT_POINTS = "Alignment points"
# バリエーションと決めたページの、罫線の一致度（「ページの番号: 見本の側の割合/ページの側の割合」。小数2桁）
LINE_MATCH = "Line match"
# 様式不明のままになったページのうち、罫線の一致度が下限に届かなかった候補があるページの、一致度と下限
# （「ページの番号: 見本の側の割合/ページの側の割合 < 下限 (様式の識別子 r版)」。小数2桁）
LINE_BELOW = "Line match below limit"
FIELDS = [
    DATE,
    USER,
    INPUT_FILE,
    OUTPUT_FILE,
    PAGES,
    FORM_FIELDS,
    FIELDS_OFF,
    MANUAL_AREAS,
    PAGES_TO_REVIEW,
    PAGES_ORIENTATION,
    PAGES_NO_REDACTION,
    PAGES_NOT_ANALYZED,
    PAGES_NOT_VIEWED,
    DELETED_PAGES,
    REDACTED_AREAS,
    OCR,
    FORMS_USED,
    ALIGNMENT_POINTS,
    LINE_MATCH,
    LINE_BELOW,
]


def log_dir() -> Path:
    """作業ログ・異常終了の記録を置くフォルダ（データのフォルダ。datadir）"""
    return datadir.data_dir()


def append(record: dict) -> Path:
    """作業ログ（CSV、Excel で開ける UTF-8 BOM 付き）に1行足し、ファイルの場所を返す"""
    path = log_dir() / LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _header(path) != FIELDS:
        # 列が変わる前の作業ログは、日時を付けた名前で残し、新しいファイルに書き始める
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        path.rename(path.with_name(f"{path.stem}_{stamp}{path.suffix}"))
    new = not path.exists()
    row = {
        DATE: datetime.now().isoformat(timespec="seconds"),
        USER: getpass.getuser(),
    }
    row.update(record)
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return path


def _header(path: Path) -> list[str]:
    """作業ログの見出しの行。UTF-8 として読めなければ空（列が変わる前の作業ログと同じく、名前を変えて残す。
    Excel が Shift_JIS で保存し直したときなど）"""
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return next(csv.reader(f), [])
    except UnicodeDecodeError:
        return []
