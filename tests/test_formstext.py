# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""起動の画面と様式の画面に出す、様式の一覧と様式の操作の文と条件（formstext.py）のテスト
（画面の部品は使わない）"""

import dataclasses
from pathlib import Path

from userforms import bundled, install_bundled, write_form

from sumikeshi_studio import formsave, formstext, formstore, i18n, uitext
from sumikeshi_studio.formstore import FolderChoice


def user_folder(folder):
    """配布用パッケージの3様式（扶養控除等申告書は編集したもの）、登録した様式、読めない様式のファイルを置いた様式のフォルダの、
    読んだ結果"""
    install_bundled(folder)
    write_form(folder, "nta-fuyou-r9", "扶養控除等申告書", fields=3, revision=2)
    write_form(folder, "u-aaaa", "医療費控除の明細書", base="nta-kihai-r8")
    (folder / "u-broken.json").write_text("{", encoding="utf-8")
    return formstore.load(FolderChoice(folder, formstore.ORIGIN_USER))


def test_form_rows_status_and_saved_text(tmp_path):
    """様式の一覧の行は、読めた様式（名前の順）のあとに読めないファイルを並べる。状態のラベルは、使える様式・この PC
    で使わない様式・新しいバージョンで保存した様式・読めないファイルで変わる。起動の画面の名前の下には保存の情報を
    出し、読めないファイルと保存の記録のない様式は出さない"""
    loaded = user_folder(tmp_path)
    rows = formstext.form_rows(loaded)
    assert [(i, s is not None, p is not None) for i, s, p in rows] == [
        ("nta-hoken-r8", True, False),
        ("u-aaaa", True, False),
        ("nta-kihai-r8", True, False),
        ("nta-fuyou-r9", True, False),
        ("u-broken", False, True),
    ]
    hoken, user, _, edited = (r[1] for r in rows[:4])
    problem = rows[4][2]
    assert formstext.form_status(hoken) == ("✓ 問題なし", uitext.GOOD)
    assert formstext.form_status(hoken, off=True) == (
        "✕ この PC では使わない",
        uitext.ERROR,
    )
    newer = dataclasses.replace(user, format=formstore.FORMAT + 1)
    assert formstext.form_status(newer) == (
        "✓ 使える（新しいバージョンで保存。このバージョンでは編集できません）",
        uitext.GOOD,
    )
    assert formstext.form_status(None, problem) == (
        "✕ 読めません（ファイルの中身が様式の形になっていません）",
        uitext.ERROR,
    )
    assert formstext.saved_text(edited) == "2026-11-07 yamada が保存"
    assert formstext.saved_text(user) == "2026-11-07 yamada が保存"
    # 配布用パッケージのファイルのまま（保存の記録がない）
    assert formstext.saved_text(hoken) == ""
    assert formstext.saved_text(None) == ""
    assert formstext.form_cells(hoken, None)[:4] == [
        "保険料控除申告書",
        "第0版",
        "",
        "",
    ]
    assert formstext.form_cells(edited, None) == [
        "扶養控除等申告書",
        "第2版",
        "2026-11-07 10:15",
        "yamada",
        "3",
        "0",
        "、".join(edited.title_words),
        "✓ 問題なし",
    ]
    assert formstext.form_cells(None, problem) == [
        "u-broken.json",
        *[""] * 6,
        "✕ 読めません（ファイルの中身が様式の形になっていません）",
    ]


def test_load_failed_keeps_folder_only_when_given():
    """様式を読む処理が例外で終わったときの結果。読もうとした様式のフォルダを渡すと、その場所と出どころを持つ"""
    failed = formstext.load_failed(OSError("つながりません"))
    assert failed.error == "様式を読めませんでした。つながりません"
    assert failed.folder is None and failed.forms == []
    choice = FolderChoice(Path("/共有"), formstore.ORIGIN_USER)
    failed = formstext.load_failed("x", choice)
    assert (failed.folder, failed.origin) == (Path("/共有"), formstore.ORIGIN_USER)


def test_launcher_conditions(tmp_path):
    """起動の画面の、墨消しの画面を開けるか・一覧の下の文・件数の文・準備の状態のラベル"""
    install_bundled(tmp_path / "good")
    good = formstore.load(FolderChoice(tmp_path / "good", formstore.ORIGIN_USER))
    blocked = user_folder(tmp_path / "blocked")
    broken = formstore.Loaded(error="様式のフォルダを読めません。")
    assert formstext.can_blackout(good, False)
    assert not formstext.can_blackout(good, True)  # 読んでいる
    assert not formstext.can_blackout(None, False)  # 読む前
    assert not formstext.can_blackout(blocked, False)  # 使う様式を読めない
    assert not formstext.can_blackout(broken, False)  # 様式のフォルダを読めない
    assert formstext.status_message(good, False, "") == ""
    assert formstext.status_message(None, False, "") == ""
    assert formstext.status_message(good, True, "") == "様式のフォルダを読んでいます…"
    assert formstext.status_message(broken, False, "") == "様式のフォルダを読めません。"
    assert formstext.status_message(blocked, False, "") == blocked.blocking_text()
    assert formstext.status_message(good, False, "理由") == (
        "起動の準備ができませんでした。理由"
    )
    assert formstext.status_message(broken, False, "理由") == (
        "起動の準備ができませんでした。理由\n様式のフォルダを読めません。"
    )
    assert formstext.summary_text(good, False, 3) == (
        "3件のうち3件を使います。" + formstext.TIME_NOTE + formstext.SUMMARY_NOTE
    )
    assert formstext.summary_text(good, True, 3) == ""
    assert formstext.summary_text(None, False, 0) == ""
    assert formstext.ready_status("理由", True) == (
        "✕ 起動の準備ができませんでした",
        uitext.ERROR,
    )
    assert formstext.ready_status("", True) == ("✓ 準備ができました", uitext.GOOD)
    assert formstext.ready_status("", False) == ("準備しています…", uitext.NEUTRAL)


def test_no_forms_notice(tmp_path):
    """様式のフォルダを読めて、様式も読めない様式のファイルも1つもないときは、ファイルは開けるが、どのページも
    様式不明になることを一覧の下に示す。「使う」をすべて外したとき・読めない様式だけのときは示さない"""
    (tmp_path / "空").mkdir()
    empty = formstore.load(FolderChoice(tmp_path / "空", formstore.ORIGIN_USER))
    assert formstext.can_blackout(empty, False)
    assert formstext.status_message(empty, False, "") == (
        "様式のフォルダに様式がありません。このままファイルを開くと、どのページも「様式不明」になり、墨消しする欄が"
        "自動では決まりません。「サンプルの様式を入れる」で、国税庁の様式を入れられます。ほかの帳票は、"
        "「様式を編集」の「新しい様式を登録」で登録できます。"
    )
    assert formstext.no_forms(empty) and not formstext.no_forms(None)
    assert formstext.status_message(empty, True, "") == "様式のフォルダを読んでいます…"
    assert formstext.status_message(empty, False, "理由").startswith(
        "起動の準備ができませんでした。理由\n様式のフォルダに様式がありません。"
    )
    install_bundled(tmp_path / "使わない")
    off = formstore.load(
        FolderChoice(tmp_path / "使わない", formstore.ORIGIN_USER),
        {"nta-fuyou-r9", "nta-hoken-r8", "nta-kihai-r8"},
    )
    assert off.active == () and formstext.status_message(off, False, "") == ""
    (tmp_path / "読めない").mkdir()
    (tmp_path / "読めない" / "u-broken.json").write_text("{", encoding="utf-8")
    broken = formstore.load(FolderChoice(tmp_path / "読めない", formstore.ORIGIN_USER))
    assert formstext.status_message(broken, False, "") == broken.blocking_text()
    i18n.set_language(i18n.EN)
    assert formstext.status_message(empty, False, "").startswith(
        'The forms folder has no forms. If you open files now, every page becomes "Unknown form"'
    )


def test_list_actions(tmp_path):
    """様式の一覧のボタン（編集・置き換え・削除・登録）を押せるかと、一覧の下の文。削除は、配布用パッケージの様式も画面から
    登録した様式も、読めないファイルもできる。新しいバージョンのツールで保存された様式は、登録のほかは押せない。
    読み書きの途中は、どれも押せず、一覧の下の文を変えない（None）"""
    loaded = user_folder(tmp_path)
    rows = {i: (i, s, p) for i, s, p in formstext.form_rows(loaded)}

    def buttons(row, busy=False, folder=loaded):
        a = formstext.list_actions(*row, busy, folder)
        return (a.edit, a.replace, a.delete, a.register), a.status

    edited = rows["nta-fuyou-r9"]
    newer = (
        edited[0],
        dataclasses.replace(edited[1], format=formstore.FORMAT + 1),
        None,
    )
    newer_file = (
        "u-new",
        None,
        formstore.Problem("u-new.json", "", "理由", newer=True),
    )
    broken_nta = (
        "nta-hoken-r8",
        None,
        formstore.Problem("nta-hoken-r8.json", "", "理由"),
    )
    #                                     編集   置換   削除   登録
    assert buttons((None, None, None)) == ((False, False, False, True), "")
    assert buttons(rows["u-aaaa"]) == ((True, True, True, True), "")
    assert buttons(rows["nta-hoken-r8"]) == ((True, True, True, True), "")
    assert buttons(edited) == ((True, True, True, True), "")
    assert buttons(broken_nta) == ((False, False, True, True), "")
    assert buttons(rows["u-broken"]) == ((False, False, True, True), "")
    assert buttons(newer) == ((False,) * 3 + (True,), formstore.NEWER_FORM)
    assert buttons(newer_file) == ((False,) * 3 + (True,), formstore.NEWER_FORM)
    assert buttons(rows["u-aaaa"], busy=True) == ((False,) * 4, None)
    assert buttons(newer, folder=None) == ((False,) * 3 + (True,), formstore.NEWER_FORM)
    # 様式のフォルダを読めないときの文は、新しいバージョンのツールで保存した様式の文より先に出す
    broken = formstore.Loaded(error="様式のフォルダを読めません。")
    assert buttons(newer, folder=broken)[1] == "様式のフォルダを読めません。"
    assert (
        buttons((None, None, None), folder=broken)[1] == "様式のフォルダを読めません。"
    )


def test_pick_page_status():
    """様式の編集の画面の下の文：解析中はそのことを、用紙のページを選ぶのを待っていて2ページ以上あれば、登録か
    置き換えかに合わせて選び方を出す"""
    analyzing = "ページを解析しています（向きと傾きを直しています）…"
    assert formstext.pick_page_status(True, True, 2) == analyzing
    assert formstext.pick_page_status(True, None, 0) == analyzing
    assert formstext.pick_page_status(False, True, 2) == (
        "様式にするページを選び、「このページを使う」を押してください"
    )
    assert formstext.pick_page_status(False, False, 3) == (
        "新しい用紙にするページを選び、「このページを使う」を押してください"
    )
    assert formstext.pick_page_status(False, True, 1) == ""
    assert formstext.pick_page_status(False, None, 2) == ""


def test_done_with_warnings():
    assert formstext.done_with_warnings("保存しました", ["一", "二"]) == (
        "保存しました。ただし、次のことができませんでした。\n・一\n・二"
    )


def test_unclear_text_without_suggestion():
    """保存の直前に、題名の語の案を作れないとき（登録済みの様式と同じ年分の見本から登録したときなど）は、例を出さずに、
    その様式を編集するよう伝える。案を作れるときは、案と消す行を示す"""
    text = formstext.unclear_text("扶養控除等申告書", "", ())
    assert text == (
        "この様式の見本は、様式『扶養控除等申告書』の見本に類似していて、2つの見本の題名からは"
        "見分けられませんでした。同じ帳票なら、新しく登録せずに、様式『扶養控除等申告書』を編集してください。"
    )
    assert "例：" not in text and "案：" not in text
    text = formstext.unclear_text(
        "扶養控除等申告書", "令和8年分　扶養控除等", ("扶養控除等",)
    )
    assert "1行（案：令和8年分　扶養控除等）を入れてください。" in text
    assert (
        "「扶養控除等」の行は、『扶養控除等申告書』の見本の題名にもあるので消してください。"
        in text
    )
    assert text.endswith("保存しますか？")


def test_similar_and_replace_text():
    """登録を始めるときと置き換えるときの、類似した様式があることを示す文"""
    fuyou, hoken = bundled("nta-fuyou-r9"), bundled("nta-hoken-r8")
    assert formstext.similar_text((fuyou, hoken)) == (
        "選んだページは、登録済みの様式『扶養控除等申告書』の見本に類似しています。"
        "様式『保険料控除申告書』の見本にも類似しています。"
    )
    assert formstext.similar_text((hoken,)) == (
        "選んだページは、登録済みの様式『保険料控除申告書』の見本に類似しています。"
    )
    assert formstext.replace_text(fuyou, hoken) == (
        "選んだページは、置き換える様式『扶養控除等申告書』よりも、様式『保険料控除申告書』の見本に類似しています。"
        "置き換える様式が『扶養控除等申告書』で正しいか確かめてください。"
    )


# ---- 範囲を描く画面の保存の前の確認


def test_save_problem_checks_in_order():
    """保存できない理由は、名前がない → 使えない名前 → 制御文字 → 同じ名前 → 範囲がない、の順に調べる"""
    hoken = bundled("nta-hoken-r8")
    assert formstext.save_problem("", [hoken], False) == "様式の名前を入れてください。"
    assert formstext.save_problem(formstore.UNKNOWN, [], True) == (
        "「様式不明」は様式の名前に使えません。"
    )
    assert formstext.save_problem("a\x07b", [], True) == (
        "様式の名前に使えない文字（制御文字）があります。"
    )
    assert formstext.save_problem(hoken.name, [hoken], False) == (
        "「保険料控除申告書」という名前の様式がすでにあります。別の名前にしてください。"
    )
    assert formstext.save_problem("住宅", [hoken], False) == (
        "墨消しする範囲を1つ以上描いてください。"
    )
    assert formstext.save_problem("住宅", [hoken], True) is None


def test_not_aligned_text_and_save_warnings():
    assert formstext.not_aligned_text(42, 150) == (
        "この画像では位置合わせの手がかりが足りません（元の画像と整合した特徴点：42点。150点以上が要ります）。"
        "保存しません。消す範囲を狭くするか、別のページから登録してください。"
    )
    # 点は足りているが、移した大きさが合わずに成り立たないとき（点が足りないとは言わない）
    assert formstext.not_aligned_text(200, 150) == (
        "この画像では、元の画像と位置合わせした大きさが合いません（元の画像と整合した特徴点：200点）。"
        "保存しません。消す範囲を狭くするか、別のページから登録してください。"
    )
    written = "範囲「本人」「配偶者」に記入があるようです。"
    distorted = (
        "このページは、用紙が歪んで写っているようです。歪んだ画像を見本にすると、"
        "スキャンしたページで範囲が欄からずれることがあります。"
    )
    low = "元のページの解像度は 100dpi です。見本には 150dpi 以上のスキャンを使ってください。"
    # 範囲を描く画面を開く前の警告は、記入・歪み・解像度の順
    assert formstext.page_warnings(True, ["本人", "配偶者"], 100) == [
        written,
        distorted,
        low,
    ]
    assert formstext.page_warnings(False, [], 149.6) == [
        "元のページの解像度は 150dpi です。見本には 150dpi 以上のスキャンを使ってください。"
    ]  # 下限より低ければ、表示は四捨五入した値
    assert formstext.page_warnings(False, [], 150) == []
    assert formstext.page_warnings(False, [], None) == []
    # 保存の前の警告は、描いた範囲の記入だけ
    assert formstext.save_warnings(["本人", "配偶者"], 6000) == [written]
    assert formstext.save_warnings([], 1500) == []
    # 確認の点が 1,500 点未満なら、位置合わせの手がかりが少ないことを知らせる（記入の警告の後に並べる）
    few = (
        "この様式は、文字や線が少なく、位置合わせの手がかりが少ないようです（1499点）。"
        "スキャンの状態によっては、様式不明になることがあります。"
    )
    assert formstext.FEW_POINTS == 1500
    assert formstext.save_warnings([], 1499) == [few]
    assert formstext.save_warnings(["本人", "配偶者"], 1499) == [written, few]


def test_overwrite_question():
    """保存の直前に読み直した版が、編集を始めたときの版と違うときだけ尋ねる"""
    user = dataclasses.replace(
        bundled("nta-hoken-r8"), id="u-0001", revision=2, folder="/様式"
    )
    same = formsave.Saved(2, "2026-11-07T10:15:00", "yamada")
    later = formsave.Saved(3, "2026-11-08T09:00:00", "tanaka")
    assert formstext.overwrite_question(user, same) is None
    assert formstext.overwrite_question(None, None) is None  # 新しい様式で、まだない
    assert formstext.overwrite_question(user, None) == (
        "この様式は、ほかの人が様式のフォルダから外しています（削除の操作など）。"
        "保存し直しますか？"
    )
    assert formstext.overwrite_question(user, later) == later.conflict_text()


# ---------------------------------------------------------------- 英語の画面


def test_english_texts(tmp_path):
    """英語の画面では、様式の一覧と様式の操作の文を英語の表から引き、値・数・区切り・箇条を英語の書き方にする"""
    loaded = user_folder(tmp_path)
    i18n.set_language(i18n.EN)
    rows = formstext.form_rows(loaded)
    spec = {i: s for i, s, _ in rows}
    assert formstext.form_status(spec["nta-hoken-r8"]) == ("✓ OK", uitext.GOOD)
    assert (
        formstext.form_status(spec["u-aaaa"], None, True)[0] == "✕ Not used on this PC"
    )
    problem = rows[-1][2]
    assert formstext.form_status(None, problem)[0].startswith("✕ Cannot read (")
    assert formstext.saved_text(spec["nta-fuyou-r9"]) == "Saved by yamada on 2026-11-07"
    cells = formstext.form_cells(spec["nta-fuyou-r9"], None)
    assert cells[1] == "Revision 2"
    words = dataclasses.replace(spec["u-aaaa"], title_words=("医療費控除", "令和9年分"))
    assert formstext.form_cells(words, None)[6] == "医療費控除, 令和9年分"
    assert (
        formstext.folder_label(Path("/f"), formstore.ORIGIN_USER)
        == "Forms folder: /f (selected folder)"
    )
    assert (
        formstext.folder_label(Path("/f"), formstore.ORIGIN_DEFAULT)
        == "Forms folder: /f (default location)"
    )
    assert formstext.summary_text(loaded, False, 5) == (
        "Using 4 of 5 forms. The more forms you use, the longer the analysis takes. "
        'Documents of forms with "Use" unchecked are not redacted automatically on this PC.'
    )
    one = formstore.Loaded(None, forms=[spec["nta-hoken-r8"]])
    assert formstext.summary_text(one, False, 1).startswith("Using 1 of 1 form. ")
    assert formstext.ready_status("", True) == ("✓ Ready", uitext.GOOD)
    assert formstext.ready_status("E", False)[0] == "✕ Preparation failed"
    assert (
        formstext.status_message(None, False, "OSError: x")
        == "Preparation failed. OSError: x"
    )
    assert formstext.status_message(None, True, "") == "Reading the forms folder…"
    assert formstext.done_with_warnings("Saved", ["a", "b"]) == (
        "Saved. However, the following could not be done:\n- a\n- b"
    )
    text = formstext.unclear_text(
        "扶養控除等申告書", "令和9年分 扶養", ("扶養", "令和9年分")
    )
    assert text.endswith(
        'Delete the lines "扶養", "令和9年分", because they also appear in the title of the sample image of '
        '"扶養控除等申告書". Save anyway?'
    )
    assert (
        '"If the title contains the following words, the page is identified as this form" box'
        in text
    )
    two = formstext.similar_text((spec["nta-fuyou-r9"], spec["u-aaaa"]))
    assert two == (
        'The selected page is similar to the sample image of the registered form "扶養控除等申告書". '
        'It is also similar to the sample image of the form "医療費控除の明細書".'
    )
    three = formstext.similar_text(
        (spec["nta-fuyou-r9"], spec["u-aaaa"], spec["nta-hoken-r8"])
    )
    assert three.endswith(
        'the sample images of the forms "医療費控除の明細書", "保険料控除申告書".'
    )
    assert (
        formstext.save_problem("様式不明", [], True)
        == '"様式不明" cannot be used as a form name.'
    )
    assert formstext.page_warnings(True, ["本人"], 100.4) == [
        'The areas "本人" seem to have entries.',
        (
            "The paper on this page appears to be distorted in the image. If a distorted image is used as the "
            "sample image, the areas may be off the fields on scanned pages."
        ),
        "The resolution of the original page is 100 dpi. Use a scan of 150 dpi or higher for the sample image.",
    ]
    assert formstext.save_warnings([], 443) == [
        (
            "This form has little text and few lines, so there seem to be few cues for alignment (443 points). "
            'Depending on the condition of the scan, pages may become "Unknown form".'
        )
    ]


def test_folder_switch_texts():
    """様式のフォルダを替えるときの、コピーを尋ねる文（変更・既定の場所に戻す）、新しいフォルダの様式を使う文、
    「既定の場所に戻す」の説明、コピーの失敗の文。英語は様式の数で単数と複数を分ける"""
    old, new = Path("/前"), Path("/新")
    assert formstext.copy_question(False, old, new, 3) == (
        "前のフォルダの様式を、選んだフォルダにコピーしますか？",
        (
            "選んだフォルダ（/新）には様式がありません。前のフォルダ（/前）の様式3件をコピーできます。"
            "前のフォルダの様式は消えません。"
        ),
        ("コピーする", "コピーしない", "変更しない"),
    )
    assert formstext.copy_question(True, old, new, 2) == (
        "様式のフォルダを既定の場所に戻しますか？",
        (
            "既定の場所（/新）には様式がありません。今のフォルダ（/前）の様式2件を、既定の場所にコピーできます。"
            "今のフォルダの様式は消えません。"
        ),
        ("コピーして戻す", "コピーせずに戻す", "戻さない"),
    )
    assert formstext.switched_text(old, new, 4) == (
        "選んだフォルダの様式を使います。",
        (
            "選んだフォルダ（/新）には様式が4件あります。前のフォルダ（/前）の様式は消えませんが、この PC では"
            "使わなくなります。"
        ),
    )
    assert formstext.reset_info(new, old, 3) == (
        "既定の場所（/新）の様式3件を使います。今のフォルダ（/前）の様式は消えませんが、この PC では使わなく"
        "なります。"
    )
    assert formstext.reset_info(new, old, 0) == (
        "既定の場所：/新。今のフォルダ（/前）の様式は消えませんが、この PC では使わなくなります。"
    )
    i18n.set_language(i18n.EN)
    text, info, buttons = formstext.copy_question(False, old, new, 1)
    assert text == "Copy the forms in the previous folder to the selected folder?"
    assert info == (
        "The selected folder (/新) has no forms. You can copy the 1 form in the previous folder (/前). "
        "The form in the previous folder is not deleted."
    )
    assert buttons == ("Copy", "Don't copy", "Keep current folder")
    text, info, buttons = formstext.copy_question(True, old, new, 2)
    assert text == "Use the default location for the forms folder?"
    assert info.startswith(
        "The default location (/新) has no forms. You can copy the 2 forms"
    )
    assert buttons == (
        "Copy and use default",
        "Use default without copying",
        "Keep current folder",
    )
    assert formstext.switched_text(old, new, 3) == (
        "The forms in the selected folder will be used.",
        (
            "The selected folder (/新) has 3 forms. The forms in the previous folder (/前) are not deleted, but "
            "they will no longer be used on this PC."
        ),
    )
    assert formstext.reset_info(new, old, 1).startswith(
        "The 1 form in the default location (/新) will be used."
    )
    assert i18n.tr(formstext.COPY_FAILED) == (
        "Could not copy the forms. The forms folder is still the previous folder. Remove the cause, and then "
        "try again."
    )
    assert i18n.tr(formstext.COPYING) == "Copying the forms…"
