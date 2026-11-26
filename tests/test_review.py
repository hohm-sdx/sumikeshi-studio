# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""確認画面のデータ（review）と、墨消しの画面に出す文とボタンの条件（reviewtext）、出力の計画と出力の前後の文・作業ログの
1行（exporttext）のテスト（画面もOCRも使わない）"""

import dataclasses
import shutil
from pathlib import Path, PurePath

import numpy as np
from userforms import bundled, bundled_forms

from sumikeshi_studio import (
    boxes,
    export,
    exporttext,
    folders,
    forms,
    formstore,
    i18n,
    imaging,
    ocr,
    pipeline,
    review,
    reviewtext,
    uitext,
    variants,
    worklog,
)
from sumikeshi_studio.boxes import KIND_MANUAL, Box, Source


def make_doc(path: Path, n_pages: int, number: int = 0) -> review.Document:
    sources = [imaging.SourcePage(path, i, 200.0) for i in range(n_pages)]
    return review.open_document(path, sources, number, ocr.Engine.RAPIDOCR, "RapidOCR")


def result(boxes, confident=True, form=forms.FUYOU, note="") -> pipeline.PageResult:
    return pipeline.PageResult(
        quarter_turns=0,
        skew=0.0,
        orientation_confident=confident,
        work=np.zeros((10, 10), np.uint8),
        boxes=boxes,
        form=form,
        note=note,
    )


def auto(active=True):
    return Box(0, 0, 10, 10, "本人の個人番号", Source.AUTO, active)


def manual():
    return Box(0, 0, 10, 10, KIND_MANUAL, Source.MANUAL)


def report(**kw) -> export.ExportReport:
    return export.ExportReport(**{"pages": 2, "masked": 3, **kw})


def test_box_with_corners_keeps_bounds_and_geometry():
    """角のある範囲（長方形でない範囲）の x・y・w・h は、角を囲む長方形。形は、長方形なら (x, y, 幅, 高さ)、角の
    ある範囲なら角の並びで、元に戻す操作の前と後の形として入れ替えられる"""
    corners = ((10.0, 20.0), (110.0, 15.0), (112.0, 45.0), (12.0, 50.0))
    box = Box(0, 0, 0, 0, "本人の個人番号", Source.AUTO, corners=corners)
    assert box.rect() == (10.0, 15.0, 102.0, 35.0)
    assert box.polygon() == corners and box.geometry() == corners
    box.set_geometry(((0, 0), (8, 0), (8, 8)))
    assert box.corners == ((0.0, 0.0), (8.0, 0.0), (8.0, 8.0))
    assert box.rect() == (0.0, 0.0, 8.0, 8.0)
    rect = Box(1, 2, 3, 4, KIND_MANUAL, Source.MANUAL)
    assert rect.corners is None and rect.geometry() == (1, 2, 3, 4)
    assert rect.polygon() == ((1, 2), (4, 2), (4, 6), (1, 6))
    rect.set_geometry((5, 6, 7, 8))
    assert rect.rect() == (5, 6, 7, 8) and rect.corners is None
    assert boxes.is_corners(corners) and not boxes.is_corners((1, 2, 3, 4))


def test_pages_know_their_numbers(tmp_path):
    doc = make_doc(tmp_path / "a.pdf", 3, number=1)
    assert [st.number for st in doc.pages] == [0, 1, 2]
    assert all(st.doc is doc for st in doc.pages)
    assert doc.number == 1


def test_page_label():
    doc = make_doc(Path("a.pdf"), 2)
    first, second = doc.pages
    assert reviewtext.joined(reviewtext.page_parts(second)) == "2ページ　解析中…"
    first.result = result([auto(), auto(active=False), manual()], confident=False)
    assert reviewtext.joined(reviewtext.page_parts(first)) == (
        "1ページ　扶養控除等申告書　欄 2（解除 1）　追加 1　⚠ 向き　未表示"
    )
    # 一覧には、行の文のあとにラベルを並べる
    assert reviewtext.page_parts(first) == (
        "1ページ　扶養控除等申告書",
        [
            ("欄 2（解除 1）", uitext.NEUTRAL),
            ("追加 1", uitext.NEUTRAL),
            ("⚠ 向き", uitext.WARN),
            ("未表示", uitext.NEUTRAL),
        ],
    )
    first.viewed = True
    first.result = result([], form=formstore.UNKNOWN, note=forms.UNKNOWN_NOTE)
    assert (
        reviewtext.joined(reviewtext.page_parts(first))
        == "1ページ　様式不明　墨消しなし　⚠ 要確認　✓"
    )
    assert reviewtext.page_parts(first)[1] == [
        ("墨消しなし", uitext.WARN),  # 墨消しする範囲が1つもない
        ("⚠ 要確認", uitext.WARN),
        ("✓", uitext.GOOD),
    ]
    second.error = "OSError: x"
    assert reviewtext.page_parts(second) == (
        "2ページ",
        [("✕ 処理できません", uitext.ERROR)],
    )


def test_doc_label_and_export_targets():
    doc = make_doc(Path("a.pdf"), 2)
    assert reviewtext.joined(reviewtext.doc_parts(doc)) == "a.pdf　解析中…"
    for st in doc.pages:
        st.result = result([auto()])
    doc.pages[0].viewed = True
    assert reviewtext.doc_parts(doc) == ("a.pdf", [("確認 1／2", uitext.NEUTRAL)])
    assert review.export_targets([doc]) == [
        doc
    ]  # 表示していないページがあっても出力できる
    doc.pages[1].viewed = True
    doc.exported = True
    assert reviewtext.doc_parts(doc) == (
        "a.pdf",
        [("確認 2／2", uitext.GOOD), ("✓ 出力済み", uitext.GOOD)],
    )
    doc.pages[1].result = result([], note="型がありません")
    assert (
        reviewtext.joined(reviewtext.doc_parts(doc))
        == "a.pdf　確認 2／2　墨消しなし 1　⚠ 要確認あり　✓ 出力済み"
    )
    assert review.export_targets([doc]) == []


def test_export_waits_for_analysis_and_skips_failed():
    a, b, c = (make_doc(Path(f"{n}.pdf"), 1, i) for i, n in enumerate("abc"))
    for d in (a, b, c):
        d.pages[0].result = result([])
    a.pages[0].viewed = True
    assert review.export_targets([a, b]) == [a, b]  # b を表示していなくても出力できる
    a.exported = True
    assert review.export_targets([a, b]) == [b]
    # 処理できないページがあるファイルは出力せず、ほかのファイルの出力も止めない
    c.pages[0].result, c.pages[0].error = None, "OSError: x"
    assert review.export_targets([a, b, c]) == [b]
    assert review.skipped_docs([a, b, c]) == [c]
    assert (
        exporttext.skipped_text(c)
        == "✗ c.pdf：処理できないページがあるため、出力しませんでした。"
        "そのページを削除すると、出力できます。"
    )
    # 解析中のページがあれば、どれも出力しない
    assert review.export_targets([a, b, make_doc(Path("d.pdf"), 1, 3)]) == []


def test_status_parts():
    """画面下は、項目ごとのラベル（文, 種類, マウスを重ねたときの説明）にする。表示しているページの要確認の文は、
    画面下でなく、ページの上の帯に出す"""
    a, b = make_doc(Path("a.pdf"), 2, 0), make_doc(Path("b.pdf"), 1, 1)
    pages = a.pages + b.pages
    a.pages[0].result = result(
        [], note="「本人」の欄の範囲が、欄に合っていないおそれがあります"
    )
    b.pages[0].error = "x"
    parts = reviewtext.status_parts([a, b], pages, a.pages[0])
    assert parts == [
        ("1／2ファイル・1／2ページ", uitext.NEUTRAL, ""),
        ("帳票：扶養控除等申告書", uitext.NEUTRAL, ""),
        ("解析中：残り 1ページ", uitext.NEUTRAL, ""),
        (
            "✕ 処理できなかったページ：1",
            uitext.ERROR,
            "そのページを削除しないと、そのファイルは出力しません",
        ),
        (
            "まだ表示していないページ：1",
            uitext.WARN,
            "このまま出力すると、出力の前に警告が出ます",
        ),
        ("出力済み：0／2ファイル", uitext.NEUTRAL, ""),
    ]
    assert not any("欄に合っていない" in t for t, _, _ in parts)
    assert reviewtext.page_messages(a.pages[0]) == [
        (
            "⚠ 要確認　「本人」の欄の範囲が、欄に合っていないおそれがあります",
            uitext.WARN,
        ),
        ("墨消しなし　墨消しする範囲がありません", uitext.WARN),
    ]
    # 出力したファイルのページは数えない（出力した後に解除・追加して未出力に戻れば、また数える）
    a.exported = True
    texts = [t for t, _, _ in reviewtext.status_parts([a, b], pages, a.pages[0])]
    assert not any("まだ表示していないページ" in t for t in texts)
    a.exported = False
    texts = [t for t, _, _ in reviewtext.status_parts([a, b], pages, a.pages[0])]
    assert "まだ表示していないページ：1" in texts
    assert reviewtext.status_parts([a], a.pages, None) == []


def test_page_messages_without_page():
    """表示しているページがなければ、ページの上の帯に何も出さない"""
    assert reviewtext.page_messages(None) == []


def test_page_messages_for_orientation_and_deleted_page():
    """ページの上の帯：向きを決められなかったページ（注意）と、削除したページ（誤りの色）"""
    doc = make_doc(Path("a.pdf"), 1)
    st = doc.pages[0]
    assert reviewtext.page_messages(st) == []  # 解析中
    st.result = result([auto()], confident=False)
    assert reviewtext.page_messages(st) == [
        ("⚠ 向き　向きが正しくないおそれがあります", uitext.WARN)
    ]
    st.deleted = True
    assert reviewtext.page_messages(st) == [
        (
            "削除したページ　墨消しPDFに入れません。削除を取り消すと元に戻ります",
            uitext.ERROR,
        )
    ]


def test_page_messages_for_variant_page():
    """バリエーションと決めたページの上の帯は、「⚠ 要確認」とバリエーションの文だけ（確かでない欄があっても名前は
    挙げない）。英語の画面では英語の文"""
    hoken = bundled("nta-hoken-r8")
    field = formstore.Field("年齢23歳未満の扶養親族", 10, 20, 300, 30, ())
    found = variants.Variant(hoken, variants.Match(0.45, 0.6), (field,), (field.name,))
    doc = make_doc(Path("a.pdf"), 1)
    st = doc.pages[0]

    def analyzed():
        loc = forms.from_variant(found)
        boxes = [Box(f.x, f.y, f.w, f.h, f.name, Source.AUTO) for f in loc.fields]
        return dataclasses.replace(
            result(boxes, form=loc.form, note=loc.note), variant=True
        )

    st.result = analyzed()
    assert reviewtext.page_messages(st) == [
        (
            "⚠ 要確認　『保険料控除申告書』のバリエーションのため、範囲がずれているおそれがあります",
            uitext.WARN,
        )
    ]
    i18n.set_language(i18n.EN)
    st.result = analyzed()
    assert reviewtext.page_messages(st) == [
        (
            (
                '⚠ Review: This sheet is a variation of the form "保険料控除申告書", '
                "so the redaction areas may be misaligned"
            ),
            uitext.WARN,
        )
    ]


def test_forms_parts():
    """画面下の様式の数。この PC で使わない様式は、誤りの色で数だけを出す（起動の画面の一覧と同じ色）"""
    loaded = formstore.Loaded(
        None, forms=list(bundled_forms()), off=frozenset({"nta-hoken-r8"})
    )
    assert reviewtext.forms_parts(loaded) == [
        ("使う様式：2", uitext.NEUTRAL, ""),
        (
            "✕ この PC で使わない様式：1",
            uitext.ERROR,
            (
                "起動の画面で「使う」のチェックを外した様式です。その帳票のページは、"
                "自動で墨消しされません"
            ),
        ),
    ]


def test_check_pages_and_worklog_columns(tmp_path, monkeypatch):
    """確かめるべきページの種類（要確認・向き・様式不明・墨消しなし・処理できない）と、その作業ログの列。削除したページは
    数えない。1つのページが2つ以上の種類に入る（様式不明のページは、要確認と墨消しなしにも入る）"""
    doc = make_doc(Path("in/a.pdf"), 5)
    doc.pages[0].result = result([auto()])
    doc.pages[1].result = result([auto()], confident=False)
    doc.pages[2].result = result([], form=formstore.UNKNOWN, note=forms.UNKNOWN_NOTE)
    doc.pages[3].error = "解析のプロセスが途中で終了しました。"
    doc.pages[4].result = result([], confident=False)
    doc.pages[4].deleted = True
    assert exporttext.check_pages(doc) == [
        (uitext.REVIEW_LABEL, [3]),
        (uitext.ORIENTATION_LABEL, [2]),
        (formstore.UNKNOWN, [3]),
        (review.NO_MASK, [3]),
        (uitext.FAILED_LABEL, [4]),
    ]
    rec = exporttext.worklog_record(doc, Path("out/a_redacted.pdf"), report(pages=4))
    assert (
        rec["Pages to review"],
        rec["Pages to check orientation"],
        rec["Pages without redaction areas"],
        rec["Pages not analyzed"],
    ) == ("3", "2", "3", "4")
    doc.pages[2].result = result([auto()])
    doc.pages[1].result = result([auto()])
    doc.pages[3].error = ""
    doc.pages[3].result = result([auto()])
    assert exporttext.check_pages(doc) == []


def test_worklog_match_columns():
    """作業ログの一致度の列：見本との位置合わせで判別したページは「Alignment points」に1段目の点の数を、バリエーションと
    決めたページは「Line match」に罫線の一致度（見本の側の割合/ページの側の割合。小数2桁）を、「ページの番号: 値」で書く。帳票が2枚写ったページは点を
    「/」でつなぐ。値のないページ（様式不明・もう一方のやり方）と、削除したページ・処理できないページは書かない。
    見本と位置合わせできて、罫線の対応で置き直したページは、両方の値を持つので、両方の列に書く。様式不明のままに
    なったページのうち、罫線の一致度が下限に届かなかった候補があるページは、「Line match below limit」に
    「ページの番号: 見本の側/ページの側 < 下限 (様式の識別子 r版)」で書く"""
    doc = make_doc(Path("in/a.pdf"), 8)
    doc.pages[0].result = dataclasses.replace(result([auto()]), points=(912,))
    doc.pages[1].result = dataclasses.replace(result([auto()]), points=(640,))
    doc.pages[2].result = dataclasses.replace(
        result([], form=formstore.UNKNOWN, note=forms.UNKNOWN_NOTE),
        line_below=forms.BelowLimit("nta-fuyou-r9", 1, 0.4123, 0.5512, 0.7),
    )
    doc.pages[3].result = dataclasses.replace(
        result([auto(), auto()]), points=(1203, 455)
    )
    doc.pages[4].result = dataclasses.replace(
        result([auto()], note="バリエーション"),
        variant=True,
        line_match=(0.4567, 0.7123),
    )
    doc.pages[5].result = dataclasses.replace(result([auto()]), points=(700,))
    doc.pages[5].deleted = True
    doc.pages[6].error = "解析のプロセスが途中で終了しました。"
    doc.pages[7].result = dataclasses.replace(
        result([auto()], note="バリエーション"),
        variant=True,
        points=(530,),
        line_match=(0.791, 0.804),
    )
    rec = exporttext.worklog_record(doc, Path("out/a_redacted.pdf"), report(pages=7))
    assert rec["Alignment points"] == "1: 912, 2: 640, 4: 1203/455, 8: 530"
    assert rec["Line match"] == "5: 0.46/0.71, 8: 0.79/0.80"
    assert rec["Line match below limit"] == "3: 0.41/0.55 < 0.70 (nta-fuyou-r9 r1)"
    assert list(rec)[-3:] == [
        worklog.ALIGNMENT_POINTS,
        worklog.LINE_MATCH,
        worklog.LINE_BELOW,
    ]
    assert worklog.FIELDS[-3:] == [
        "Alignment points",
        "Line match",
        "Line match below limit",
    ]


def test_write_worklog_returns_reason_when_it_cannot_write(tmp_path, monkeypatch):
    """作業ログに書けたら空、書けなければ理由を返し、例外を外へ出さない"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    doc = make_doc(Path("in/a.pdf"), 1)
    doc.pages[0].result = result([auto()])
    out = Path("out/a_redacted.pdf")
    assert exporttext.write_worklog(doc, out, report(pages=1)) == ""
    assert (worklog.log_dir() / worklog.LOG_NAME).exists()

    def locked(record):
        raise PermissionError("使用中")

    monkeypatch.setattr(worklog, "append", locked)
    assert (
        exporttext.write_worklog(doc, out, report(pages=1)) == "PermissionError: 使用中"
    )


