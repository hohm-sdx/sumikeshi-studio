# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式のデータの読み込みと、様式のフォルダの決め方のテスト（画面も OCR も使わない）"""

import dataclasses
import json
import os

import pytest
from userforms import (
    bundled,
    bundled_forms,
    install_bundled,
    png_bytes,
    write_form,
    write_png,
)

from sumikeshi_studio import __version__, formsave, formstore, i18n
from sumikeshi_studio.formstore import FolderChoice


def load(folder):
    return formstore.load(FolderChoice(folder, formstore.ORIGIN_USER))


def test_bundled_forms_are_read_from_forms_folder(tmp_path):
    """配布用パッケージの3様式は、利用者の様式と同じ形の .json で、様式のフォルダに置くと名前の順に読める"""
    install_bundled(tmp_path)
    loaded = load(tmp_path)
    assert loaded.problems == []
    forms = loaded.active
    assert [(f.id, f.name, len(f.fields), f.revision) for f in forms] == [
        ("nta-hoken-r8", "保険料控除申告書", 1, 0),
        ("nta-kihai-r8", "基・配・特・所の申告書", 4, 0),
        ("nta-fuyou-r9", "扶養控除等申告書", 10, 0),
    ]
    assert [(f.title_words, f.title_excludes) for f in forms] == [
        (("保険料控除申",), ("保険料控除証明書",)),
        (
            (
                "基礎控除申",
                "配偶者控除等申",
                "特定親族特別控除申",
                "所得金額調整控除申",
            ),
            (),
        ),
        (("扶養控除等",), ()),
    ]
    assert all(f.image_path() == tmp_path / f"{f.id}.png" for f in forms)
    assert forms[2].log_label() == "扶養控除等申告書 (revision 0)"
    assert [dataclasses.replace(f, folder="") for f in forms] == [
        dataclasses.replace(f, folder="") for f in bundled_forms()
    ]


def test_only_user_and_nta_files_are_read(tmp_path):
    """様式のフォルダで読むのは u-… と、年分の付いた nta-…<年分>.json だけ。識別子の形に合わないファイル（x-….json）と、
    年分のない nta-….json は読まず、一覧にも出ない"""
    for form_id in ("x-fuyou", "nta-fuyou", "nta-fuyou-r10", "u-aaaa"):
        write_form(tmp_path, form_id, f"様式 {form_id}")
    loaded = load(tmp_path)
    assert [f.id for f in loaded.forms] == ["nta-fuyou-r10", "u-aaaa"]
    assert loaded.problems == []


