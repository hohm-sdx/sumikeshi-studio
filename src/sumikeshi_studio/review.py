# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""確認画面が扱うデータ（開いたファイルとページ）と、ファイルを開く処理、範囲とページの数え方

画面の部品（PySide6）を使わないため、画面を起動せずにテストできる。
確認画面は起動してすぐにこのモジュールを使うため、重いライブラリを読み込むモジュール
（imaging・pipeline）は、型の注釈と、使う関数の中でだけ読み込む（画面を先に出すため）。
画面に出す文とボタンの条件は reviewtext.py、出力の計画と、出力の前後の文・作業ログの1行は exporttext.py にある。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from . import boxes, folders, formstore, ocr
from .i18n import tr

if TYPE_CHECKING:
    from . import imaging, pipeline


@dataclass(eq=False)
class PageState:
    source: imaging.SourcePage
    doc: Document
    number: int  # ファイルの中のページ番号（0始まり）
    result: pipeline.PageResult | None = None
    error: str = ""
    viewed: bool = False
    # 画面で削除した（墨消しPDFに入れない。元のファイルは変えない）。解析は止めず、
    # 結果も受け取る（削除を取り消したときに使うため）
    deleted: bool = False

    @property
    def ready(self) -> bool:
        return self.result is not None


@dataclass(eq=False)
class Document:
    """開いたファイル1つ分の状態"""

    path: Path
    number: int = 0  # 開いたファイルの中での順番（0始まり）
    pages: list[PageState] = field(default_factory=list)
    exported: bool = False  # 出力した後に解除・追加をすると False に戻る
    # 開いたときに選ばれていた OCR。題名の判別に使い、解析し直すときも同じものを使う
    engine: ocr.Engine = ocr.Engine.RAPIDOCR
    # 作業ログの「OCR」の列に書く OCR の名前（いつも RapidOCR）
    engine_label: str = "RapidOCR"
    # 開いたときの入力ファイルの大きさと更新日時（imaging.file_stamp）。出力のときに照合し、違えば出力しない。
    # None なら照合しない
    stamp: tuple[int, int] | None = None
    # 開いたフォルダからの、ファイルのあるフォルダの相対パス（folders.Found.folder）
    folder: PurePath = field(default_factory=PurePath)

    @property
    def label(self) -> str:
        """画面に出す相対パス（開いたフォルダの直下のファイルと、直接選んだファイルは名前だけ）"""
        return str(self.folder / self.path.name)

    @property
    def kept(self) -> list[PageState]:
        """削除していないページ（墨消しPDFに入れるページ）"""
        return [st for st in self.pages if not st.deleted]

    @property
    def failed(self) -> bool:
        """削除していないページに、処理できないページがある（確認済みにできないため、出力しない）

        処理できないページを削除すれば、残りのページを墨消しして出力できる。
        """
        return any(st.error for st in self.kept)

    @property
    def all_deleted(self) -> bool:
        """全ページを削除した（出力しない）"""
        return not self.kept


def open_document(
    path: Path,
    sources: list[imaging.SourcePage],
    number: int,
    engine: ocr.Engine,
    engine_label: str,
    stamp: tuple[int, int] | None = None,
    folder: PurePath | None = None,
) -> Document:
    doc = Document(path, number, engine=engine, engine_label=engine_label, stamp=stamp)
    if folder is not None:
        doc.folder = folder
    doc.pages = [PageState(s, doc, i) for i, s in enumerate(sources)]
    return doc


# 開けるファイルが1つもなかったときの文
NO_FILES_TEXT = "開ける PDF・画像のファイルがありませんでした。"
# 墨消し済みのPDF を除いたときの文（画面下のラベルと、メッセージに添える文）
SKIPPED_TEXT = "墨消し済みのPDFを{count}件除きました"
SKIPPED_NOTE = "墨消し済みのPDFを{count}件除きました。"