def test_output_names_are_decided_over_all_opened_files(tmp_path):
    """出力先の名前は、出力するファイルの選び方によらず、開いた全ファイルを通して決める。入力と同じファイルを見分ける"""
    a, b = (
        make_doc(tmp_path / "x" / "s.pdf", 1),
        make_doc(tmp_path / "y" / "s.pdf", 1, 1),
    )
    names = exporttext.output_names([a, b], tmp_path)
    assert [p.name for p in names.values()] == ["s_redacted.pdf", "s_redacted_2.pdf"]
    assert not exporttext.overwrites_input([a, b], list(names.values()))
    assert exporttext.overwrites_input([a, b], [tmp_path / "x" / "s.pdf"])


def test_worklog_record_and_result_text():
    doc = make_doc(Path("in/a.pdf"), 2)
    doc.pages[0].result = result([auto(), auto(active=False), manual()])
    doc.pages[1].result = result([], form=formstore.UNKNOWN, note=forms.UNKNOWN_NOTE)
    out = Path("out/a_redacted.pdf")
    rec = exporttext.worklog_record(doc, out, report())
    assert rec == {
        "Input file": str(doc.path),
        "Output file": str(out),
        "Pages": 2,
        "Form fields": 2,
        "Fields turned off": 1,
        "Manually added areas": 1,
        "Pages to review": "2",
        "Pages to check orientation": "",
        "Pages without redaction areas": "2",
        "Pages not analyzed": "",
        "Pages not viewed": "1, 2",
        "Deleted pages": "",
        "Redacted areas": 3,
        "OCR": "RapidOCR",
        "Forms used": "",
        "Alignment points": "",
        "Line match": "",
        "Line match below limit": "",
    }
    assert exporttext.result_text(doc, out, report()).splitlines() == [
        "⚠ a.pdf → a_redacted.pdf　墨消しした範囲：3か所",
        "　　表示していないページ：1〜2（確かめずに出力しました）",
        "　　墨消しする範囲のないページ：2",  # 記号は変えない
        (
            "　　要確認のページ：2（墨消しの範囲が欄に合っていないおそれがある欄か、"
            "様式を判別できずに墨消ししていない用紙があるページ）"
        ),
    ]
    for st in doc.pages:
        st.viewed = True
    assert exporttext.worklog_record(doc, out, report())["Pages not viewed"] == ""
    assert exporttext.result_text(doc, out, report()).splitlines()[0] == (
        "✓ a.pdf → a_redacted.pdf　墨消しした範囲：3か所"
    )

    assert exporttext.failure_text(doc, "E") == "✗ a.pdf：出力できませんでした。E"

    # 作業ログに書けなかったときは、理由を入れて「⚠」にする
    lines = exporttext.result_text(
        doc, out, report(), "PermissionError: 使用中"
    ).splitlines()
    assert lines[0].startswith("⚠ a.pdf")
    assert (
        "　　作業ログに書けませんでした（PermissionError: 使用中）。"
        "worklog.csv を開いているソフトがあれば閉じてください。"
    ) in lines


