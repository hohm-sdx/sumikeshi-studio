# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面に出す文と、ボタンを押せるかの条件

画面の部品（PySide6）を使わないため、画面を起動せずにテストできる。左の一覧のファイルとページの行、画面の下の
ラベル、ページの上の帯の文、操作の説明、ツールバーのボタンを押せるかの条件、起動の準備と様式のフォルダの読み込みの
ラベルを作る。様式の画面の側は formstext.py。重いライブラリを読み込むモジュール（pipeline）は、型の注釈でだけ
読み込む。文を返す関数は、今の画面の言語の文を返す（i18n.tr）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import i18n, uitext
from .formstore import UNKNOWN, display_name  # formstore は標準ライブラリだけを読み込む
from .i18n import tr
from .review import (
    NO_MASK,
    SKIPPED_TEXT,
    Document,
    PageState,
    count_boxes,
    export_targets,
    no_mask,
    no_mask_pages,
    review_pages,
    skipped_tip,
)
from .uitext import ERROR, GOOD, NEUTRAL, WARN

if TYPE_CHECKING:
    from . import pipeline
    from .folders import Counts
    from .formstore import Loaded


# ---------------------------------------------------------------- 一覧と画面下の表示の文


def doc_parts(d: Document) -> tuple[str, list[tuple[str, str]]]:
    """一覧のファイルの行：文と、並べるラベル（文, 種類）。ページの数は、削除したページを除いて数える
    （確認の対象でないため）"""
    labels = []
    kept = d.kept
    if not kept:
        labels.append((tr("全ページ削除（出力しない）"), NEUTRAL))
    elif d.failed:
        labels.append((tr("✕ 処理できないページあり"), ERROR))
    elif not all(st.ready for st in kept):
        labels.append((tr("解析中…"), NEUTRAL))
    else:
        viewed = sum(st.viewed for st in kept)
        labels.append(
            (
                tr("確認 {viewed}／{total}", viewed=viewed, total=len(kept)),
                GOOD if viewed == len(kept) else NEUTRAL,
            )
        )
    if kept and (deleted := len(d.pages) - len(kept)):
        labels.append((tr("削除 {count}", count=deleted), NEUTRAL))
    if none := len(no_mask_pages(d)):
        labels.append((f"{tr(NO_MASK)} {none}", WARN))
    if review_pages(d.pages):
        labels.append((tr("⚠ 要確認あり"), WARN))
    if d.exported:
        labels.append((tr("✓ 出力済み"), GOOD))
    return d.label, labels


def page_parts(st: PageState) -> tuple[str, list[tuple[str, str]]]:
    """一覧のページの行：文と、並べるラベル（文, 種類）"""
    text = tr(uitext.PAGE_NUMBER, number=st.number + 1)
    if st.deleted:
        return text, [(tr("削除（出力しない）"), NEUTRAL)]
    if st.error:
        return text, [(tr(uitext.FAILED_LABEL), ERROR)]
    if st.result is None:
        return text, [(tr("解析中…"), NEUTRAL)]
    c = count_boxes([st])
    text += i18n.gap() + display_name(st.result.form)
    labels = []
    if c.auto and c.off:
        labels.append((tr("欄 {auto}（解除 {off}）", auto=c.auto, off=c.off), NEUTRAL))
    elif c.auto:
        labels.append((tr("欄 {auto}", auto=c.auto), NEUTRAL))
    if c.manual:
        labels.append((tr("追加 {count}", count=c.manual), NEUTRAL))
    if no_mask(st):
        labels.append((tr(NO_MASK), WARN))
    if not st.result.orientation_confident:
        labels.append((tr(uitext.ORIENTATION_LABEL), WARN))
    if st.result.note:
        labels.append((tr(uitext.REVIEW_LABEL), WARN))
    labels.append(("✓", GOOD) if st.viewed else (tr("未表示"), NEUTRAL))
    return text, labels


def joined(parts: tuple[str, list[tuple[str, str]]]) -> str:
    """行の文とラベルの文をつないだもの（一覧の表示の文。読み上げとテストで使う）"""
    text, labels = parts
    return i18n.gap().join([text, *(label for label, _ in labels)])


