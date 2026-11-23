# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""結果が変わっていないことを確かめるための、試験画像の一覧と結果の記録

次の65件について解析の結果を記録する。記録は regression_<OCR>.json に置き、test_regression.py が比べる。
- 公開様式の試験画像（6様式 × 5通りの傾き、記入なし）の30件
- 扶養控除等申告書 令和9年分に記入したもの（5通りの傾き）の5件
- 見本のスキャン（samples/fictional_entries/sample_カラー原色.pdf）の4ページ
- 公開様式を 300dpi の白黒2値（CCITT G4）にした12件（6様式 × 2通りの傾き）と、カラーの JPEG にした6件
  （PDF を画像にするライブラリの違いを見つけるため）
- そのほかの8件：スマートフォンで撮った写真に見立てた画像の3件、
  類似した様式（令和8年分の扶養控除等申告書）を登録した組で解析した3件、配布用パッケージの扶養控除等申告書を編集した組と、
  この PC で使わない様式を除いた組で解析した2件

記録する項目は、判別した様式、要確認の文、向き（90度単位の回転の回数と、向きに自信があるか）、傾き（小数3桁の度）、
作業画像の高さ・幅、墨消しする範囲（種類と、小数2桁の座標。角のある範囲は、角を囲む長方形）、角のある範囲の角
（種類と、小数2桁の座標。角のある範囲があるページだけ）、枠の線が見つからない範囲と枠の線を越えた記入がある
範囲の名前、範囲を使った様式と類似した様式（識別子と版）。座標を小数で記録するのは、範囲の座標が 0.5画素単位の
値で、整数に丸めると 0.5画素の違いが記録に出ないため。

記録を作り直すとき（結果が変わることを確かめたうえで）:
    uv run python tests/regression.py rapidocr

記録を変えずに、今の結果を別のファイルに書き、記録と件ごとに比べるとき:
    uv run python tests/regression.py rapidocr --compare <書き出すファイル>