def test_unviewed_warning():
    """表示していないページを、ファイルごとに、「⚠ 要確認」「⚠ 向き」のページと分けて示す"""
    a, b, c = (make_doc(Path(f"{n}.pdf"), 6, i) for i, n in enumerate("abc"))
    for d in (a, b, c):
        for st in d.pages:
            st.result = result([auto()])
    for st in a.pages[:3] + b.pages + c.pages[1:]:
        st.viewed = True
    a.pages[4].result = result(
        [], note="「本人」の欄の範囲が、欄に合っていないおそれがあります"
    )
    a.pages[5].result = result([], confident=False)
    assert review.unviewed_pages(a) == [4, 5, 6]
    text = exporttext.unviewed_warning([a, b, c])
    assert "・a.pdf\n　　表示していないページ：4\n" in text
    assert "　　表示していない「⚠ 要確認」のページ：5\n" in text
    assert "　　表示していない「⚠ 向き」のページ：6\n" in text
    assert "・b.pdf" not in text  # 全ページを表示したファイルは示さない
    assert "・c.pdf\n　　表示していないページ：1\n" in text
    assert (
        "表示していないページは、墨消しの範囲が確認されないまま出力されます。" in text
    )
    assert "出力後の検査" not in text  # 出力のたびの検査はしないので、その文も出さない
    assert (
        "「⚠ 要確認」のページには、墨消しの範囲が欄に合っていないおそれがある欄か、"
        "様式を判別できずに墨消ししていない用紙があります。" in text
    )
    assert "向きを自動で判定できず、確かめていません" in text
    assert text.endswith("表示していないページを確かめずに出力しますか？")
    # 「⚠ 要確認」「⚠ 向き」のページがなければ、その説明は出さない
    plain = exporttext.unviewed_warning([c])
    assert "おそれがあります" not in plain and "向きを自動で" not in plain
    assert exporttext.unviewed_warning([b]) == ""


def test_unviewed_warning_without_plain_pages():
    """表示していないページがすべて「⚠ 要確認」のファイルは、「表示していないページ：」の行を出さず、「⚠ 要確認」の行
    だけを示す"""
    d = make_doc(Path("d.pdf"), 3)
    d.pages[0].result = result([auto()])
    d.pages[0].viewed = True
    for st in d.pages[1:]:
        st.result = result(
            [auto()], note="「本人」の欄の範囲が、欄に合っていないおそれがあります"
        )
    text = exporttext.unviewed_warning([d])
    assert "・d.pdf\n　　表示していない「⚠ 要確認」のページ：2〜3\n" in text
    assert "表示していないページ：" not in text