def form_text(res: pipeline.PageResult) -> str:
    """画面下の「帳票：」の後に出す文。様式の名前と版（formstore.FormSpec.detail_label）"""
    spec = next((s for s in res.used if s.name == res.form), None)
    return spec.detail_label() if spec is not None else display_name(res.form)


def status_parts(
    docs: list[Document],
    pages: list[PageState],
    cur: PageState | None,
    forms: Loaded | None = None,
    skipped: Sequence[str] = (),
) -> list[tuple[str, str, str]]:
    """画面下に並べるラベル（文, 種類, マウスを重ねたときの説明）。cur は表示しているページ。forms は、開いた
    ファイルの解析に使う様式。skipped は、開くときに除いた墨消し済みのPDF（review.Opened.skipped）。表示しているページの
    「⚠ 要確認」などは、ページの上の帯に出す（page_messages）"""
    if cur is None:
        return []
    # 削除したページは、解析を待たず、出力もしないため数えない
    waiting = sum(not st.ready and not st.error and not st.deleted for st in pages)
    errors = sum(bool(st.error) and not st.deleted for st in pages)
    # 削除したページ、処理できないページがあるファイル（出力しない）、出力したファイル（出力し終えた）の
    # ページは数えない（出力した後に解除・追加・削除すると未出力に戻り、また数える）
    unviewed = sum(
        st.ready
        and not st.viewed
        and not st.deleted
        and not st.doc.failed
        and not st.doc.exported
        for st in pages
    )
    doc = cur.doc
    parts = [
        (
            tr(
                "{doc}／{docs}ファイル・{page}／{pages}ページ",
                doc=doc.number + 1,
                docs=len(docs),
                page=cur.number + 1,
                pages=len(doc.pages),
            ),
            NEUTRAL,
            "",
        )
    ]
    if cur.result is not None and not cur.deleted:
        parts.append((tr("帳票：{form}", form=form_text(cur.result)), NEUTRAL, ""))
    # OCR の名前は出さない（使う OCR は RapidOCR だけのため。作業ログの「OCR」の列には書く）
    if waiting:
        parts.append((tr("解析中：残り {count}ページ", count=waiting), NEUTRAL, ""))
    if errors:
        parts.append(
            (
                tr("✕ 処理できなかったページ：{count}", count=errors),
                ERROR,
                tr("そのページを削除しないと、そのファイルは出力しません"),
            )
        )
    if unviewed:
        parts.append(
            (
                tr("まだ表示していないページ：{count}", count=unviewed),
                WARN,
                tr("このまま出力すると、出力の前に警告が出ます"),
            )
        )
    if len(docs) > 1:
        done = sum(d.exported for d in docs)
        parts.append(
            (
                tr("出力済み：{done}／{count}ファイル", done=done, count=len(docs)),
                NEUTRAL,
                "",
            )
        )
    if skipped:
        parts.append(
            (tr(SKIPPED_TEXT, count=len(skipped)), NEUTRAL, skipped_tip(skipped))
        )
    if forms is not None:
        parts.extend(forms_parts(forms))
    return parts


def forms_parts(forms: Loaded) -> list[tuple[str, str, str]]:
    """画面下の、使う様式の数。この PC で使わない様式は、名前を並べず数だけを出す（どの様式を使わないかは
    起動の画面の一覧で見る。使わない様式が多いと、名前を並べた文が長くなるため）"""
    parts = [(tr("使う様式：{count}", count=len(forms.active)), NEUTRAL, "")]
    if off := len(forms.off_names()):
        parts.append(
            (
                tr("✕ この PC で使わない様式：{count}", count=off),
                ERROR,
                tr(
                    "起動の画面で「使う」のチェックを外した様式です。その帳票のページは、"
                    "自動で墨消しされません"
                ),
            )
        )
    return parts