@dataclass
class Opened:
    """開いた結果"""

    docs: list[Document] = field(default_factory=list)
    # 開けなかったファイルとフォルダの理由
    failed: list[str] = field(default_factory=list)
    # 除いた墨消し済みのPDF（folders.Inputs.skipped）
    skipped: list[str] = field(default_factory=list)


def open_inputs(
    inputs: folders.Inputs,
    engine: ocr.Engine,
    engine_label: str,
    progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Opened | None:
    """開くファイルの一覧（folders.Inputs）のファイルを開く（PDF はページの数を数える）。progress には、開いた
    ファイルの数と全体の数を渡す。取り消されたら None。imaging は重いライブラリを読み込むので、関数の中で読み込む"""
    from . import imaging

    opened = Opened(failed=list(inputs.errors), skipped=list(inputs.skipped))
    total = len(inputs.files)
    progress(0, total)
    for i, found in enumerate(inputs.files):
        if cancelled():
            return None
        path = found.path
        try:
            sources = imaging.open_pages(path)
            # 開いたときの大きさと更新日時。出力のときに照合する
            stamp = imaging.file_stamp(path)
        except imaging.InputError as e:
            opened.failed.append(_with_label(str(e), found))
            continue
        except OSError:
            opened.failed.append(tr("ファイルを読めません: {name}", name=found.label))
            continue
        finally:
            progress(i + 1, total)
        opened.docs.append(
            open_document(
                path,
                sources,
                len(opened.docs),
                engine,
                engine_label,
                stamp,
                found.folder,
            )
        )
    return None if cancelled() else opened


def _with_label(text: str, found: folders.Found) -> str:
    """開けなかった理由の文の、ファイルの名前を相対パスにする。imaging の文は、どれも「…: {name}」で終わる"""
    name = found.path.name
    if found.label != name and text.endswith(f": {name}"):
        return text[: -len(name)] + found.label
    return text


def open_documents(
    paths: list[Path], engine: ocr.Engine, engine_label: str, depth: int | None = 0
) -> Opened:
    """選ばれたファイルとフォルダ（depth の深さまで。None はすべて）を開く"""
    opened = open_inputs(folders.collect(paths, depth), engine, engine_label)
    assert opened is not None  # 取り消さない
    return opened


def skipped_note(skipped: Sequence[str]) -> str:
    """除いた墨消し済みのPDFがあったことを、メッセージに添える文（なければ空）"""
    return tr(SKIPPED_NOTE, count=len(skipped)) if skipped else ""


def open_failure_text(
    failed: list[str], opened: bool, skipped: Sequence[str] = ()
) -> str:
    """開けなかったファイルがあったときの文。opened は、ほかに開いたファイルがあるか。除いた墨消し済みのPDFがあれば、
    その件数を添える"""
    if opened:
        head = tr("次のファイルは開けませんでした。ほかのファイルは開きます。")
    else:
        head = tr("次のファイルは開けませんでした。")
    text = "\n".join([head, *failed])
    return f"{text}\n\n{skipped_note(skipped)}" if skipped else text


def no_files_text(skipped: Sequence[str] = ()) -> str:
    """開けるファイルが1つもなかったときの文。除いた墨消し済みのPDFがあれば、その件数を添える"""
    return "\n".join(filter(None, [tr(NO_FILES_TEXT), skipped_note(skipped)]))


def skipped_tip(skipped: Sequence[str]) -> str:
    """除いた墨消し済みのPDFの、マウスを重ねたときの説明。相対パスは folders.SKIPPED_SHOWN 件まで並べる"""
    shown = skipped[: folders.SKIPPED_SHOWN]
    lines = [
        tr(
            "名前の末尾が「_redacted」か「_redacted_数字」のファイルは、墨消し済みのPDFとみなして"
            "開きません（「ファイルを開く」で選べば開けます）。"
        ),
        *shown,
    ]
    if rest := len(skipped) - len(shown):
        lines.append(tr("ほか {count}件", count=rest))
    return "\n".join(lines)


# ---------------------------------------------------------------- 数え方


@dataclass(frozen=True)
class Counts:
    auto: int = 0  # 型で決めた個人番号欄
    off: int = 0  # 型で決めた欄のうち、解除したもの
    manual: int = 0  # 手で追加した範囲


def count_boxes(pages: list[PageState]) -> Counts:
    auto = off = manual = 0
    for st in pages:
        for b in st.result.boxes if st.result else []:
            if b.source == boxes.Source.MANUAL:
                manual += 1
            else:
                auto += 1
                off += not b.active
    return Counts(auto, off, manual)


def is_edited(page_boxes: list[boxes.Box]) -> bool:
    """解除・追加の操作をしたか"""
    return any(b.source == boxes.Source.MANUAL or not b.active for b in page_boxes)


def no_mask(st: PageState) -> bool:
    """墨消しする範囲（オンの範囲。自動の欄と手で追加した範囲）が1つもない、削除していない解析済みのページ
    （様式不明のページ、全部の範囲を解除したページなど。処理できないページは除く）"""
    return (
        st.result is not None
        and not st.deleted
        and not st.error
        and not any(b.active for b in st.result.boxes)
    )


def no_mask_pages(doc: Document) -> list[int]:
    """墨消しする範囲のないページ（1始まり）"""
    return [st.number + 1 for st in doc.pages if no_mask(st)]


NO_MASK = (
    "墨消しなし"  # ラベルの文（注意の色。⚠ は要確認と向きに使っているので付けない）
)


def review_pages(pages: list[PageState]) -> list[int]:
    """画面で確かめてほしいことのあるページ（1始まり）。削除したページは除く"""
    return [
        st.number + 1
        for st in pages
        if not st.deleted and st.result is not None and st.result.note
    ]


def orientation_pages(pages: list[PageState]) -> list[int]:
    """向きを確かめてほしいページ（1始まり。画面の「⚠ 向き」）。削除したページは除く"""
    return [
        st.number + 1
        for st in pages
        if not st.deleted
        and st.result is not None
        and not st.result.orientation_confident
    ]


def unknown_pages(pages: list[PageState]) -> list[int]:
    """どの様式とも位置合わせできなかったページ（1始まり。様式不明）。削除したページは除く"""
    return [
        st.number + 1
        for st in pages
        if not st.deleted
        and st.result is not None
        and st.result.form == formstore.UNKNOWN
    ]


def failed_pages(doc: Document) -> list[int]:
    """処理できないページ（1始まり。解析が失敗したページ）。削除したページは除く"""
    return [st.number + 1 for st in doc.kept if st.error]


def export_targets(docs: list[Document]) -> list[Document]:
    """出力できるファイル：まだ出力していないもの

    開いたファイルの全ページの解析が終わるまでは、どのファイルも出力しない（解析が終わっていない
    ページは、墨消しする範囲が決まっていないため）。表示していないページがあっても出力できる。出力する前に、
    警告のメッセージを出す（unviewed_warning）。処理できないページがあるファイルは、確認済みにできないため
    出力せず、ほかのファイルの出力も止めない（出力するときに確かめる。skipped_docs）。
    削除したページは、解析を待たず、出力にも入れない。全ページを削除したファイルは出力しない
    （deleted_docs）。
    """
    rest = [d for d in docs if not d.failed and not d.all_deleted]
    if not all(st.ready for d in rest for st in d.kept):
        return []
    return [d for d in rest if not d.exported]


def deleted_docs(docs: list[Document]) -> list[Document]:
    """出力しないファイル：全ページを削除したもの"""
    return [d for d in docs if d.all_deleted]


def unviewed_pages(doc: Document) -> list[int]:
    """表示していないページ（1始まり）。削除したページは除く"""
    return [st.number + 1 for st in doc.kept if not st.viewed]


def deleted_pages(doc: Document) -> list[int]:
    """削除したページ（1始まり。元のファイルでのページ番号）"""
    return [st.number + 1 for st in doc.pages if st.deleted]


def skipped_docs(docs: list[Document]) -> list[Document]:
    """出力しないファイル：処理できないページがあるもの"""
    return [d for d in docs if d.failed]