def test_deleted_pages_are_left_out():
    """削除したページは、出力・解析の待ち・表示していないページ・要確認・処理できないページのどれにも数えない"""
    a, b = make_doc(Path("a.pdf"), 3, 0), make_doc(Path("b.pdf"), 1, 1)
    pages = a.pages + b.pages
    a.pages[0].result = result([auto()])
    a.pages[0].viewed = True
    a.pages[1].error = "OSError: x"  # 処理できないページ
    a.pages[2].result = None  # 解析中
    b.pages[0].result = result(
        [], note="「本人」の欄の範囲が、欄に合っていないおそれがあります"
    )
    assert a.failed and review.export_targets([a, b]) == [b]
    assert (
        reviewtext.joined(reviewtext.doc_parts(a)) == "a.pdf　✕ 処理できないページあり"
    )
    # 処理できないページと解析中のページを削除すると、残りを出力できる
    a.pages[1].deleted = a.pages[2].deleted = True
    assert not a.failed and a.kept == [a.pages[0]]
    assert review.export_targets([a, b]) == [a, b]
    assert reviewtext.joined(reviewtext.doc_parts(a)) == "a.pdf　確認 1／1　削除 2"
    assert (
        reviewtext.joined(reviewtext.page_parts(a.pages[1]))
        == "2ページ　削除（出力しない）"
    )
    assert review.deleted_pages(a) == [2, 3]
    text = "".join(t for t, _, _ in reviewtext.status_parts([a, b], pages, a.pages[1]))
    assert reviewtext.page_messages(a.pages[1])[0][0].startswith("削除したページ")
    assert "処理できなかったページ" not in text and "解析中" not in text
    # 表示していないページ・要確認のページからも除く
    b.pages[0].deleted = True
    assert review.unviewed_pages(b) == [] and review.review_pages(b.pages) == []
    assert exporttext.unviewed_warning([a, b]) == ""
    # 全ページを削除したファイルは出力しない
    assert b.all_deleted and review.export_targets([a, b]) == [a]
    assert review.deleted_docs([a, b]) == [b]
    assert (
        reviewtext.joined(reviewtext.doc_parts(b))
        == "b.pdf　全ページ削除（出力しない）"
    )
    assert exporttext.all_deleted_text(b) == (
        "・b.pdf：全ページを削除したため、出力しませんでした。"
    )


def test_deleted_pages_in_log_and_result():
    doc = make_doc(Path("in/a.pdf"), 6)
    for st in doc.pages:
        st.result, st.viewed = result([auto()]), True
    for i in (2, 4, 5):
        doc.pages[i].deleted = True
    out = Path("out/a_redacted.pdf")
    rec = exporttext.worklog_record(doc, out, report(pages=3))
    assert rec["Deleted pages"] == "3, 5, 6" and rec["Pages"] == 3
    assert rec["Form fields"] == 3  # 墨消しPDFに入れたページの分
    lines = exporttext.result_text(doc, out, report(pages=3)).splitlines()
    assert lines[0].startswith("✓ a.pdf")  # 削除だけでは「⚠」にしない
    assert lines[1] == "　　削除したページ：3、5〜6"


def test_ranges_text():
    assert exporttext.ranges_text([1, 3, 4, 5, 8, 9]) == "1、3〜5、8〜9"
    assert exporttext.ranges_text([2]) == "2"
    assert exporttext.ranges_text([]) == ""


def reviewed(path: Path, number: int) -> review.Document:
    doc = make_doc(path, 1, number)
    doc.pages[0].result = result([])
    doc.pages[0].viewed = True
    return doc


def test_plan_export_names_and_existing_files(tmp_path):
    a = reviewed(tmp_path / "x" / "scan.pdf", 0)
    b = reviewed(tmp_path / "y" / "scan.pdf", 1)
    b.exported = (
        True  # 出力済みのファイルは出力しないが、名前は全ファイルを通して決める
    )
    out = tmp_path / "出力"
    out.mkdir()
    (out / "scan_redacted.pdf").write_bytes(b"")
    plan = exporttext.plan_export([a, b], out)
    assert plan.error == ""
    assert plan.jobs == [(a, out / "scan_redacted.pdf")]
    assert plan.existing == [out / "scan_redacted.pdf"]


def test_plan_export_refuses_to_overwrite_input(tmp_path):
    # 前に出力したファイルを開いて、同じフォルダに出力しようとした場合
    src = tmp_path / "scan.pdf"
    again = reviewed(tmp_path / "scan_redacted.pdf", 1)
    plan = exporttext.plan_export([reviewed(src, 0), again], tmp_path)
    assert plan.error == exporttext.NO_OVERWRITE


def test_plan_export_without_targets(tmp_path):
    doc = make_doc(tmp_path / "a.pdf", 1)  # 解析が終わっていない
    assert exporttext.plan_export([doc], tmp_path).jobs == []


def three_docs(tmp_path):
    """表示したファイル、表示していないページのあるファイル、処理できないページのあるファイル（1ページずつ）"""
    ok, unviewed, failed = (
        make_doc(tmp_path / f"{n}.pdf", 1, i) for i, n in enumerate("abc")
    )
    for d in (ok, unviewed):
        d.pages[0].result = result([auto()])
    ok.pages[0].viewed = True
    failed.pages[0].error = "OSError: 試験"
    return ok, unviewed, failed


def test_export_questions_in_order(tmp_path):
    """出力の前に確かめる文は、処理できないページがあるファイルを除くこと、出力先のパスが長すぎるファイルを除く
    こと、表示していないページの警告、上書きの順。確かめることがなければ空"""
    ok, unviewed, failed = three_docs(tmp_path)
    out = tmp_path / "出力"

    def plan(docs, existing=False, too_long=()):
        jobs = [
            (d, out / f"{d.path.stem}_redacted.pdf")
            for d in review.export_targets(docs)
        ]
        return exporttext.ExportPlan(
            [job for job in jobs if job[0] not in too_long],
            existing=[p for _, p in jobs] if existing else [],
            folder=out,
            too_long=[job for job in jobs if job[0] in too_long],
        )

    skipped = exporttext.skipped_question([failed])
    assert skipped == (
        "次のファイルは、処理できないページがあるため出力しません。ほかのファイルを出力しますか？\n"
        "・c.pdf\n処理できないページを削除すると、そのファイルも出力できます。"
    )
    overwrite = exporttext.overwrite_question(
        [out / "a_redacted.pdf", out / "b_redacted.pdf"], out
    )
    assert overwrite == (
        "出力先に、同じ名前のファイルがすでにあります。上書きしますか？\n"
        "・a_redacted.pdf\n・b_redacted.pdf"
    )
    too_long = exporttext.too_long_question([(ok, out / "a_redacted.pdf")], out)
    assert too_long == (
        "次のファイルは、出力先のパスが長すぎるため出力しません。ほかのファイルを出力しますか？\n"
        "・a_redacted.pdf\n出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。"
    )
    warning = exporttext.unviewed_warning([unviewed])
    assert warning.startswith("次のファイルには、表示していないページがあります。")
    assert exporttext.export_questions([ok], plan([ok])) == []
    assert exporttext.export_questions([ok, failed], plan([ok, failed])) == [skipped]
    assert exporttext.export_questions([unviewed], plan([unviewed])) == [warning]
    assert exporttext.export_questions([ok, unviewed], plan([ok, unviewed], True)) == [
        warning,
        overwrite,
    ]
    docs = [ok, unviewed, failed]
    assert exporttext.export_questions(docs, plan(docs, True)) == [
        skipped,
        warning,
        overwrite,
    ]
    assert exporttext.export_questions(docs, plan(docs, False, [ok])) == [
        skipped,
        too_long,
        warning,
    ]