def test_folder_is_chosen_on_launcher_or_default(tmp_path, monkeypatch):
    """様式のフォルダは、起動の画面で指定したフォルダ、なければ既定の場所（%LOCALAPPDATA%\\SumikeshiStudio\\forms）。
    既定の場所は、なければ作る。指定したフォルダは作らない（つながらない共有フォルダを作らないため）"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    default = tmp_path / "appdata" / "SumikeshiStudio" / "forms"
    assert formstore.choose_folder("") == FolderChoice(
        default, formstore.ORIGIN_DEFAULT
    )
    loaded = formstore.load(formstore.choose_folder(""))
    assert default.is_dir() and loaded.error == "" and loaded.folder == default
    assert loaded.summary_text() == "様式：使う0"
    chosen = formstore.choose_folder(str(tmp_path / "共有"))
    assert chosen == FolderChoice(tmp_path / "共有", formstore.ORIGIN_USER)
    assert formstore.load(chosen).error.startswith("様式のフォルダを読めないため")
    assert not (tmp_path / "共有").exists()


def test_forms_are_used_in_name_order(tmp_path):
    """様式は名前の文字の符号の順に並ぶ（配布用パッケージの様式と画面から登録した様式を分けない）"""
    install_bundled(tmp_path)
    write_form(tmp_path, "u-bbbb", "住宅借入金等特別控除申告書", base="nta-hoken-r8")
    write_form(tmp_path, "u-aaaa", "医療費控除の明細書", base="nta-kihai-r8")
    write_form(tmp_path, "nta-fuyou-r9", "扶養控除等申告書", fields=3, revision=2)
    (tmp_path / "メモ.json").write_text(
        "{}", encoding="utf-8"
    )  # 様式でないファイルは読まない
    loaded = load(tmp_path)
    assert loaded.blocking_text() == "" and loaded.problems == []
    # 「住」U+4F4F・「保」U+4FDD・「医」U+533B・「基」U+57FA・「扶」U+6276 の順
    assert [(f.id, len(f.fields)) for f in loaded.active] == [
        ("u-bbbb", 1),
        ("nta-hoken-r8", 1),
        ("u-aaaa", 4),
        ("nta-kihai-r8", 4),
        ("nta-fuyou-r9", 3),
    ]
    assert loaded.summary_text() == "様式：使う5"
    user, fuyou = loaded.active[0], loaded.active[4]
    assert fuyou.log_label() == "扶養控除等申告書 (revision 2)"
    assert user.log_label() == "住宅借入金等特別控除申告書 (revision 1)"
    assert (
        user.detail_label() == "住宅借入金等特別控除申告書（第1版、2026-11-07 yamada）"
    )
    assert user.image_path() == tmp_path / "u-bbbb-r1.png"


def test_forms_not_used_on_this_terminal_are_left_out(tmp_path):
    """この端末で使わない様式は判別に使わない。読めなくても、ファイルを開くのを止めない。
    画面下には数だけを出す"""
    install_bundled(tmp_path)
    write_form(tmp_path, "u-aaaa", "住宅借入金等特別控除申告書", base="nta-hoken-r8")
    write_form(tmp_path, "u-cccc", "読めない様式", copy_image=False)
    off = {"nta-hoken-r8", "u-cccc", "u-gone"}  # なくなった様式の識別子は無視する
    loaded = formstore.load(FolderChoice(tmp_path, formstore.ORIGIN_USER), off)
    assert loaded.blocking_text() == ""
    assert [f.id for f in loaded.active] == ["u-aaaa", "nta-kihai-r8", "nta-fuyou-r9"]
    assert loaded.off_names() == ["保険料控除申告書", "読めない様式"]
    assert loaded.summary_text() == "様式：使う3・この PC で使わない2"
    # 使う様式が読めなければ、開かない
    assert load(tmp_path).blocking_text().startswith("次の様式を読めないため")


def test_unreadable_folder_blocks_opening(tmp_path):
    loaded = load(tmp_path / "ない")
    text = loaded.blocking_text()
    assert text.startswith("様式のフォルダを読めないため、ファイルを開けません。")
    assert str(tmp_path / "ない") in text and "共有フォルダにつながっているか" in text


def test_unreadable_forms_block_opening(tmp_path):
    """使う様式のファイルを1つでも読めなければ、ファイルを開かない。名前（読めなければファイル名）と理由を示す"""
    (tmp_path / "u-broken.json").write_text("{ 書きかけ", encoding="utf-8")
    write_form(tmp_path, "u-noimage", "画像のない様式", copy_image=False)
    write_form(tmp_path, "u-big", "大きすぎる様式", copy_image=False)
    write_png(tmp_path / "u-big-r1.png", 9000, 600)
    write_form(tmp_path, "u-size", "大きさの合わない様式", copy_image=False)
    write_png(tmp_path / "u-size-r1.png", 100, 100)
    write_form(tmp_path, "u-unknown", "様式不明")  # 様式不明のページの名前
    write_form(tmp_path, "u-newer", "新しい形の様式", format=formstore.FORMAT + 1)
    write_form(tmp_path, "u-wrongid", "識別子の違う様式", id="u-other")
    write_form(tmp_path, "nta-kihai-r8", "基・配・特・所の申告書", base="nta-kihai-r8",
               fields=0, extra_key=1)  # fmt: skip
    json_path = tmp_path / "nta-kihai-r8.json"
    data = json.loads(json_path.read_text("utf-8"))
    data["fields"] = [{"name": "幅のない範囲", "rect": [0, 0, 0, 10]}]
    json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    loaded = load(tmp_path)
    reasons = {p.file: p.reason for p in loaded.problems}
    assert reasons == {
        "nta-kihai-r8.json": "範囲（fields）が正しくありません",
        "u-big.json": "見本の画像（u-big-r1.png）が大きすぎます（縦横の上限は8000画素）",
        "u-broken.json": "ファイルの中身が様式の形になっていません",
        "u-newer.json": (
            f"新しいバージョンのツールで保存された様式です（フォーマットの番号 {formstore.FORMAT + 1}）。"
            "ツールを新しいバージョンに更新してください。"
        ),
        "u-noimage.json": "見本の画像（u-noimage-r1.png）を読めません",
        "u-size.json": "見本の画像の大きさが、様式の情報（size）と合いません",
        "u-unknown.json": "「様式不明」は様式の名前に使えません",
        "u-wrongid.json": "識別子（id）がファイル名と合いません",
    }
    text = loaded.blocking_text()
    assert text.startswith("次の様式を読めないため、ファイルを開けません。")
    assert (
        "・画像のない様式（u-noimage.json）：見本の画像（u-noimage-r1.png）を読めません"
        in text
    )
    assert "・u-broken.json（u-broken.json）：" in text
    assert loaded.forms == []


# ---------------------------------------------------------------- 保存


def change_rows(folder):
    import csv

    with (folder / formsave.CHANGE_LOG).open(encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def new_user_form(folder, name="住宅借入金等特別控除申告書"):
    base = bundled("nta-hoken-r8")
    spec = formstore.FormSpec(
        formsave.new_user_id(), name, "", base.size, base.fields,
        title_words=("住宅借入金等特別控除申",),
    )  # fmt: skip
    png = base.image_path().read_bytes()
    return formsave.save(folder, spec, png, formsave.OP_REGISTER, "yamada")


def test_save_writes_new_revision_and_keeps_old_in_history(tmp_path):
    """保存するたびに版を1つ増やし、見本の画像は版ごとの名前で書く。前の版は「history」に移す"""
    first = new_user_form(tmp_path)
    assert first.revision == 1 and first.image == f"{first.id}-r1.png"
    assert first.updated_by == "yamada" and first.folder == str(tmp_path)
    assert formsave.saved_revision(tmp_path, first.id).revision == 1
    renamed = dataclasses.replace(first, name="住宅借入金等特別控除申告書（2年目）")
    second = formsave.save(tmp_path, renamed, None, formsave.OP_EDIT, "suzuki")
    assert second.revision == 2 and (tmp_path / second.image).is_file()
    history = tmp_path / formsave.HISTORY_DIR
    assert sorted(p.name for p in history.iterdir()) == [
        f"{first.id}-r1.json",
        f"{first.id}-r1.png",
    ]
    assert not (tmp_path / first.image).exists()
    (loaded,) = load(tmp_path).forms
    assert (loaded.name, loaded.revision, loaded.updated_by) == (
        "住宅借入金等特別控除申告書（2年目）",
        2,
        "suzuki",
    )
    rows = change_rows(tmp_path)
    assert rows[0] == formsave.CHANGE_FIELDS
    assert [r[1:] for r in rows[1:]] == [
        ["yamada", "register", "住宅借入金等特別控除申告書", "1", "1"],
        ["suzuki", "edit", "住宅借入金等特別控除申告書（2年目）", "2", "1"],
    ]
    # 様式のファイルとして読まない一時的なファイルは残らない
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_later_saver_sees_who_saved_first(tmp_path):
    """2人が同じ版から編集を始めたとき、後の人は、先に保存した人と日時を確かめてから上書きする"""
    first = new_user_form(tmp_path)  # 2人とも第1版から編集を始める
    formsave.save(tmp_path, dataclasses.replace(first, name="Aさんの名前"), None,
                   formsave.OP_EDIT, "tanaka")  # fmt: skip
    saved = formsave.saved_revision(tmp_path, first.id)
    assert saved.revision == 2 != first.revision
    assert saved.conflict_text().startswith("tanakaさんが（20")
    assert saved.conflict_text().endswith("）に保存しています。上書きしますか？")
    third = formsave.save(tmp_path, dataclasses.replace(first, name="Bさんの名前"), None,
                           formsave.OP_EDIT, "sato")  # fmt: skip
    assert third.revision == 3
    history = sorted(p.name for p in (tmp_path / formsave.HISTORY_DIR).iterdir())
    assert history == [f"{first.id}-r{n}.{e}" for n in (1, 2) for e in ("json", "png")]


def fuyou_in(folder):
    return {f.id: f for f in load(folder).forms}["nta-fuyou-r9"]


def test_edit_and_delete_bundled_and_user_forms(tmp_path):
    """配布用パッケージの様式も、画面から登録した様式と同じく、編集すると版を進め、削除すると「history」に移す。版の付いた
    ファイル名は、識別子の年分（-r9）を版と取り違えない"""
    user = new_user_form(tmp_path)
    install_bundled(tmp_path)
    fuyou = fuyou_in(tmp_path)
    edited = formsave.save(tmp_path, dataclasses.replace(fuyou, fields=fuyou.fields[:2]),
                            None, formsave.OP_EDIT, "yamada")  # fmt: skip
    assert (edited.id, edited.revision, edited.image) == (
        "nta-fuyou-r9",
        1,
        "nta-fuyou-r9-r1.png",
    )
    history = sorted(p.name for p in (tmp_path / formsave.HISTORY_DIR).iterdir())
    assert history == ["nta-fuyou-r9-r0.json", "nta-fuyou-r9.png"]
    assert [len(f.fields) for f in load(tmp_path).active] == [1, 1, 4, 2]
    formsave.move_to_history(tmp_path, "nta-fuyou-r9", formsave.OP_DELETE, "yamada")
    formsave.move_to_history(tmp_path, user.id, formsave.OP_DELETE, "yamada")
    assert [f.id for f in load(tmp_path).active] == ["nta-hoken-r8", "nta-kihai-r8"]
    assert not (tmp_path / "nta-fuyou-r9.json").exists()
    assert [r[2] for r in change_rows(tmp_path)[1:]] == [
        "register", "edit", "delete", "delete"
    ]  # fmt: skip
    # 読めないファイルも移せる
    (tmp_path / "u-broken.json").write_text("{", encoding="utf-8")
    formsave.move_to_history(tmp_path, "u-broken", formsave.OP_DELETE, "yamada")
    assert (tmp_path / formsave.HISTORY_DIR / "u-broken.json").is_file()
    assert change_rows(tmp_path)[-1][3] == "u-broken.json"


def test_title_key():
    assert formstore.title_key("扶養控除等（異動）申告書") == "扶養控除等異動申告書"
    assert formstore.title_key(" 「保険料」 控除 ") == "保険料控除"


def test_format_numbers(tmp_path):
    """フォーマットの番号。このツールが扱える番号より大きくても、墨消しに使うのに要る番号
    （usable_from_format）が扱える番号以下なら墨消しに使うが、編集はできない。どちらも大きければ読めない"""
    newer = formstore.FORMAT + 1
    write_form(tmp_path, "u-0001", "今のフォーマットの様式")
    write_form(tmp_path, "u-0002", "項目を足した様式", base="nta-hoken-r8",
               format=newer, usable_from_format=1, new_item="知らない項目")  # fmt: skip
    write_form(
        tmp_path, "u-0003", "意味を変えた様式", format=newer, usable_from_format=newer
    )
    loaded = load(tmp_path)
    forms = {f.id: f for f in loaded.forms}
    assert forms["u-0001"].editable and forms["u-0001"].format == formstore.FORMAT
    assert forms["u-0002"].format == newer and not forms["u-0002"].editable
    assert "u-0002" in [f.id for f in loaded.active]  # 墨消しに使う
    (problem,) = loaded.problems
    assert problem.file == "u-0003.json" and problem.newer
    assert problem.reason == (
        f"新しいバージョンのツールで保存された様式です（フォーマットの番号 {newer}）。"
        "ツールを新しいバージョンに更新してください。"
    )


def test_saved_json_has_format_and_tool_version(tmp_path, monkeypatch):
    """保存した .json には、このツールのフォーマットの番号と、保存したツールのバージョンを書く。古いフォーマットの
    様式を保存すると、このツールのフォーマットになる"""
    saved = new_user_form(tmp_path)
    data = json.loads((tmp_path / f"{saved.id}.json").read_text("utf-8"))
    now = formstore.FORMAT
    assert (data["format"], data["usable_from_format"]) == (now, 1)
    version = f"{__version__} (no version.txt)"
    assert data["saved_with"] == version == saved.saved_with
    assert load(tmp_path).forms[0].saved_with == version
    # フォーマットの番号が1つ大きいツールに見立てる（今のフォーマットの様式は、読めて編集でき、保存すると上がる）
    monkeypatch.setattr(formstore, "FORMAT", now + 1)
    old = load(tmp_path).forms[0]
    assert old.format == now and old.editable
    formsave.save(tmp_path, dataclasses.replace(old, name="保存し直した"), None,
                   formsave.OP_EDIT, "yamada")  # fmt: skip
    data = json.loads((tmp_path / f"{saved.id}.json").read_text("utf-8"))
    assert (data["format"], data["usable_from_format"]) == (now + 1, 1)


# ---- 枠の線の記録

TB, LR = formstore.TOP_BOTTOM, formstore.LEFT_RIGHT


def test_bundled_forms_record_frames():
    """配布用パッケージの3様式は、すべての欄に上下・左右の枠の線があるとして記録している"""
    for spec in bundled_forms():
        assert spec.format == formstore.FORMAT
        assert all(f.frame == (TB, LR) for f in spec.fields)


def test_frame_is_saved_and_read(tmp_path):
    """墨消しする範囲の枠の線の組（frame）を保存し、読み直すと同じになる。消す範囲には書かない"""
    base = bundled("nta-hoken-r8").fields[0]
    fields = tuple(
        formstore.Field(name, base.x, base.y + 50 * i, base.w, base.h, frame)
        for i, (name, frame) in enumerate(
            [("両方", (TB, LR)), ("上下", (TB,)), ("左右", (LR,)), ("なし", ())]
        )
    )
    spec = dataclasses.replace(
        new_user_form(tmp_path),
        fields=fields,
        erase=(formstore.Field("消す範囲1", 10, 10, 50, 50),),
    )
    formsave.save(tmp_path, spec, None, formsave.OP_EDIT, "yamada")
    data = json.loads((tmp_path / f"{spec.id}.json").read_text("utf-8"))
    assert [f["frame"] for f in data["fields"]] == [[TB, LR], [TB], [LR], []]
    assert "frame" not in data["erase"][0]
    again = {f.id: f for f in load(tmp_path).forms}[spec.id]
    assert [f.frame for f in again.fields] == [(TB, LR), (TB,), (LR,), ()]


def test_forms_without_frame_record(tmp_path):
    """枠の線の記録のない様式（前のフォーマット）は、識別子によらず枠なしとする"""
    write_form(tmp_path, "u-0001", "記録のない登録した様式", format=1)
    write_form(tmp_path, "nta-fuyou-r9", "扶養控除等申告書", format=1)
    forms = load(tmp_path).forms
    fields = [f for spec in forms for f in spec.fields]
    assert len(forms) == 2 and all(f.frame is None for f in fields)
    assert {formstore.frame_pairs(f) for f in fields} == {()}
    framed = bundled("nta-fuyou-r9").fields[0]
    assert formstore.frame_pairs(framed) == (TB, LR)


def test_previous_version_uses_frame_form_for_masking(tmp_path):
    """保存した様式は、枠の線の記録があっても、墨消しに使うのに要るフォーマットの番号（usable_from_format）を1に
    する（前のバージョン（フォーマット1）のツールでも墨消しに使える。番号の扱いは test_format_numbers で確かめる）"""
    spec = new_user_form(tmp_path)
    data = json.loads((tmp_path / f"{spec.id}.json").read_text("utf-8"))
    assert data["usable_from_format"] == 1
    assert all("frame" in f for f in data["fields"])


def test_broken_frame_record(tmp_path):
    write_form(tmp_path, "u-0001", "枠の線の記録が正しくない様式")
    path = tmp_path / "u-0001.json"
    data = json.loads(path.read_text("utf-8"))
    data["fields"][0]["frame"] = "top_bottom"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (problem,) = load(tmp_path).problems
    assert problem.reason == "範囲（fields）の枠の線（frame）が正しくありません"


# ---- 見本の罫線


def test_bundled_forms_have_lines():
    """配布用パッケージの3様式は、塗る前の作業画像から読んだ見本の罫線を持つ（線は用紙の中で、端は小さい順）"""
    for spec in bundled_forms():
        h, v = spec.lines
        assert len(h) > 50 and len(v) > 50
        width, height = spec.size
        assert all(0 <= s[0] <= height and 0 <= s[1] < s[2] <= width for s in h)
        assert all(0 <= s[0] <= width and 0 <= s[1] < s[2] <= height for s in v)


def test_lines_are_saved_and_read(tmp_path):
    """見本の罫線を保存し、読み直すと同じになる。持たない様式は lines を書かず、読むと None"""
    lines = (((100.5, 10, 500, 3),), ((200.0, 50, 400, 2), (300.5, 50, 400, 2)))
    spec = dataclasses.replace(new_user_form(tmp_path), lines=lines)
    formsave.save(tmp_path, spec, None, formsave.OP_EDIT, "yamada")
    data = json.loads((tmp_path / f"{spec.id}.json").read_text("utf-8"))
    assert data["lines"] == {
        "h": [[100.5, 10, 500, 3]],
        "v": [[200.0, 50, 400, 2], [300.5, 50, 400, 2]],
    }
    assert {f.id: f for f in load(tmp_path).forms}[spec.id].lines == lines
    plain = new_user_form(tmp_path, "罫線のない様式")
    data = json.loads((tmp_path / f"{plain.id}.json").read_text("utf-8"))
    assert "lines" not in data
    assert {f.id: f for f in load(tmp_path).forms}[plain.id].lines is None


def test_form_with_too_many_lines_is_saved_without_lines(tmp_path):
    """見本の罫線が、横か縦で上限（MAX_LINES）を超える様式は、罫線を持たない様式として保存する（罫線ごと保存すると、
    読むときに読めない様式になる）。上限ちょうどの様式は、罫線ごと保存して読める"""
    many = tuple((10.0 + 0.25 * i, 10, 500, 1) for i in range(formstore.MAX_LINES + 1))
    for name, lines, kept in (
        ("横が多い様式", (many, ((200.0, 50, 400, 2),)), False),
        ("縦が多い様式", (((100.5, 10, 500, 3),), many), False),
        ("上限ちょうどの様式", (many[:-1], ((200.0, 50, 400, 2),)), True),
    ):
        spec = dataclasses.replace(new_user_form(tmp_path, name), lines=lines)
        saved = formsave.save(tmp_path, spec, None, formsave.OP_EDIT, "yamada")
        loaded = load(tmp_path)
        assert loaded.problems == [] and name in [f.name for f in loaded.active]
        read = {f.id: f for f in loaded.forms}[spec.id]
        assert (read.lines is not None) == (saved.lines is not None) == kept, name
        data = json.loads((tmp_path / f"{spec.id}.json").read_text("utf-8"))
        assert ("lines" in data) == kept, name


WRONG_LINES = "見本の罫線（lines）が正しくありません"
OUTSIDE_LINES = "見本の罫線（lines）が用紙の外にあるか、正しくありません"


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        ({"h": []}, WRONG_LINES),  # 縦の線がない
        ([[10, 0, 100, 1]], WRONG_LINES),
        ({"h": "線", "v": []}, WRONG_LINES),
        ({"h": [[10, 0, 100, 1]] * (formstore.MAX_LINES + 1), "v": []}, WRONG_LINES),
        ({"h": [[10, 0, 100]], "v": []}, WRONG_LINES),
        ({"h": [[10, 0.5, 100, 1]], "v": []}, WRONG_LINES),
        ({"h": [[True, 0, 100, 1]], "v": []}, WRONG_LINES),
        ({"h": [[float("nan"), 0, 100, 1]], "v": []}, WRONG_LINES),
        ({"h": [[10, 0, 100, 1]], "v": [[10, 0, 99999, 1]]}, OUTSIDE_LINES),
        ({"h": [[99999, 0, 100, 1]], "v": []}, OUTSIDE_LINES),
        ({"h": [[10, 100, 100, 1]], "v": []}, OUTSIDE_LINES),
        ({"h": [[10, 0, 100, 0]], "v": []}, OUTSIDE_LINES),
        ({"h": [[10, 0, 100, formstore.MAX_LINE_WIDTH + 1]], "v": []}, OUTSIDE_LINES),
    ],
)
def test_broken_lines(tmp_path, lines, reason):
    """見本の罫線の記録が正しくない様式は、読めない様式とする（共有フォルダの中の、ほかの人が書いたファイルを読むため）"""
    write_form(tmp_path, "u-0001", "罫線の記録が正しくない様式", lines=lines)
    (problem,) = load(tmp_path).problems
    assert problem.reason == reason


def test_line_match_is_saved_and_read(tmp_path):
    """罫線の並びを判別に使うかと、罫線の一致度の下限を保存し、読み直すと同じになる。新しく登録した様式は、使うで
    0.60。この項目のない様式（フォーマット3以下で保存した様式）は、使うにして、下限は、配布用パッケージの様式の
    識別子なら様式ごとの値（扶養 0.65・保険料 0.45・基配 0.45）、ほかは 0.60"""
    saved = new_user_form(tmp_path)
    data = json.loads((tmp_path / f"{saved.id}.json").read_text("utf-8"))
    assert data["line_match"] == {"use": True, "min": 0.6}
    changed = dataclasses.replace(saved, line_match_use=False, line_match_min=0.85)
    formsave.save(tmp_path, changed, None, formsave.OP_EDIT, "yamada")
    data = json.loads((tmp_path / f"{saved.id}.json").read_text("utf-8"))
    assert data["line_match"] == {"use": False, "min": 0.85}
    read = {f.id: f for f in load(tmp_path).forms}[saved.id]
    assert (read.line_match_use, read.line_match_min) == (False, 0.85)
    old = tmp_path / "old"
    old.mkdir()
    write_form(old, "u-0001", "項目のない様式", format=3)
    for form_id in ("nta-fuyou-r9", "nta-hoken-r8", "nta-kihai-r8"):
        write_form(old, form_id, f"前のバージョンで入れた {form_id}", format=3)
    loaded = load(old)
    assert loaded.problems == []
    assert {f.id: (f.line_match_use, f.line_match_min) for f in loaded.forms} == {
        "u-0001": (True, 0.60),
        "nta-fuyou-r9": (True, 0.65),
        "nta-hoken-r8": (True, 0.45),
        "nta-kihai-r8": (True, 0.45),
    }
    assert formstore.FORMAT == 4 and formstore.USABLE_FROM_FORMAT == 1


@pytest.mark.parametrize(
    "value",
    [
        {"use": True},  # 下限がない
        {"min": 0.6},  # 使うかがない
        {"use": "yes", "min": 0.6},
        {"use": True, "min": "0.6"},
        {"use": True, "min": True},
        {"use": True, "min": 0},
        {"use": True, "min": 1.01},
        {"use": True, "min": float("nan")},
        [True, 0.6],
        0.6,
    ],
)
def test_broken_line_match(tmp_path, value):
    """罫線の一致度の下限の記録が正しくない様式は、読めない様式とする（共有フォルダの中の、ほかの人が書いた
    ファイルを読むため）"""
    write_form(tmp_path, "u-0001", "下限の記録が正しくない様式", line_match=value)
    (problem,) = load(tmp_path).problems
    assert problem.reason == "罫線の一致度の下限（line_match）が正しくありません"


def test_bundled_forms_are_in_current_format():
    """配布用パッケージの3様式は、今のフォーマットで、様式ごとの下限を .json に持つ"""
    for form_id, least in formstore.BUNDLED_LINE_MATCH_MIN.items():
        path = formstore.BUNDLED_DIR / f"{form_id}.json"
        data = json.loads(path.read_text("utf-8"))
        assert data["format"] == formstore.FORMAT
        assert data["line_match"] == {"use": True, "min": least}


# ---------------------------------------------------------------- 版の番号、保存と削除の失敗、壊れたファイル


def test_save_after_delete_does_not_reuse_revision(tmp_path):
    """削除した後に、同じ識別子の様式をもう一度入れて保存しても、前に使った版の番号（画像の名前）を使わない"""
    install_bundled(tmp_path)
    fuyou = fuyou_in(tmp_path)
    first = formsave.save(tmp_path, dataclasses.replace(fuyou, fields=fuyou.fields[:2]),
                           None, formsave.OP_EDIT, "yamada")  # fmt: skip
    assert first.image == "nta-fuyou-r9-r1.png"
    formsave.move_to_history(tmp_path, "nta-fuyou-r9", formsave.OP_DELETE, "yamada")
    install_bundled(tmp_path)
    fuyou = fuyou_in(tmp_path)
    again = formsave.save(tmp_path, dataclasses.replace(fuyou, fields=fuyou.fields[:3]),
                           None, formsave.OP_EDIT, "suzuki")  # fmt: skip
    assert (again.revision, again.image) == (2, "nta-fuyou-r9-r2.png")


def test_existing_image_name_is_not_overwritten(tmp_path):
    """新しい版の名前の画像がすでにあれば（ほかの人がほぼ同時に保存した）、上書きせずに版を進める"""
    first = new_user_form(tmp_path)
    placed = tmp_path / f"{first.id}-r2.png"
    placed.write_bytes("ほかの人の画像".encode())
    second = formsave.save(tmp_path, dataclasses.replace(first, name="直した名前"), None,
                            formsave.OP_EDIT, "suzuki")  # fmt: skip
    assert (second.revision, second.image) == (3, f"{first.id}-r3.png")
    assert placed.read_bytes() == "ほかの人の画像".encode()


def test_failure_after_saving_is_a_warning(tmp_path, monkeypatch):
    """.json を置き換えた後の処理（history に移す・変更の記録に書く）の失敗は、保存の成功として扱い、理由を返す"""
    first = new_user_form(tmp_path)

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", formsave.CHANGE_LOG)

    monkeypatch.setattr(formsave, "append_change", locked)
    warnings = []
    second = formsave.save(tmp_path, dataclasses.replace(first, name="直した名前"), None,
                            formsave.OP_EDIT, "suzuki", warnings)  # fmt: skip
    assert formsave.saved_revision(tmp_path, first.id).revision == second.revision == 2
    assert [w.split("（PermissionError")[0] for w in warnings] == [
        "様式の変更の記録（changes.csv）に書けませんでした"
    ]


def test_failure_before_replacing_leaves_no_file(tmp_path, monkeypatch):
    """.json を置き換える前に失敗したら、書いた画像と一時ファイルを消して、例外を出す"""
    first = new_user_form(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())

    def deny(a, b):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(PermissionError):
        formsave.save(tmp_path, dataclasses.replace(first, name="直した名前"), None,
                       formsave.OP_EDIT, "suzuki")  # fmt: skip
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_delete_moves_json_first(tmp_path, monkeypatch):
    """削除は .json を先に「history」に移す。画像を移せなくても、様式は一覧から消え、理由を返す"""
    user = new_user_form(tmp_path)
    real = os.replace

    def deny_png(a, b):
        if str(a).endswith(".png"):
            raise PermissionError(13, "Permission denied")
        return real(a, b)

    monkeypatch.setattr(os, "replace", deny_png)
    warnings = []
    formsave.move_to_history(tmp_path, user.id, formsave.OP_DELETE, "yamada", warnings)
    assert not (tmp_path / f"{user.id}.json").exists()
    assert (tmp_path / formsave.HISTORY_DIR / f"{user.id}-r1.json").is_file()
    assert [w.split("（")[0] for w in warnings] == [
        "見本の画像を「history」に移せませんでした"
    ]
    assert load(tmp_path).problems == [] and load(tmp_path).forms == []


def _spec_text(form_id: str, **change) -> str:
    base = json.loads((formstore.BUNDLED_DIR / "nta-hoken-r8.json").read_text("utf-8"))
    spec = dict(base, id=form_id, image=f"{form_id}-r1.png", revision=1)
    spec.update(change)
    return json.dumps(spec, ensure_ascii=False)


def test_broken_json_is_one_unreadable_form(tmp_path):
    """壊れた .json は、その様式だけを読めない様式にし、ほかの様式は読む。入れ子の深い .json・長い整数・NaN・
    Infinity・用紙の外の範囲・制御文字の名前・大きすぎるファイルも、読めない様式にする"""
    hoken = bundled("nta-hoken-r8")
    f0 = hoken.fields[0]
    rect = [f0.x, f0.y, f0.w, f0.h]
    cases = {
        "u-deep": _spec_text("u-deep")[:-1]
        + ', "x": '
        + "[" * 100000
        + "]" * 100000
        + "}",
        "u-longint": _spec_text("u-longint")[:-1] + ', "x": ' + "9" * 5000 + "}",
        "u-control": _spec_text("u-control", name="様式\u0007"),
        "u-nan": _spec_text("u-nan").replace(f"[{rect[0]},", "[NaN,", 1),
        "u-inf": _spec_text("u-inf").replace(f", {rect[2]},", ", Infinity,", 1),
        "u-outside": _spec_text(
            "u-outside", fields=[{"name": "外", "rect": [99999, 10, 50, 20]}]
        ),
        "u-huge": _spec_text("u-huge", pad="x" * formstore.MAX_SPEC_BYTES),
    }
    for form_id, text in cases.items():
        (tmp_path / f"{form_id}.json").write_text(text, encoding="utf-8")
        (tmp_path / f"{form_id}-r1.png").write_bytes(hoken.image_path().read_bytes())
    write_form(tmp_path, "u-good", "読める様式")
    loaded = load(tmp_path)
    reasons = {p.form_id: p.reason for p in loaded.problems}
    assert reasons == {
        "u-deep": "ファイルを読めません（RecursionError）",
        "u-longint": "ファイルを読めません（ValueError）",
        "u-control": "名前（name）に使えない文字（制御文字）があります",
        "u-nan": "範囲（fields）が正しくありません",
        "u-inf": "範囲（fields）が正しくありません",
        "u-outside": "範囲（fields）が用紙の外にあります（外）",
        "u-huge": "ファイルが大きすぎます（上限は1MB）",
    }
    assert "読める様式" in [f.name for f in loaded.forms]


def test_template_png_is_checked_to_the_end(tmp_path, monkeypatch):
    """見本の画像は、最後まで読んで確かめる（途中で切れた・CRC が違う・IEND のない・大きすぎる画像は読めない様式）。
    確かめるのはプロセスの中で画像ごとに1回だけ"""
    good = bundled("nta-hoken-r8").image_path().read_bytes()
    broken = bytearray(good)
    broken[100] ^= 0xFF  # 最初の IDAT の中の1バイトを変える（CRC が合わなくなる）
    cases = {
        "u-cut": good[: len(good) // 2],
        "u-crc": bytes(broken),
        "u-noend": good[:-12],
        "u-huge": png_bytes(10, 10) + b"\0" * formstore.MAX_IMAGE_BYTES,
    }
    for form_id, data in cases.items():
        (tmp_path / f"{form_id}.json").write_text(_spec_text(form_id), encoding="utf-8")
        (tmp_path / f"{form_id}-r1.png").write_bytes(data)
    write_form(tmp_path, "u-good", "読める様式", base="nta-hoken-r8")
    checked = []
    real = formstore.check_png
    monkeypatch.setattr(formstore, "check_png", lambda d: checked.append(1) or real(d))
    loaded = load(tmp_path)
    reasons = {p.form_id: p.reason for p in loaded.problems}
    assert reasons == {
        "u-cut": "見本の画像（u-cut-r1.png）が途中で切れているか、壊れています",
        "u-crc": "見本の画像（u-crc-r1.png）が壊れています（CRC が合いません）",
        "u-noend": "見本の画像（u-noend-r1.png）が途中で切れているか、壊れています",
        "u-huge": "見本の画像が大きすぎます（ファイルの上限は16MB）",
    }
    assert "読める様式" in [f.name for f in loaded.forms]
    first = len(checked)
    load(tmp_path)
    assert (
        len(checked) - first == 3
    )  # 読めた画像は確かめ直さず、読めなかった3つだけ確かめ直す


def test_new_spec_from_editor_boxes():
    """範囲を描く画面の範囲から、保存する様式を作る。座標は整数に丸め、幅と高さは1以上にする。消す範囲は erase に
    入れ、枠の線の組は持たない。版とフォルダは、編集を始めた様式から引き継ぐ"""
    from sumikeshi_studio.boxes import Box, Source

    boxes = [
        Box(10.4, 20.6, 30.2, 0.3, "本人の個人番号", Source.AUTO, frame=("上下",)),
        Box(1, 2, 3, 4, "消す", Source.ERASE, frame=("左右",)),
        Box(5, 6, 7, 8, "範囲1", Source.MANUAL),
    ]
    new = formsave.new_spec("u-0009", "住宅", None, (80, 120), boxes, ("住宅",), ())
    assert [(f.name, f.x, f.y, f.w, f.h, f.frame) for f in new.fields] == [
        ("本人の個人番号", 10, 21, 30, 1, ("上下",)),
        ("範囲1", 5, 6, 7, 8, ()),
    ]
    assert [(f.name, f.frame) for f in new.erase] == [("消す", None)]
    assert (new.id, new.image, new.size, new.revision, new.folder) == (
        "u-0009",
        "",
        (80, 120),
        0,
        "",
    )
    user = dataclasses.replace(
        bundled("nta-hoken-r8"), id="u-0001", revision=2, folder="/様式"
    )
    edited = formsave.new_spec("u-0001", "住宅", user, user.size, boxes, (), ("年調",))
    assert (edited.image, edited.revision, edited.folder) == (user.image, 2, "/様式")
    # 見本の罫線も引き継ぐ（範囲だけの編集。登録と置き換えでは、保存の前に読み直す）。新しい様式は持たない
    assert edited.lines == user.lines and user.lines is not None
    assert new.lines is None


def test_english_reasons_and_labels(tmp_path):
    """英語の画面では、読めない様式の理由・ファイルを開かない理由・画面下の様式の数・判別した様式の名前を英語にする。
    PNG の確認の文は、見本の画像の名前の後につなぐ"""
    i18n.set_language(i18n.EN)
    (tmp_path / "u-broken.json").write_text("{", encoding="utf-8")
    write_form(tmp_path, "u-noimage", "画像のない様式", copy_image=False)
    write_form(tmp_path, "u-big", "大きすぎる様式", copy_image=False)
    write_png(tmp_path / "u-big-r1.png", 9000, 600)
    write_form(tmp_path, "u-unknown", "様式不明")
    loaded = load(tmp_path)
    reasons = {p.file: p.reason for p in loaded.problems}
    assert reasons == {
        "u-big.json": "The sample image (u-big-r1.png) is too large (the limit is 8000 pixels per side)",
        "u-broken.json": "The file contents are not in the form format",
        "u-noimage.json": "Cannot read the sample image (u-noimage-r1.png)",
        "u-unknown.json": '"様式不明" cannot be used as a form name',
    }
    lines = loaded.blocking_text().splitlines()
    assert (
        lines[0] == "Files cannot be opened because the following forms cannot be read."
    )
    assert lines[1] == "- 大きすぎる様式 (u-big.json): " + reasons["u-big.json"]
    assert lines[5] == f"Forms folder: {tmp_path}"
    assert lines[6].startswith('On the start screen, click "Edit forms"')
    off = dataclasses.replace(loaded, off=frozenset({"u-big"}))
    assert off.summary_text() == "Forms: 0 in use, 1 not used on this PC"
    assert formstore.Loaded().summary_text() == "Forms: 0 in use"
    spec = dataclasses.replace(bundled("nta-fuyou-r9"), folder="/f", revision=3,
                               updated_at="2026-11-07T10:20:30", updated_by="yamada")  # fmt: skip
    assert spec.detail_label() == "扶養控除等申告書 (revision 3, 2026-11-07 yamada)"


# ---------------------------------------------------------------- 様式のフォルダを替えるときのコピー


def test_count_forms_and_overlaps(tmp_path):
    """様式のファイルの数は has_forms と同じ数え方（読めない .json も数え、識別子の形に合わない .json は数えない）。フォルダが
    ないときは 0。一方のフォルダがもう一方の中にあるか（同じフォルダも含む）"""
    install_bundled(tmp_path / "a")
    (tmp_path / "a" / "u-broken.json").write_text("{", encoding="utf-8")
    write_form(tmp_path / "a", "x-fuyou", "識別子の形に合わない様式")
    assert formstore.count_forms(tmp_path / "a") == 4
    assert formstore.count_forms(tmp_path / "ない") == 0
    assert formstore.overlaps(tmp_path / "a", tmp_path / "a" / "history")
    assert formstore.overlaps(tmp_path / "a" / "x", tmp_path / "a")
    assert formstore.overlaps(tmp_path / "a", tmp_path / "a")
    assert not formstore.overlaps(tmp_path / "a", tmp_path / "ab")


def test_copy_forms_copies_only_forms_and_their_images(tmp_path):
    """コピーするのは、前のフォルダの直下の様式の .json と、それぞれが指す見本の画像だけ。history・changes.csv・
    ほかのファイルと、どの .json も指さない画像はコピーしない。コピーした様式は、前のフォルダと同じ版で読める"""
    old, new = tmp_path / "前", tmp_path / "新"
    install_bundled(old)
    fuyou = {f.id: f for f in load(old).forms}["nta-fuyou-r9"]
    formsave.save(old, dataclasses.replace(fuyou, fields=fuyou.fields[:2]), None,
                  formsave.OP_EDIT, "yamada")  # fmt: skip
    (old / "資料.txt").write_text("ほかの資料", encoding="utf-8")
    (old / "u-broken.json").write_text("{ 読めない", encoding="utf-8")
    copied = formsave.copy_forms(old, new)
    assert copied == [
        "nta-fuyou-r9.json",
        "nta-hoken-r8.json",
        "nta-kihai-r8.json",
        "u-broken.json",
    ]
    assert sorted(p.name for p in new.iterdir()) == sorted(
        [*copied, "nta-fuyou-r9-r1.png", "nta-hoken-r8.png", "nta-kihai-r8.png"]
    )
    for name in copied:
        assert (new / name).read_bytes() == (old / name).read_bytes()
    loaded = load(new)
    assert [(f.id, f.revision) for f in loaded.forms] == [
        ("nta-hoken-r8", 0),
        ("nta-kihai-r8", 0),
        ("nta-fuyou-r9", 1),
    ]
    assert [p.file for p in loaded.problems] == [
        "u-broken.json"
    ]  # 新しいフォルダでも読めない様式


def test_copy_forms_uses_image_moved_to_history(tmp_path):
    """.json を読んだ後に、ほかの PC が保存して画像が history に移ったときは、history の同じ名前の画像を使う。
    .json が指す画像がどこにもないときは、.json だけをコピーする"""
    old, new = tmp_path / "前", tmp_path / "新"
    old.mkdir()
    write_form(old, "u-0001", "移った画像の様式")
    (old / formsave.HISTORY_DIR).mkdir()
    (old / "u-0001-r1.png").rename(old / formsave.HISTORY_DIR / "u-0001-r1.png")
    write_form(old, "u-0002", "画像のない様式", copy_image=False)
    assert formsave.copy_forms(old, new) == ["u-0001.json", "u-0002.json"]
    assert sorted(p.name for p in new.iterdir()) == [
        "u-0001-r1.png",
        "u-0001.json",
        "u-0002.json",
    ]
    assert [f.id for f in load(new).forms] == ["u-0001"]


def test_copy_forms_does_not_overwrite_form_saved_meanwhile(tmp_path):
    """コピーの途中で、ほかの PC が新しいフォルダに同じ名前の様式を保存していたら上書きせず、このコピーで書いた
    .json を消して例外を出す"""
    old, new = tmp_path / "前", tmp_path / "新"
    install_bundled(old)
    new.mkdir()
    (new / "nta-kihai-r8.json").write_text("ほかの PC の様式", encoding="utf-8")
    with pytest.raises(FileExistsError):
        formsave.copy_forms(old, new)
    assert sorted(p.name for p in new.glob("*.json")) == ["nta-kihai-r8.json"]
    assert (new / "nta-kihai-r8.json").read_text("utf-8") == "ほかの PC の様式"


def test_copy_forms_failure_leaves_no_form(tmp_path, monkeypatch):
    """途中で失敗したら、このコピーで書いた .json を消して例外を出す（新しいフォルダは様式のない状態に戻る）"""
    import shutil

    old, new = tmp_path / "前", tmp_path / "新"
    install_bundled(old)
    real, calls = shutil.copyfile, []

    def fail_second(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(shutil, "copyfile", fail_second)
    with pytest.raises(PermissionError):
        formsave.copy_forms(old, new)
    assert not formstore.has_forms(new)