def page_messages(cur: PageState | None) -> list[tuple[str, str]]:
    """表示しているページの上の帯に出す文（文, 種類）：削除したページ、向き、要確認"""
    if cur is None:
        return []
    if cur.deleted:
        return [
            (
                tr(
                    "削除したページ　墨消しPDFに入れません。削除を取り消すと元に戻ります"
                ),
                ERROR,
            )
        ]
    if cur.result is None:
        return []
    messages = []
    if not cur.result.orientation_confident:
        messages.append(
            (
                tr("⚠ 向き　向きが正しくないおそれがあります"),
                WARN,
            )
        )
    if cur.result.note:
        messages.append((tr("⚠ 要確認　{note}", note=cur.result.note), WARN))
    # 様式不明のページは、「⚠ 要確認」の文が同じこと（墨消ししていない）を言うので、「墨消しなし」の文は出さない
    # （一覧のラベルは重ねて出す）
    if no_mask(cur) and cur.result.form != UNKNOWN:
        messages.append(
            (
                tr("{label}　墨消しする範囲がありません", label=tr(NO_MASK)),
                WARN,
            )
        )
    return messages


# ---------------------------------------------------------------- 墨消しの画面のボタンと、起動の準備のラベル

# 画面の側は、これらの結果で部品を更新する

# 「操作の説明」のメッセージに出す、操作とすること
HELP_ROWS = [
    ("欄・範囲をクリック", "墨消しする・しないを切り替える"),
    ("何もない所をドラッグ", "範囲を追加する"),
    ("枠の角をドラッグ", "その角だけを動かす"),
    ("Shift+枠の角をドラッグ", "長方形として大きさを変える"),
    ("枠の中をドラッグ", "動かす"),
    ("手で追加した範囲を右クリック", "削除する"),
    ("右のボタンを押しながらドラッグ", "ページをスクロールする"),
    ("左の一覧でページを選んで Delete", "ページを削除する（墨消しPDFに入れない）"),
    ("PgUp・PgDown、一覧で上下の矢印キー", "前・次のページに移る"),
    ("Ctrl+Z・Ctrl+Y", "元に戻す・やり直す"),
    ("Ctrl+0・Ctrl++・Ctrl+-・Ctrl+ホイール", "全体を表示・拡大・縮小"),
]


def help_text() -> str:
    rows = (
        i18n.bullet(tr("{key}：{what}", key=tr(key), what=tr(what)))
        for key, what in HELP_ROWS
    )
    return tr("操作の説明") + "\n\n" + "\n".join(rows)


def editing_locked(exporting: bool, opening) -> bool:
    """今のファイルを編集する操作（範囲の操作・回転・ページの削除・元に戻す）を受け付けない

    出力のあいだと、ファイルを開くために様式のフォルダを読んでいるあいだ（opening は、読み終わったら開くファイル。
    読んでいなければ None。読み終わったら今のファイルを閉じる）。読んでいるあいだに編集した内容が、
    確認なしに消えないようにするため。
    """
    return exporting or opening is not None


def can_export(docs: list[Document], exporting: bool, opening) -> bool:
    """出力できる。様式のフォルダを読み終わってから別のファイルを開くあいだは、出力しない
    （開くことを選んだ時点で、今のファイルを破棄することを確かめている。出力の途中で開かないように）"""
    return not exporting and opening is None and bool(export_targets(docs))


# 墨消しの画面の「墨消しPDFを出力」の操作とボタンの文（出力できるファイルの数を付ける前の文）
EXPORT_LABEL = "墨消しPDFを出力"


def export_text(count: int) -> str:
    """「墨消しPDFを出力」のボタンの文。出力できるファイルがあれば、その数を出す"""
    if not count:
        return tr(EXPORT_LABEL)
    return tr("{label}（{count}件）", label=tr(EXPORT_LABEL), count=count)


@dataclass(frozen=True)
class Actions:
    """墨消しの画面のツールバーのボタンを押せるか（「元に戻す」「やり直す」は、元に戻す操作の記録を見るので画面で決める）"""

    prev: bool  # 前のページ
    next: bool  # 次のページ
    turn: bool  # 左・右に90度回す
    delete_page: bool  # このページを削除（削除を取り消す）
    page_deleted: (
        bool  # 表示しているページを削除した（ボタンの文を「削除を取り消す」にする）
    )
    open: bool  # ファイルを開く・フォルダを開く
    export_count: int  # 出力できるファイルの数
    export: bool  # 墨消しPDFを出力