def test_output_follows_folders_and_long_paths_are_left_out(tmp_path, monkeypatch):
    """出力先は、出力先のフォルダの下の、開いたフォルダからの相対パスのフォルダ。同じ名前の「_2」は、出力先の
    フォルダごとに付ける。結果と上書きの確認は相対パスで出す。長いパスを使えない PC では、出力先のパスが長すぎる
    ファイルを除き、すべてがそうなら出力できない理由にする"""
    out = tmp_path / "out"
    docs = [
        make_doc(Path("in/scan.pdf"), 1),
        make_doc(Path("in/scan.jpg"), 1),
        make_doc(Path("in/A/scan.pdf"), 1),
        make_doc(Path("in/a/B/scan.pdf"), 1),
        make_doc(Path("in/A/scan.PNG"), 1),
    ]
    for d, folder in zip(docs, ["", "", "A", "a/B", "A"]):
        d.folder = PurePath(folder)
        for st in d.pages:
            st.result, st.viewed = result([auto()]), True
    names = exporttext.output_names(docs, out)
    assert (
        [names[d].relative_to(out).as_posix() for d in docs]
        == [
            "scan_redacted.pdf",
            "scan_redacted_2.pdf",
            "A/scan_redacted.pdf",
            "a/B/scan_redacted.pdf",
            "A/scan_redacted_2.pdf",  # A と a は同じフォルダとして数えない（別のフォルダ a/B）
        ]
    )
    nested = str(Path("A", "scan.pdf"))
    assert exporttext.result_text(docs[2], names[docs[2]], report()).startswith(
        f"✓ {nested} → {Path('A', 'scan_redacted.pdf')}　"
    )
    assert exporttext.overwrite_question([names[docs[3]]], out).endswith(
        f"・{Path('a', 'B', 'scan_redacted.pdf')}"
    )
    assert exporttext.plan_export(docs, out).too_long == []
    monkeypatch.setattr(exporttext, "long_paths", lambda: False)
    long_name = "x" * 200
    deep = make_doc(Path(f"in/{long_name}/scan.pdf"), 1)
    deep.folder = PurePath(long_name)
    for st in deep.pages:
        st.result, st.viewed = result([auto()]), True
    root = Path("/" + "o" * 40)
    plan = exporttext.plan_export([docs[0], deep], root)
    assert [d for d, _ in plan.jobs] == [docs[0]]
    assert [d for d, _ in plan.too_long] == [deep]
    assert exporttext.export_questions([docs[0], deep], plan)[0].startswith(
        "次のファイルは、出力先のパスが長すぎるため出力しません。"
    )
    plan = exporttext.plan_export([deep], root)
    assert plan.error.startswith("出力先のパスが長すぎるため、出力できません。\n・")
    # 上限：出力の途中の一時的な名前を含めて259文字
    assert len(export._temporary(Path("a"), "0" * 8).name) == (
        len("a") + exporttext.TEMPORARY_EXTRA
    )
    base = Path("/" + "d" * 240)
    file_ok = base / ("f" * (259 - 241 - 1 - exporttext.TEMPORARY_EXTRA))
    assert not exporttext.path_too_long(file_ok)
    assert exporttext.path_too_long(file_ok.with_name(file_ok.name + "f"))
    texts = exporttext.export_result(out, [], [], docs, tmp_path / "log", [deep])
    assert texts.warning
    assert (
        f"✗ {deep.label}：出力先のパスが長すぎるため、出力しませんでした。"
        in texts.text
    )


def test_result_warns():
    """1ファイルの結果を「⚠」にするのは、表示していないページがある（削除したページは除く）か、作業ログに書け
    なかったとき。結果の文の記号も同じ"""
    doc = make_doc(Path("a.pdf"), 2)
    for st in doc.pages:
        st.result, st.viewed = result([auto()]), True
    out, rep = Path("a_redacted.pdf"), report()
    assert not exporttext.result_warns(doc, "")
    assert exporttext.result_text(doc, out, rep).startswith("✓ ")
    assert exporttext.result_warns(doc, "PermissionError: 試験")
    assert exporttext.result_text(doc, out, rep, "PermissionError: 試験").startswith(
        "⚠ "
    )
    doc.pages[1].viewed = False
    assert exporttext.result_warns(doc, "")
    assert exporttext.result_text(doc, out, rep).startswith("⚠ ")
    doc.pages[1].deleted = True
    assert not exporttext.result_warns(doc, "")


def test_export_result(tmp_path):
    """結果のダイアログは、1行目に出力先、続けてファイルごとの文、処理できないファイルと全ページを削除したファイルの
    文。「⚠」「✗」のファイルか処理できないファイルがあれば警告にし、確かめるよう促す2行と、作業ログのフォルダを開く
    ボタンを足す。全ページを削除したファイルだけでは警告にしない"""
    ok, _, failed = three_docs(tmp_path)
    deleted = make_doc(tmp_path / "d.pdf", 1, 3)
    deleted.pages[0].deleted = True
    out, logs = tmp_path / "出力", tmp_path / "ログ"
    open_out = ("出力先のフォルダを開く", out, "出力先のフォルダ")
    open_log = ("作業ログのフォルダを開く", logs, "作業ログのフォルダ")
    check = [
        "⚠ や ✗ の付いたファイルを確かめてください。",
        f"作業ログ：{logs / 'worklog.csv'}",
    ]
    r = exporttext.export_result(out, ["✓ a.pdf"], [False], [ok], logs)
    assert r == exporttext.ExportResult(f"出力先：{out}\n✓ a.pdf", False, [open_out])
    r = exporttext.export_result(out, ["✓ a.pdf"], [False], [ok, deleted], logs)
    assert not r.warning and r.folders == [open_out]
    assert r.text.split("\n")[1:] == ["✓ a.pdf", exporttext.all_deleted_text(deleted)]
    r = exporttext.export_result(out, ["⚠ a.pdf"], [True], [ok], logs)
    assert r.warning and r.folders == [open_out, open_log]
    assert r.text.split("\n") == [f"出力先：{out}", "⚠ a.pdf", *check]
    r = exporttext.export_result(out, ["✓ a.pdf"], [False], [ok, failed], logs)
    assert r.warning and r.folders == [open_out, open_log]
    assert r.text.split("\n")[1:] == [
        "✓ a.pdf",
        exporttext.skipped_text(failed),
        *check,
    ]


def test_is_edited():
    assert not review.is_edited([auto()])
    assert review.is_edited([auto(active=False)])  # 欄を解除した
    assert review.is_edited([manual()])


def test_used_forms_and_form_text():
    """作業ログの「使った様式」は、墨消しPDFに入れたページで範囲を使った様式を、版を付けて1回ずつ並べる。
    画面下の「判別：」には、版と、保存の記録があれば保存した日と人も出す"""
    fuyou, hoken = bundled("nta-fuyou-r9"), bundled("nta-hoken-r8")
    user = formstore.FormSpec(
        "u-1", "住宅借入金等特別控除申告書", "u-1-r2.png", (2492, 1773), (),
        revision=2, updated_at="2026-11-07T10:15:00", updated_by="yamada",
        folder="/様式",
    )  # fmt: skip
    doc = make_doc(Path("in/a.pdf"), 4)
    for st, used in zip(doc.pages, [(fuyou,), (user, fuyou), (fuyou,), (hoken,)]):
        st.result = result([], form=used[0].name)
        st.result.used = used
    doc.pages[3].deleted = True  # 削除したページの様式は書かない
    assert exporttext.used_forms_text(doc) == (
        "扶養控除等申告書 (revision 0), 住宅借入金等特別控除申告書 (revision 2)"
    )
    assert reviewtext.form_text(doc.pages[0].result) == "扶養控除等申告書（第0版）"
    assert reviewtext.form_text(doc.pages[1].result) == (
        "住宅借入金等特別控除申告書（第2版、2026-11-07 yamada）"
    )
    # 範囲を使った様式がないページ（様式不明）は、名前だけを出す
    assert reviewtext.form_text(result([], form=formstore.UNKNOWN)) == "様式不明"