見本のスキャンがないとき（公開のリポジトリ）は、見本の4件を解析しない。記録を作り直すときは、見本の4件は今の
記録の値をそのまま書き、比べるときは、見本の4件を比べない。どちらも、終わりにそのことを表示する。
"""

import json
import sys
import tempfile
from functools import cache
from pathlib import Path

from scan import (
    PHOTO_LONG,
    PHOTO_SHORT,
    SAMPLE_SCAN,
    SAMPLES,
    make_photo,
    make_scan,
    page_image,
)
from userforms import bundled_forms, install_bundled, make_r8, write_form

from sumikeshi_studio import formstore, imaging, ocr, pipeline

HERE = Path(__file__).resolve().parent
FORMS = [
    "2025bun_01",
    "2026bun_01",
    "2027bun_01",
    "2026bun_08",
    "2026bun_06",
    "2026bun_04",
]
ANGLES = [-1.5, -0.6, 0.8, 3, 8]

L, S = PHOTO_LONG, PHOTO_SHORT
# 写真に写す紙の並びと、縦長の写真か。紙は (様式, 記入あり, 中心x, 中心y, 長辺の画素数, 回転の角度, 台形のゆがみ)
# （scan.make_photo の sheets の画像を、page_image の指定で書いたもの）。置き方は tests/test_photos.py と同じ
PHOTOS = {
    "扶養控除等申告書": ([("2027bun_01", True, L / 2, S / 2, 0.5 * L, 4, 0.15)], False),
    "扶養控除等申告書と保険料控除申告書": (
        [
            ("2027bun_01", True, S / 2, L * 0.26, 2750, 1, 0.05),
            ("2026bun_04", False, S / 2, L * 0.75, 2750, -1.5, 0.05),
        ],
        True,
    ),
    "型のない用紙": ([("2026bun_08", False, L / 2, S / 2, 0.6 * L, 4, 0.15)], False),
}


def _load(folder: Path, off: tuple[str, ...] = ()) -> tuple[formstore.FormSpec, ...]:
    """様式のフォルダの様式を読んだ組（off は、この PC で使わない様式）"""
    choice = formstore.FolderChoice(folder, formstore.ORIGIN_USER)
    return formstore.load(choice, off=off).active


def _installed(
    folder: Path, off: tuple[str, ...] = ()
) -> tuple[formstore.FormSpec, ...]:
    """配布用パッケージの3様式を様式のフォルダに置いて読んだ組"""
    install_bundled(folder)
    return _load(folder, off)


def _edited_fuyou(folder: Path) -> tuple[formstore.FormSpec, ...]:
    """配布用パッケージの3様式を様式のフォルダに置き、扶養控除等申告書を、墨消しする範囲を先頭の3つにして保存した組"""
    install_bundled(folder)
    write_form(folder, "nta-fuyou-r9", "扶養控除等申告書", fields=3, frame=True)
    return _load(folder)


# 使う様式の組の名前と、作業用のフォルダから組を作る関数。利用者の様式の識別子は、記録に書くので固定する
# （「u-」の後は英数字だけ。formstore.SPEC_FILE に合わないファイルは、様式として読まれない）
FORMSETS = {
    "令和8年分（題名に年分あり）": lambda folder: make_r8(
        folder, ("令和8年分　扶養控除等",), "u-r8year"
    ),
    "令和8年分（題名に年分なし）": lambda folder: make_r8(
        folder, ("扶養控除等",), "u-r8noyear"
    ),
    "扶養控除等申告書を編集": _edited_fuyou,
    "扶養控除等申告書を使わない": lambda folder: _installed(folder, ("nta-fuyou-r9",)),
}

# (名前, 指定)。指定は make_scan に渡すもの。見本のスキャンは {"sample_page": ページ番号}、写真は
# {"photo": PHOTOS の名前}。"forms" があれば、FORMSETS のその組で解析する（なければ配布用パッケージの3様式）
CASES = [
    (f"{form} 記入なし {angle:+.1f}度", {"form": form, "angle": angle})
    for form in FORMS
    for angle in ANGLES
]
CASES += [
    (
        f"2027bun_01 記入あり {angle:+.1f}度",
        {"form": "2027bun_01", "angle": angle, "filled": True},
    )
    for angle in ANGLES
]
CASES += [(f"見本 {n}ページ", {"sample_page": n}) for n in (1, 2, 3, 4)]
CASES += [
    (
        f"{form} 白黒2値 {angle:+.1f}度",
        {"form": form, "angle": angle, "kind": "bilevel"},
    )
    for form in FORMS
    for angle in (0.8, 3)
]
CASES += [
    (f"{form} カラーJPEG +0.8度", {"form": form, "angle": 0.8, "kind": "jpeg"})
    for form in FORMS
]
CASES += [(f"写真 {name}", {"photo": name}) for name in PHOTOS]
CASES += [
    (
        f"{form} 記入なし +0.8度 {forms}",
        {"form": form, "angle": 0.8, "forms": forms},
    )
    for form, forms in (
        ("2026bun_01", "令和8年分（題名に年分あり）"),
        ("2027bun_01", "令和8年分（題名に年分あり）"),
        ("2026bun_01", "令和8年分（題名に年分なし）"),
        ("2027bun_01", "扶養控除等申告書を使わない"),
    )
]
CASES += [
    (
        "2027bun_01 記入あり +0.8度 扶養控除等申告書を編集",
        {
            "form": "2027bun_01",
            "angle": 0.8,
            "filled": True,
            "forms": "扶養控除等申告書を編集",
        },
    )
]


def record_path(engine: str) -> Path:
    return HERE / f"regression_{engine}.json"


@cache
def _work() -> tempfile.TemporaryDirectory:
    """様式の組を置く作業用のフォルダ（1回の実行で1つ。終わるときに消える）"""
    return tempfile.TemporaryDirectory(prefix="sumikeshi-studio-regression-")


@cache
def formset(name: str) -> tuple[formstore.FormSpec, ...]:
    """FORMSETS の組（1回の実行で1回だけ作る）"""
    return FORMSETS[name](Path(_work().name) / name)


def analyze_page(
    spec: dict, folder: Path, engine: str, analyze=None
) -> pipeline.PageResult:
    """1件の試験画像を folder に作り、解析する。analyze は解析の関数（省くと pipeline.analyze。テストは、結果を
    使い回す conftest.cached_analyze を渡す）"""
    spec = dict(spec)
    forms = spec.pop("forms", None)
    if "sample_page" in spec:
        page = imaging.open_pages(SAMPLE_SCAN)[spec["sample_page"] - 1]
    elif "photo" in spec:
        sheets, portrait = PHOTOS[spec["photo"]]
        path = folder / "photo.jpg"
        papers = [(page_image(form, filled), *place) for form, filled, *place in sheets]
        make_photo(papers, path, portrait)
        page = imaging.open_pages(path)[0]
    else:
        page = imaging.open_pages(
            make_scan(spec.pop("form"), folder / "scan.pdf", **spec)
        )[0]
    return (analyze or pipeline.analyze)(
        page, engine=engine, formset=formset(forms) if forms else bundled_forms()
    )


def form_label(spec: formstore.FormSpec) -> str:
    """記録に書く様式：識別子と版（配布用パッケージのファイルのままの様式は版0）"""
    return f"{spec.id} r{spec.revision}"


def to_record(res: pipeline.PageResult) -> dict:
    """解析の結果を、記録と同じ形にする"""
    record = {
        "form": str(res.form),
        "note": res.note,
        "quarter_turns": res.quarter_turns,
        "confident": res.orientation_confident,
        "skew": round(res.skew, 3),
        "work": list(res.work.shape[:2]),
        "boxes": [[b.kind, *(round(v, 2) for v in b.rect())] for b in res.boxes],
        "unframed": list(res.unframed),
        "crossed": list(res.crossed),
        "used": [form_label(s) for s in res.used],
        "similar": [form_label(s) for s in res.similar],
    }
    corners = [
        [b.kind, [[round(x, 2), round(y, 2)] for x, y in b.corners]]
        for b in res.boxes
        if b.corners is not None
    ]
    if corners:  # 角のある範囲のないページの記録は、項目を足さずに今の形のままにする
        record["corners"] = corners
    return record


def analyze_case(spec: dict, folder: Path, engine: str, analyze=None) -> dict:
    """1件を解析し、記録と同じ形の結果を返す（analyze は analyze_page と同じ）"""
    return to_record(analyze_page(spec, folder, engine, analyze))


def main(engine: str, compare_to: Path | None = None):
    version = ""
    path = record_path(engine)
    record = json.loads(path.read_text("utf-8"))["cases"] if path.exists() else {}
    # 見本のスキャンがなくても、残りの件の記録を作り直せるようにする
    no_sample = not SAMPLE_SCAN.exists()
    results = {}
    not_analyzed = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, spec in CASES:
            if no_sample and "sample_page" in spec:
                not_analyzed.append(name)
                if not compare_to and name in record:
                    results[name] = record[name]
                continue
            results[name] = analyze_case(spec, Path(tmp), engine)
            print(name, results[name]["form"], len(results[name]["boxes"]), flush=True)
    data = {"engine": engine, "version": version, "cases": results}
    out = compare_to or path
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if compare_to:
        print_differences(
            {k: v for k, v in record.items() if k not in not_analyzed}, results
        )
    if not_analyzed:
        sample = SAMPLE_SCAN
        if sample.is_relative_to(SAMPLES.parent):
            sample = sample.relative_to(SAMPLES.parent).as_posix()
        where = f"見本のスキャン（{sample}）がないので、見本の{len(not_analyzed)}件は解析せず"
        if compare_to:
            print(f"{where}、比べませんでした")
        else:
            missing = [name for name in not_analyzed if name not in record]
            print(f"{where}、記録のまま残しました")
            if missing:
                print(f"記録になかったので、記録に書かなかった件：{'、'.join(missing)}")


# 範囲の座標のほかに比べる項目と、違ったときに出す名前
ITEMS = [
    ("form", "判別"),
    ("note", "要確認"),
    ("quarter_turns", "向き"),
    ("confident", "向きの自信"),
    ("skew", "傾き"),
    ("work", "作業画像の大きさ"),
    ("unframed", "線が見つからない範囲"),
    ("crossed", "線を越えた記入"),
    ("used", "使った様式"),
    ("similar", "類似した様式"),
    ("corners", "角のある範囲の角"),
]


def differences(old: dict, new: dict) -> tuple[str, float | None]:
    """1件の記録と結果を比べ、変わった項目と、範囲の座標の差の最大値を返す。範囲の数か種類が変わったときは、
    座標の差を None にする"""
    changed = [label for key, label in ITEMS if old.get(key) != new.get(key)]
    if [b[0] for b in old["boxes"]] != [b[0] for b in new["boxes"]]:
        changed.append("範囲の数・種類")
        return "・".join(changed), None
    diff = max(
        (
            abs(a - b)
            for o, n in zip(old["boxes"], new["boxes"])
            for a, b in zip(o[1:], n[1:])
        ),
        default=0,
    )
    return "・".join(changed), diff


def print_differences(record: dict, results: dict) -> None:
    """件ごとの違いと、座標の差の分布を表示する"""
    from collections import Counter

    spread = Counter()
    changed = []
    for name, old in record.items():
        if name not in results:
            print(f"記録にだけある件：{name}")
            continue
        what, diff = differences(old, results[name])
        where = "座標は比べられない" if diff is None else f"座標の差 最大{diff:g}画素"
        if what:
            changed.append(name)
            print(f"変わった：{name}（{what}。{where}）")
        else:
            print(f"{name}：{where}")
        if diff is not None:
            spread[diff] += 1
    for name in results.keys() - record.keys():
        print(f"結果にだけある件：{name}")
    print(f"座標のほかの項目が変わった件：{len(changed)}")
    print(
        "座標の差の最大値の分布："
        + "、".join(f"{d:g}画素 {n}件" for d, n in sorted(spread.items()))
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    target = None
    if "--compare" in args:
        i = args.index("--compare")
        target = Path(args[i + 1])
        del args[i : i + 2]
    main(args[0] if args else ocr.Engine.RAPIDOCR, target)