def actions(
    docs: list[Document], pages: list[PageState], current: int, exporting: bool, opening
) -> Actions:
    """ボタンを押せるか。current は表示しているページ（全ファイルを通した番号。表示していなければ -1）"""
    locked = editing_locked(exporting, opening)
    has = bool(pages)
    cur = pages[current] if has and current >= 0 else None
    return Actions(
        prev=has and current > 0,
        next=has and current < len(pages) - 1,
        turn=bool(cur and cur.ready and not cur.deleted) and not locked,
        delete_page=cur is not None and not locked,
        page_deleted=cur is not None and cur.deleted,
        open=not exporting,
        export_count=len(export_targets(docs)),
        export=can_export(docs, exporting, opening),
    )


OPENING = "ファイルを開いています…"  # 画面下のラベルと、開く途中の進み具合の初めの文


def wait_parts(
    preload_error: str, ready: bool, pending, opening, loading: bool = False
) -> list[tuple[str, str, str]]:
    """画面下に、ほかのラベルより先に並べる、起動の準備と様式のフォルダの読み込みと、ファイルを開く途中のラベル
    （文, 種類, マウスを重ねたときの説明）。pending は、起動の準備が終わったら開くファイル。loading は、様式を
    読み終わってファイルを開いている途中か"""
    waits = []
    if preload_error:
        failed = tr(
            "{failed}。{error}", failed=tr(uitext.PRELOAD_FAILED), error=preload_error
        )
        waits.append((f"✕ {failed}", ERROR, ""))
    elif not ready:
        if pending:
            tip = tr(
                "OCR などを読み込んでいます。選んだファイルは、準備が終わりしだい開きます"
            )
        else:
            tip = tr("OCR などを読み込んでいます")
        waits.append((tr("起動の準備をしています…"), NEUTRAL, tip))
    if opening is not None and loading:
        waits.append(
            (
                tr(OPENING),
                NEUTRAL,
                tr(
                    "開き終わりしだい、今のファイルは閉じます。そのため、今のファイルは編集できません"
                ),
            )
        )
    elif opening is not None:
        waits.append(
            (
                tr(uitext.FORMS_READING),
                NEUTRAL,
                tr(
                    "読み終わりしだい選んだファイルを開き、今のファイルは閉じます。"
                    "そのため、今のファイルは編集できません"
                ),
            )
        )
    return waits


# ---------------------------------------------------------------- 開く深さとファイルを開く途中の文

# 深さを選ぶダイアログの選択肢（深さ, 文）。深さは、開いたフォルダを 0 として数える（None はすべて）
DEPTH_CHOICES: tuple[tuple[int | None, str], ...] = (
    (0, "直下だけ"),
    (1, "1つ下のフォルダまで"),
    (2, "2つ下まで"),
    (3, "3つ下まで"),
    (None, "すべて"),
)
DEPTH_QUESTION = (
    "選んだフォルダにはサブフォルダがあります。どのフォルダまでのファイルを開きますか？"
)


def depth_text(label: str, counts: Counts, depth: int | None) -> str:
    """深さを選ぶダイアログの選択肢の文。label は DEPTH_CHOICES の文。数え終わった深さは件数、上限で数えるのを
    止めた深さは「n件以上」、数えている深さは「数えています…」"""
    name = tr(label)
    count = counts.count(depth)
    if counts.exact(depth):
        return tr("{label}：{number}件", label=name, number=f"{count:,}", count=count)
    if counts.stopped:
        return tr(
            "{label}：{number}件以上", label=name, number=f"{count:,}", count=count
        )
    return tr("{label}：数えています…", label=name)


def opening_text(count: int, total: int) -> str:
    """ファイルを開く途中の文（workers.FileOpener.progress の値）"""
    if total < 0:
        return tr(
            "フォルダを読んでいます（{number}件）…", number=f"{count:,}", count=count
        )
    return tr(
        "開いています（{done}／{total}件）", done=f"{count:,}", total=f"{total:,}"
    )