def test_pages_without_masks_are_labeled():
    """墨消しする範囲が1つもない、削除していない解析済みのページに「墨消しなし」を付ける"""
    doc = make_doc(Path("a.pdf"), 6)
    other, off, unmasked, deleted, failed, waiting = doc.pages
    other.result = result([])  # 墨消しする範囲のない解析の結果
    off.result = result([], form=forms.HOKEN)  # この PC で使わない様式の帳票（範囲0）
    unmasked.result = result([auto(active=False), manual()])
    unmasked.result.boxes[1].active = False  # 全部の範囲を解除した
    deleted.result, deleted.deleted = result([]), True
    failed.error = "x"
    assert [review.no_mask(st) for st in doc.pages] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert ("墨消しなし", uitext.WARN) in reviewtext.page_parts(other)[1]
    assert ("墨消しなし", uitext.WARN) not in reviewtext.page_parts(deleted)[1]
    waiting.result = result([manual()])  # 手で追加した範囲が1つあれば付けない
    assert not review.no_mask(waiting)
    assert ("墨消しなし 3", uitext.WARN) in reviewtext.doc_parts(doc)[1]
    assert (
        reviewtext.page_messages(other)[-1][0]
        == "墨消しなし　墨消しする範囲がありません"
    )
    failed.error = ""
    failed.result = result([auto()])
    text = exporttext.unviewed_warning([doc])
    assert "　　表示していない「墨消しなし」のページ：1〜3\n" in text
    assert "「墨消しなし」のページは、墨消しする範囲がないまま出力されます。" in text
    for st in doc.pages:
        st.viewed = True
    lines = exporttext.result_text(doc, Path("a_redacted.pdf"), report()).splitlines()
    assert lines[0].startswith("✓ a.pdf")  # 記号は変えない
    assert "　　墨消しする範囲のないページ：1〜3" in lines


def test_unknown_page_labels():
    """様式不明のページ：一覧の行の文と画面下は「様式不明」、ラベルは「墨消しなし」と「⚠ 要確認」。ページの上の帯は
    「⚠ 要確認」の文だけにし、同じことを言う「墨消しなし」の文は出さない"""
    doc = make_doc(Path("a.pdf"), 1)
    st = doc.pages[0]
    st.result = result([], form=formstore.UNKNOWN, note=forms.UNKNOWN_NOTE)
    assert (
        reviewtext.joined(reviewtext.page_parts(st))
        == "1ページ　様式不明　墨消しなし　⚠ 要確認　未表示"
    )
    assert ("帳票：様式不明", uitext.NEUTRAL, "") in reviewtext.status_parts(
        [doc], doc.pages, st
    )
    assert reviewtext.page_messages(st) == [
        (f"⚠ 要確認　{forms.UNKNOWN_NOTE}", uitext.WARN)
    ]
    # 手で範囲を追加すると「墨消しなし」は消え、「⚠ 要確認」は残る
    st.result.boxes.append(manual())
    assert (
        reviewtext.joined(reviewtext.page_parts(st))
        == "1ページ　様式不明　追加 1　⚠ 要確認　未表示"
    )


# ---- 墨消しの画面のボタンと、起動の準備のラベル


def test_actions_follow_pages_and_locks():
    """ボタンを押せるかは、ページの有無・表示しているページの位置と状態・出力中・様式のフォルダを読んでいるあいだで
    決まる（画面のボタンがこの値のとおりになることは、test_forms_screens.py の test_action_states で確かめる）"""
    none = reviewtext.actions([], [], -1, False, None)
    assert none == reviewtext.Actions(
        prev=False,
        next=False,
        turn=False,
        delete_page=False,
        page_deleted=False,
        open=True,
        export_count=0,
        export=False,
    )
    docs = [make_doc(Path("a.pdf"), 1), make_doc(Path("b.pdf"), 1, number=1)]
    pages = [st for d in docs for st in d.pages]
    for st in pages:
        st.result = result([auto()])
    first = reviewtext.actions(docs, pages, 0, False, None)
    assert (first.prev, first.next, first.turn, first.delete_page) == (
        False,
        True,
        True,
        True,
    )
    assert (first.open, first.export_count, first.export) == (True, 2, True)
    last = reviewtext.actions(docs, pages, 1, False, None)
    assert (last.prev, last.next) == (True, False)
    pages[1].result = None  # 解析が終わっていないページは回せず、出力もできない
    waiting = reviewtext.actions(docs, pages, 1, False, None)
    assert (waiting.turn, waiting.delete_page, waiting.export) == (False, True, False)
    assert (
        waiting.export_count == 0
    )  # 全ページの解析が終わるまでは、出力できるファイルを数えない
    pages[1].result = result([auto()])
    pages[1].deleted = True  # 削除したページは回せないが、削除を取り消せる
    deleted = reviewtext.actions(docs, pages, 1, False, None)
    assert (deleted.turn, deleted.delete_page, deleted.page_deleted) == (
        False,
        True,
        True,
    )
    pages[1].deleted = False
    exporting = reviewtext.actions(docs, pages, 1, True, None)
    assert (exporting.turn, exporting.delete_page, exporting.open) == (
        False,
        False,
        False,
    )
    assert not exporting.export
    opening = reviewtext.actions(docs, pages, 1, False, [Path("c.pdf")])
    assert (opening.turn, opening.delete_page, opening.open, opening.export) == (
        False,
        False,
        True,
        False,
    )
    assert reviewtext.editing_locked(False, []) and not reviewtext.editing_locked(
        False, None
    )
    assert reviewtext.can_export(docs, False, None)
    assert reviewtext.export_text(2) == "墨消しPDFを出力（2件）"
    assert reviewtext.export_text(0) == "墨消しPDFを出力"


def test_wait_parts():
    """画面下の、起動の準備と様式のフォルダの読み込みのラベル"""
    assert reviewtext.wait_parts("", True, [], None) == []
    assert reviewtext.wait_parts("理由", False, [], None) == [
        ("✕ 起動の準備ができませんでした。理由", uitext.ERROR, "")
    ]
    assert reviewtext.wait_parts("", False, [], None) == [
        ("起動の準備をしています…", uitext.NEUTRAL, "OCR などを読み込んでいます")
    ]
    assert reviewtext.wait_parts("", False, [Path("a.pdf")], None)[0][2] == (
        "OCR などを読み込んでいます。選んだファイルは、準備が終わりしだい開きます"
    )
    assert reviewtext.wait_parts("", True, [], [Path("a.pdf")]) == [
        (
            "様式のフォルダを読んでいます…",
            uitext.NEUTRAL,
            (
                "読み終わりしだい選んだファイルを開き、今のファイルは閉じます。"
                "そのため、今のファイルは編集できません"
            ),
        )
    ]


def test_help_text():
    text = reviewtext.help_text()
    assert text.startswith(
        "操作の説明\n\n・欄・範囲をクリック：墨消しする・しないを切り替える\n"
    )
    assert text.endswith(
        "・Ctrl+0・Ctrl++・Ctrl+-・Ctrl+ホイール：全体を表示・拡大・縮小"
    )
    assert (
        "\n・枠の角をドラッグ：その角だけを動かす\n・Shift+枠の角をドラッグ：長方形として大きさを変える\n"
        in text
    )
    assert len(reviewtext.HELP_ROWS) == 11
    assert "\n・右のボタンを押しながらドラッグ：ページをスクロールする\n" in text


