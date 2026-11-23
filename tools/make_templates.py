# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""配布用パッケージに入れる3様式（src/sumikeshi_studio/bundled_forms/）を、国税庁の公開様式（samples/20xxbun_xx.pdf）から作る

様式ごとに、型の画像（PNG）と、様式の情報（<識別子>.json。利用者の様式と同じ形）を書く。型の画像は、公開様式の
1ページ目を、様式の画面で PDF から登録するときと同じく描いて向きと傾きを直した作業画像から、範囲を描く画面で
保存するときと同じく欄の中を紙の色で塗り（欄を囲む枠の線は残す。formcheck.fill_ranges）、縮めたもの。欄の位置は、
公開様式の上で測った枠の線の中心（PUBLIC_FIELDS）をそのまま使う。

作るたびに同じものができる（.json は同じバイト列、型の画像は同じ画素）。tests/test_make_templates.py が、
作り直したものとコミットされているものが同じことを確かめる。

見本の罫線（.json の lines）は、コミットされている様式が持つものをそのまま使い、読み直さない。読み直すときは
--reread-lines を付ける（罫線の一致度が動くので、様式ごとの下限の値も測り直す）。

使い方（リポジトリの直下で）:
    uv run python tools/make_templates.py [--reread-lines]
"""

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from sumikeshi_studio import (
    formcheck,
    forms,
    formsave,
    formstore,
    imaging,
    orientation,
    rulings,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
OUT = formstore.BUNDLED_DIR
SOURCE_URL = "https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/"
# 公開様式を200dpiの作業画像にしたときの、個人番号欄の左上・幅・高さ。4辺とも、印刷された枠の線の中心に置く
# （公開様式の作業画像で枠の線を検出して測った値）。線の中心より内側に置くと、傾けた画像や白黒2値の画像で、
# 欄の端に書いた数字の縦の画を枠の線に選び、数字の一部が墨消しの外に残ることがある
PUBLIC_FIELDS = {
    "2027bun_01": [
        ("支払者の法人（個人）番号", 469, 181, 449, 63),
        ("本人の個人番号", 1072, 181, 413.5, 63),
        ("A 源泉控除対象配偶者", 561, 453, 427, 47.5),
        ("B 源泉控除対象親族 1", 561, 552, 427, 59),
        ("B 源泉控除対象親族 2", 561, 662.5, 427, 59.5),
        ("B 源泉控除対象親族 3", 561, 773.5, 427, 59),
        ("B 源泉控除対象親族 4", 561, 884, 427, 59),
        ("住民税 16歳未満の扶養親族 1", 536, 1407, 413, 56),
        ("住民税 16歳未満の扶養親族 2", 536, 1463, 413, 56),
        ("住民税 退職手当等を有する配偶者・扶養親族", 536, 1564, 413, 55),
    ],
    "2026bun_04": [
        ("年齢23歳未満の扶養親族", 493, 1322.5, 377, 38.5),
    ],
    "2026bun_06": [
        ("配偶者", 1284, 367.5, 409.5, 49.5),
        ("特定親族 1", 516.5, 1120.5, 410.5, 76.5),
        ("特定親族 2", 516.5, 1197, 410.5, 74.5),
        ("所得金額調整控除の扶養親族等", 1098, 1445, 408, 54),
    ],
}
# 型にする申告書（識別子、名前、年分、公開様式、型の画像）
TEMPLATES = [
    ("nta-fuyou-r9", forms.FUYOU, 9, "2027bun_01", "nta-fuyou-r9.png"),
    ("nta-hoken-r8", forms.HOKEN, 8, "2026bun_04", "nta-hoken-r8.png"),
    ("nta-kihai-r8", forms.KIHAI, 8, "2026bun_06", "nta-kihai-r8.png"),
]
# 題名の語と除く語
TITLE_WORDS = {
    "nta-fuyou-r9": (["扶養控除等"], []),
    "nta-hoken-r8": (["保険料控除申"], ["保険料控除証明書"]),
    "nta-kihai-r8": (
        ["基礎控除申", "配偶者控除等申", "特定親族特別控除申", "所得金額調整控除申"],
        [],
    ),
}


def work_image(page: imaging.SourcePage) -> np.ndarray:
    """ツールの解析と同じ手順で、向きと傾きを直した作業画像にする"""
    gray = cv2.cvtColor(imaging.render(page, dpi=imaging.WORK_DPI), cv2.COLOR_RGB2GRAY)
    turned = imaging.correct(gray, orientation.detect(gray).quarter_turns, 0)
    return imaging.correct(turned, 0, imaging.estimate_skew(turned))


def kept_lines(form_id: str) -> dict | None:
    """コミットされている様式の見本の罫線（.json の lines。様式がまだなければ None）"""
    path = OUT / f"{form_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text("utf-8")).get("lines")


def build(form_id: str, reread_lines: bool = False) -> tuple[dict, np.ndarray]:
    """1つの様式の、様式の情報（.json に書く辞書）と型の画像（TEMPLATE_SCALE の解像度、グレー）を作る

    見本の罫線は、コミットされている様式が持つものをそのまま使う（reread_lines なら、塗る前の作業画像から読み直す）。
    罫線の読み取りの処理を変えるたびに読み直すと、罫線の一致度（様式ごとの下限を決めたときの値）が動くため。
    """
    _, name, year, public, image_name = next(t for t in TEMPLATES if t[0] == form_id)
    pdf = SAMPLES / f"{public}.pdf"
    work = work_image(imaging.open_pages(pdf)[0])
    h, w = work.shape
    fields, rects = [], []
    for label, x, y, fw, fh in PUBLIC_FIELDS[public]:
        # 3様式の欄は、どれも上下左右に印刷された枠の線がある（公開様式で確かめた）
        fields.append(
            {
                "name": label,
                "rect": [x, y, fw, fh],
                "frame": list(formstore.FRAME_PAIRS),
            }
        )
        rects.append((x, y, fw, fh))
    small = formcheck.make_template(work, True, rects)
    words, excludes = TITLE_WORDS[form_id]
    lines = None if reread_lines else kept_lines(form_id)
    if lines is None:
        lines = formsave.lines_json(rulings.read(work))
    spec = {
        "format": formstore.FORMAT,
        "id": form_id,
        "name": name,
        "revision": 0,
        "updated_at": "",
        "updated_by": "",
        "image": image_name,
        "size": [w, h],
        "fields": fields,
        "erase": [],
        "title_words": words,
        "title_excludes": excludes,
        "line_match": {
            "use": True,
            "min": formstore.default_line_match_min(form_id),
        },
        # 読み直すときは、塗る前の作業画像から読む（範囲を描く画面で登録するときと同じ）
        "lines": lines,
        # ここから下は、型の作り方の記録（ツールは読まない）
        "source": pdf.name,
        "source_url": SOURCE_URL + pdf.name,
        "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "public_form": public,
        "year": year,
    }
    return spec, small


def spec_text(spec: dict) -> str:
    """様式の情報の .json の中身"""
    return json.dumps(spec, ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    reread_lines = "--reread-lines" in sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    for form_id, *_ in TEMPLATES:
        spec, small = build(form_id, reread_lines)
        ok, buf = cv2.imencode(".png", small)
        assert ok
        (OUT / spec["image"]).write_bytes(buf.tobytes())
        (OUT / f"{form_id}.json").write_text(spec_text(spec), encoding="utf-8")
        print(
            f"{spec['image']}: {spec['source']}、作業画像 {spec['size'][0]}×{spec['size'][1]}、"
            f"欄 {len(spec['fields'])}"
        )


if __name__ == "__main__":
    sys.exit(main())