def test_open_documents_and_failure_text(tmp_path):
    """選ばれたファイルとフォルダを開き、開けなかった理由を集める。開けなかったときの文"""
    png = (
        Path(__file__).resolve().parent
        / "data"
        / "公開様式_2026bun_04_200dpi_グレー.png"
    )
    good = tmp_path / "保険料.png"
    shutil.copy(png, good)
    broken = tmp_path / "壊れた.pdf"
    broken.write_bytes(b"not a pdf")
    opened = review.open_documents(
        [broken, good, good], ocr.Engine.RAPIDOCR, "RapidOCR"
    )
    assert [
        (d.path.name, d.number, len(d.pages), d.stamp is not None) for d in opened.docs
    ] == [("保険料.png", 0, 1, True)]  # 重ねて選ばれたファイルは1つにする
    failed = opened.failed
    assert failed == ["PDFを開けません（壊れている可能性があります）: 壊れた.pdf"]
    assert opened.skipped == []
    assert review.open_failure_text(failed, True) == (
        "次のファイルは開けませんでした。ほかのファイルは開きます。\n" + failed[0]
    )
    assert review.open_failure_text(failed, False) == (
        "次のファイルは開けませんでした。\n" + failed[0]
    )
    empty = tmp_path / "空"
    empty.mkdir()
    assert (
        review.open_documents([empty], ocr.Engine.RAPIDOCR, "RapidOCR")
        == review.Opened()
    )
    assert review.no_files_text() == "開ける PDF・画像のファイルがありませんでした。"


def test_previous_outputs_are_skipped_and_told(tmp_path):
    """フォルダの中の墨消し済みのPDF は開かず、件数を、開けなかったファイルの文・開けるファイルが1つも
    なかったときの文・画面下のラベルで伝える。マウスを重ねると相対パスを10件まで出す"""
    png = (
        Path(__file__).resolve().parent
        / "data"
        / "公開様式_2026bun_04_200dpi_グレー.png"
    )
    folder = tmp_path / "in"
    folder.mkdir()
    shutil.copy(png, folder / "保険料.png")
    for name in ("保険料_redacted.pdf", "保険料_redacted_2.pdf"):
        (folder / name).write_bytes(b"not a pdf")
    opened = review.open_documents([folder], ocr.Engine.RAPIDOCR, "RapidOCR")
    assert [d.path.name for d in opened.docs] == ["保険料.png"]
    assert opened.skipped == ["保険料_redacted.pdf", "保険料_redacted_2.pdf"]
    note = "墨消し済みのPDFを2件除きました。"
    assert review.open_failure_text(["x"], True, opened.skipped) == (
        f"次のファイルは開けませんでした。ほかのファイルは開きます。\nx\n\n{note}"
    )
    assert review.no_files_text(opened.skipped) == (
        f"開ける PDF・画像のファイルがありませんでした。\n{note}"
    )
    doc = opened.docs[0]
    parts = reviewtext.status_parts(
        opened.docs, doc.pages, doc.pages[0], None, opened.skipped
    )
    explain = (
        "名前の末尾が「_redacted」か「_redacted_数字」のファイルは、墨消し済みのPDFとみなして"
        "開きません（「ファイルを開く」で選べば開けます）。"
    )
    assert parts[-1] == (
        "墨消し済みのPDFを2件除きました",
        uitext.NEUTRAL,
        f"{explain}\n保険料_redacted.pdf\n保険料_redacted_2.pdf",
    )
    names = [f"{i}_redacted.pdf" for i in range(12)]
    assert review.skipped_tip(names) == "\n".join([explain, *names[:10], "ほか 2件"])
    assert review.skipped_tip(names[:10]) == "\n".join([explain, *names[:10]])
    i18n.set_language(i18n.EN)
    try:
        assert review.skipped_tip(names).endswith("\n9_redacted.pdf\n2 more")
        assert review.no_files_text(names[:1]) == (
            "There were no PDF or image files that could be opened.\n"
            "Skipped 1 already redacted PDF."
        )
        assert (
            reviewtext.status_parts(opened.docs, doc.pages, doc.pages[0], None, names)[
                -1
            ][0]
            == "Skipped 12 already redacted PDFs"
        )
    finally:
        i18n.set_language(i18n.JA)


# ---------------------------------------------------------------- 英語の画面


def test_english_texts_of_redaction_screen():
    """英語の画面では、一覧の行・画面の下・ページの上の帯・操作の説明・出力のボタンの文を英語にする。様式不明は
    画面の言語の文にし、様式の名前と手で追加した範囲の名前の値は変えない"""
    i18n.set_language(i18n.EN)
    doc = make_doc(Path("a.pdf"), 2)
    first, second = doc.pages
    assert reviewtext.joined(reviewtext.page_parts(second)) == "Page 2  Analyzing…"
    first.result = result([auto(), auto(active=False), manual()], confident=False)
    assert reviewtext.joined(reviewtext.page_parts(first)) == (
        "Page 1  扶養控除等申告書  Fields 2 (off 1)  Added 1  ⚠ Orientation  Not viewed"
    )
    assert first.result.boxes[2].kind == KIND_MANUAL
    first.viewed = True
    first.result = result([], form=formstore.UNKNOWN, note="note")
    assert (
        reviewtext.joined(reviewtext.page_parts(first))
        == "Page 1  Unknown form  No redaction  ⚠ Review  ✓"
    )
    assert reviewtext.page_messages(first) == [("⚠ Review: note", uitext.WARN)]
    second.result = result([auto()])
    assert (
        reviewtext.joined(reviewtext.doc_parts(doc))
        == "a.pdf  Viewed 1/2  No redaction 1  ⚠ Needs review"
    )
    second.result = result([])
    second.viewed = True
    assert reviewtext.page_messages(second) == [
        ("No redaction: there are no redaction areas", uitext.WARN)
    ]
    second.deleted = True
    assert (
        reviewtext.joined(reviewtext.doc_parts(doc))
        == "a.pdf  Viewed 1/1  Deleted 1  No redaction 1  ⚠ Needs review"
    )
    assert reviewtext.page_messages(second)[0][0].startswith(
        "Deleted page: not included in the redacted PDF."
    )
    second.deleted, second.result = False, None
    loaded = formstore.Loaded(
        None, forms=list(bundled_forms()), off=frozenset({"nta-hoken-r8"})
    )
    assert [
        t for t, _, _ in reviewtext.status_parts([doc], doc.pages, first, loaded)
    ] == [
        "File 1/1, page 1/2",
        "Form: Unknown form",
        "Analyzing: 1 page left",
        "Forms in use: 2",
        "✕ Forms not used on this PC: 1",
    ]
    help_lines = reviewtext.help_text().splitlines()
    assert help_lines[:3] == [
        "How to use",
        "",
        "- Click a field or area: Turn redaction on or off",
    ]
    assert "- Ctrl+Z, Ctrl+Y: Undo, redo" in help_lines
    assert "- Drag with the right mouse button held down: Scroll the page" in help_lines
    assert reviewtext.export_text(0) == "Export redacted PDF"
    assert reviewtext.export_text(1) == "Export redacted PDF (1 file)"
    assert reviewtext.export_text(3) == "Export redacted PDF (3 files)"
    assert reviewtext.wait_parts("", False, ["a"], None) == [
        (
            "Preparing…",
            uitext.NEUTRAL,
            "Loading OCR and other components. The selected files open when preparation is finished",
        )
    ]
    assert (
        reviewtext.wait_parts("E", True, None, None)[0][0] == "✕ Preparation failed. E"
    )
    assert review.open_failure_text(["x"], True) == (
        "The following files could not be opened. The other files will be opened.\nx"
    )


def test_english_texts_of_export():
    """英語の画面では、出力の前の確認・出力の結果・番号の並べ方を英語にする"""
    i18n.set_language(i18n.EN)
    assert exporttext.ranges_text([1, 3, 4, 5, 8, 9]) == "1, 3–5, 8–9"
    doc = make_doc(Path("a.pdf"), 3)
    first, second, third = doc.pages
    first.result, first.viewed = result([auto()]), True
    second.result = result([auto()], note="note")
    third.result = result([])
    warning = exporttext.unviewed_warning([doc])
    assert warning.splitlines() == [
        "The following files have pages that you have not viewed.",
        "Pages not viewed are exported without their redaction areas being checked.",
        (
            '"⚠ Review" pages have fields whose redaction areas may not match the fields, or sheets that are '
            "not redacted because their forms could not be identified."
        ),
        '"No redaction" pages are exported without any redaction areas.',
        "",
        "- a.pdf",
        '    "⚠ Review" pages not viewed: 2',
        '    "No redaction" pages not viewed: 3',
        "",
        "Export without checking the pages not viewed?",
    ]
    out = Path("/o/a_redacted.pdf")
    text = exporttext.result_text(doc, out, report(masked=1), "PermissionError: x")
    assert text.splitlines() == [
        "⚠ a.pdf → a_redacted.pdf  1 area redacted",
        "    Pages not viewed: 2–3 (exported without being checked)",
        "    Pages without redaction areas: 3",
        "    Could not write to the work log (PermissionError: x). If a program has worklog.csv open, close it.",
        (
            "    Pages to review: 2 (pages with fields whose redaction areas may not match the fields, or with "
            "sheets that are not redacted because their forms could not be identified)"
        ),
    ]
    assert exporttext.result_text(doc, out, report(masked=3)).startswith(
        "⚠ a.pdf → a_redacted.pdf  3 areas"
    )
    assert exporttext.failure_text(doc, "E") == "✗ a.pdf: could not be exported. E"
    assert (
        exporttext.all_deleted_text(doc)
        == "- a.pdf: not exported, because all pages were deleted."
    )
    r = exporttext.export_result(Path("/o"), ["r"], [True], [doc], Path("/log"))
    assert r.text.splitlines() == [
        "Output folder: /o",
        "r",
        'Check the files marked "⚠" or "✗".',
        "Work log: /log/worklog.csv",
    ]
    assert r.folders == [
        ("Open output folder", Path("/o"), "the output folder"),
        ("Open work log folder", Path("/log"), "the work log folder"),
    ]
    assert exporttext.overwrite_question([out], Path("/o")) == (
        "Files with the same names already exist in the output folder. Overwrite them?\n- a_redacted.pdf"
    )


def test_depth_choice_and_opening_texts():
    """深さを選ぶダイアログの選択肢：数え終わった深さは件数、上限で止めた深さは「n件以上」、数えている深さは
    「数えています…」。ファイルを開く途中の文：フォルダをたどる途中は見つけた数、開く途中は開いた数と全体の数"""
    counts = folders.Counts((12, 73, 10_000), 1, False, False)
    texts = [
        reviewtext.depth_text(label, counts, d) for d, label in reviewtext.DEPTH_CHOICES
    ]
    assert texts == [
        "直下だけ：12件",
        "1つ下のフォルダまで：85件",
        "2つ下まで：数えています…",
        "3つ下まで：数えています…",
        "すべて：数えています…",
    ]
    stopped = folders.Counts((12, 73, 10_000), 1, False, True)
    assert reviewtext.depth_text("すべて", stopped, None) == "すべて：10,085件以上"
    complete = folders.Counts((12, 73), 1, True, False)
    assert reviewtext.depth_text("3つ下まで", complete, 3) == "3つ下まで：85件"
    assert reviewtext.opening_text(1234, -1) == "フォルダを読んでいます（1,234件）…"
    assert reviewtext.opening_text(120, 3400) == "開いています（120／3,400件）"
    i18n.set_language(i18n.EN)
    try:
        assert (
            reviewtext.depth_text("直下だけ", counts, 0) == "This folder only: 12 files"
        )
        assert reviewtext.depth_text("すべて", stopped, None) == (
            "All levels: 10,085 or more files"
        )
        assert (
            reviewtext.depth_text("2つ下まで", counts, 2)
            == "Down to 2 levels: counting…"
        )
        assert reviewtext.opening_text(1, -1) == "Reading folders (1 file found)…"
        assert reviewtext.opening_text(1, 2) == "Opening (1/2 files)"
    finally:
        i18n.set_language(i18n.JA)


def test_documents_show_paths_from_opened_folder(tmp_path):
    """左の一覧のファイルの行は、開いたフォルダからの相対パス（直下のファイルは名前だけ）。開けなかったファイルの
    理由も相対パスで示す"""
    png = (
        Path(__file__).resolve().parent
        / "data"
        / "公開様式_2026bun_04_200dpi_グレー.png"
    )
    folder = tmp_path / "in"
    (folder / "A" / "B").mkdir(parents=True)
    shutil.copy(png, folder / "1.png")
    shutil.copy(png, folder / "A" / "B" / "2.png")
    (folder / "A" / "壊れた.pdf").write_bytes(b"x")
    opened = review.open_documents([folder], ocr.Engine.RAPIDOCR, "RapidOCR", None)
    assert [reviewtext.doc_parts(d)[0] for d in opened.docs] == [
        "1.png",
        str(Path("A", "B", "2.png")),
    ]
    assert opened.failed == [
        f"PDFを開けません（壊れている可能性があります）: {Path('A', '壊れた.pdf')}"
    ]
    progress = []
    assert (
        review.open_inputs(
            folders.collect([folder], None),
            ocr.Engine.RAPIDOCR,
            "RapidOCR",
            lambda n, total: progress.append((n, total)),
            lambda: len(progress) > 2,
        )
        is None
    )
    assert progress == [(0, 3), (1, 3), (2, 3)]  # 3件目を開く前に取り消した


def test_long_paths_reads_one_byte_result(monkeypatch):
    """長いパスを使えるかは、Windows では ntdll の RtlAreLongPathsEnabled の1バイトの戻り値で決める（上位のバイトに
    残った値を読まない）。関数がなければ使えないとみなす。ほかの OS では、いつも使える"""
    import ctypes
    import types

    assert exporttext.long_paths()  # この環境（Linux）

    class Function:
        restype = ctypes.c_int

        def __init__(self, raw):
            self.raw = raw

        def __call__(self):
            return self.raw & 0xFF if self.restype is ctypes.c_ubyte else self.raw

    calls = []

    def windll(name, raw):
        calls.append(name)
        return types.SimpleNamespace(RtlAreLongPathsEnabled=Function(raw))

    monkeypatch.setattr(exporttext, "os", types.SimpleNamespace(name="nt"))
    for raw, expected in [(0x100, False), (0x101, True), (0, False)]:
        monkeypatch.setattr(
            ctypes, "WinDLL", lambda name, raw=raw: windll(name, raw), raising=False
        )
        assert exporttext.long_paths() is expected
    assert calls == ["ntdll"] * 3

    def missing(name):
        raise OSError("ない")

    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    assert exporttext.long_paths() is False
