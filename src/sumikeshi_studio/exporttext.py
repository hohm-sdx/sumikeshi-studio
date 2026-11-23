# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""墨消しの画面の出力の計画と、出力の前後の文・作業ログの1行

画面の部品（PySide6）を使わないため、画面を起動せずにテストできる。墨消しした PDF を作る処理は export.py（解析の
プロセスで動かす）にあり、このモジュールは、どのファイルをどこに出力するか、出力の前に確かめる文、出力の結果の文と
作業ログの1行を作る。重いライブラリを読み込むモジュール（export）は、型の注釈と、使う関数の中でだけ読み込む。
"""

import os
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import formstore, i18n, uitext, worklog
from .i18n import tr
from .review import (
    NO_MASK,
    Document,
    count_boxes,
    deleted_docs,
    deleted_pages,
    export_targets,
    failed_pages,
    no_mask,
    no_mask_pages,
    orientation_pages,
    review_pages,
    skipped_docs,
    unknown_pages,
    unviewed_pages,
)

if TYPE_CHECKING:
    from . import export
    from .formstore import FormSpec


# ---------------------------------------------------------------- 出力


def unviewed_warning(docs: list[Document]) -> str:
    """出力する前に出す警告の文。表示していないページがなければ空

    ファイルごとに、表示していないページを示す。そのうち「⚠ 要確認」「⚠ 向き」「墨消しなし」のページは分けて示す。
    """
    blocks = []
    any_review = any_turn = any_none = (
        False  # 「⚠ 要確認」「⚠ 向き」「墨消しなし」の未表示のページがあったか
    )
    for d in docs:
        pages = [st for st in d.kept if not st.viewed]
        if not pages:
            continue
        review = [st.number + 1 for st in pages if st.result and st.result.note]
        turn = [
            st.number + 1
            for st in pages
            if st.result and not st.result.orientation_confident
        ]
        # 「⚠ 要確認」「⚠ 向き」のページは、そちらの説明で足りるので重ねて示さない
        nomask = [
            st.number + 1
            for st in pages
            if no_mask(st) and st.number + 1 not in review and st.number + 1 not in turn
        ]
        plain = [
            st.number + 1
            for st in pages
            if st.number + 1 not in review
            and st.number + 1 not in turn
            and st.number + 1 not in nomask
        ]
        lines = [i18n.bullet(d.label)]
        if plain:
            lines.append(
                tr("　　表示していないページ：{pages}", pages=ranges_text(plain))
            )
        if review:
            lines.append(
                tr(
                    "　　表示していない「⚠ 要確認」のページ：{pages}",
                    pages=ranges_text(review),
                )
            )
            any_review = True
        if turn:
            lines.append(
                tr(
                    "　　表示していない「⚠ 向き」のページ：{pages}",
                    pages=ranges_text(turn),
                )
            )
            any_turn = True
        if nomask:
            lines.append(
                tr(
                    "　　表示していない「{label}」のページ：{pages}",
                    label=tr(NO_MASK),
                    pages=ranges_text(nomask),
                )
            )
            any_none = True
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    head = [
        tr("次のファイルには、表示していないページがあります。"),
        tr("表示していないページは、墨消しの範囲が確認されないまま出力されます。"),
    ]
    if any_review:
        head.append(
            tr(
                "「⚠ 要確認」のページには、墨消しの範囲が欄に合っていないおそれがある欄か、"
                "様式を判別できずに墨消ししていない用紙があります。"
            )
        )
    if any_turn:
        head.append(
            tr("「⚠ 向き」のページは、向きを自動で判定できず、確かめていません。")
        )
    if any_none:
        head.append(
            tr(
                "「{label}」のページは、墨消しする範囲がないまま出力されます。",
                label=tr(NO_MASK),
            )
        )
    tail = tr("表示していないページを確かめずに出力しますか？")
    return "\n".join([*head, "", *blocks, "", tail])


NO_OVERWRITE = "元のファイルには上書きできません。別のフォルダを選んでください。"


@dataclass(frozen=True)
class ExportPlan:
    jobs: list[tuple[Document, Path]]  # 出力するファイルと出力先
    error: str = ""  # 出力できない理由（空なら出力できる）
    existing: list[Path] = field(default_factory=list)  # 出力先にすでにあるファイル
    folder: Path = Path()  # 出力先のフォルダ
    # 出力先のパスが長すぎるため、出力しないファイルと出力先（jobs には入れない）
    too_long: list[tuple[Document, Path]] = field(default_factory=list)


def output_names(docs: list[Document], folder: Path) -> dict[Document, Path]:
    """開いたファイルごとの出力先（folder の下の、開いたフォルダからの相対パスのフォルダの「元の名前_redacted.pdf」）

    名前は開いた全ファイルを通して決める。出力の順や、出力するファイルの選び方によって名前が変わらないようにするため。
    """
    from . import export

    outs = export.output_paths([d.path for d in docs], folder, [d.folder for d in docs])
    return dict(zip(docs, outs))


def shown_output(out: Path, folder: Path) -> str:
    """画面に出す出力先：出力先のフォルダからの相対パス"""
    try:
        return str(out.relative_to(folder))
    except ValueError:
        return str(out)


def overwrites_input(docs: list[Document], outs: list[Path]) -> bool:
    """出力先のどれかが、開いたファイルのどれかと同じファイルか"""
    inputs = {d.path.resolve() for d in docs}
    return any(out.resolve() in inputs for out in outs)


# 出力先のパスが長すぎるときの文
SHALLOW_FOLDER = (
    "出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。"
)
# 長いパスを使えない Windows の PC での、ファイルのパスの長さの上限（文字の数。MAX_PATH の260は終わりの文字を含む）。
# 作るフォルダの上限（CreateDirectoryW は、それより12文字短い）は、出力の途中の一時的な名前がこの上限に収まれば、
# いつも収まる
MAX_FILE_PATH = 259
# 出力の途中の一時的な名前（export.export の「.<出力の名前>.<8桁>.tmp」）が、出力の名前より長い文字の数
TEMPORARY_EXTRA = len("..00000000.tmp")


def long_paths() -> bool:
    """長いパス（260文字以上）を使えるか。Windows では、PC の設定（LongPathsEnabled）と、プロセスが長いパスに
    対応しているかで決まる（ntdll の RtlAreLongPathsEnabled）。ほかの OS では、いつも真"""
    if os.name != "nt":
        return True
    import ctypes

    try:
        enabled = ctypes.WinDLL("ntdll").RtlAreLongPathsEnabled
    except OSError, AttributeError:
        return False
    # 戻り値は BOOLEAN（1バイト）。型を付けないと int として読み、上位のバイトに残った値で真になるおそれがある
    enabled.restype = ctypes.c_ubyte
    enabled.argtypes = []
    return bool(enabled())


def _length(path: Path) -> int:
    """Windows の数え方のパスの長さ（UTF-16 の単位の数）"""
    return len(str(path).encode("utf-16-le")) // 2


def path_too_long(out: Path) -> bool:
    """長いパスを使えない PC で、出力の途中の一時的な名前のパスが上限を超えるか"""
    return not long_paths() and _length(out) + TEMPORARY_EXTRA > MAX_FILE_PATH


def plan_export(docs: list[Document], folder: Path) -> ExportPlan:
    """出力するファイルを folder の下に「元の名前_redacted.pdf」で出力するときの、出力先を決める。出力先のパスが
    長すぎるファイル（path_too_long）は除く（すべてがそうなら、出力できない理由にする）"""
    names = output_names(docs, folder)
    jobs = [(d, names[d]) for d in export_targets(docs)]
    if overwrites_input(docs, [out for _, out in jobs]):
        return ExportPlan(jobs, error=tr(NO_OVERWRITE), folder=folder)
    too_long = [(d, out) for d, out in jobs if path_too_long(out)]
    if too_long and len(too_long) == len(jobs):
        return ExportPlan(jobs, error=too_long_text(too_long, folder), folder=folder)
    jobs = [job for job in jobs if job not in too_long]
    return ExportPlan(
        jobs,
        existing=[out for _, out in jobs if out.exists()],
        folder=folder,
        too_long=too_long,
    )


def too_long_text(too_long: list[tuple[Document, Path]], folder: Path) -> str:
    """出力するファイルのすべての出力先のパスが長すぎて、出力できないときの文"""
    return "\n".join(
        [
            tr("出力先のパスが長すぎるため、出力できません。"),
            *(i18n.bullet(shown_output(out, folder)) for _, out in too_long),
            tr(SHALLOW_FOLDER),
        ]
    )


def too_long_question(too_long: list[tuple[Document, Path]], folder: Path) -> str:
    """出力の前の確認：出力先のパスが長すぎるファイルを除いて、ほかのファイルを出力してよいか"""
    return "\n".join(
        [
            tr(
                "次のファイルは、出力先のパスが長すぎるため出力しません。ほかのファイルを出力しますか？"
            ),
            *(i18n.bullet(shown_output(out, folder)) for _, out in too_long),
            tr(SHALLOW_FOLDER),
        ]
    )


def skipped_question(skipped: list[Document]) -> str:
    """出力の前の確認：処理できないページがあるファイルを除いて、ほかのファイルを出力してよいか"""
    return "\n".join(
        [
            tr(
                "次のファイルは、処理できないページがあるため出力しません。ほかのファイルを出力しますか？"
            ),
            *(i18n.bullet(d.label) for d in skipped),
            tr("処理できないページを削除すると、そのファイルも出力できます。"),
        ]
    )


def overwrite_question(existing: list[Path], folder: Path) -> str:
    """出力の前の確認：出力先にすでにある同じ名前のファイルを、上書きしてよいか（出力先のフォルダからの相対パスで
    並べる）"""
    return "\n".join(
        [
            tr("出力先に、同じ名前のファイルがすでにあります。上書きしますか？"),
            *(i18n.bullet(shown_output(p, folder)) for p in existing),
        ]
    )


def export_questions(docs: list[Document], plan: ExportPlan) -> list[str]:
    """出力の前に、この順に1つずつ確かめる文：処理できないページがあるファイルは除くこと、出力先のパスが長すぎる
    ファイルは除くこと、表示していないページがあれば警告、出力先に同じ名前のファイルがあれば上書きしてよいか。
    確かめることがなければ空"""
    questions = []
    if skipped := skipped_docs(docs):
        questions.append(skipped_question(skipped))
    if plan.too_long:
        questions.append(too_long_question(plan.too_long, plan.folder))
    # 表示していないページは、墨消しする範囲を確かめないまま出力することになるため、警告して確かめる。
    # 「はい」を押し続けて進まないよう、既定のボタンは「いいえ」にする（ほかの確認も「いいえ」が既定）
    if warning := unviewed_warning([d for d, _ in plan.jobs]):
        questions.append(warning)
    if plan.existing:
        questions.append(overwrite_question(plan.existing, plan.folder))
    return questions


def worklog_record(doc: Document, out: Path, report: export.ExportReport) -> dict:
    """作業ログの1行（日時と担当者は worklog.append が足す）。ページの番号は「, 」でつなぐ"""
    counts = count_boxes(doc.kept)  # 墨消しPDFに入れたページの分
    return {
        worklog.INPUT_FILE: str(doc.path),
        worklog.OUTPUT_FILE: str(out),
        worklog.PAGES: report.pages,
        worklog.FORM_FIELDS: counts.auto,
        worklog.FIELDS_OFF: counts.off,
        worklog.MANUAL_AREAS: counts.manual,
        worklog.PAGES_TO_REVIEW: page_list(review_pages(doc.pages)),
        worklog.PAGES_ORIENTATION: page_list(orientation_pages(doc.pages)),
        worklog.PAGES_NO_REDACTION: page_list(no_mask_pages(doc)),
        worklog.PAGES_NOT_ANALYZED: page_list(failed_pages(doc)),
        worklog.PAGES_NOT_VIEWED: page_list(unviewed_pages(doc)),
        worklog.DELETED_PAGES: page_list(deleted_pages(doc)),
        worklog.REDACTED_AREAS: report.masked,
        worklog.OCR: doc.engine_label,
        worklog.FORMS_USED: used_forms_text(doc),
        worklog.ALIGNMENT_POINTS: points_text(doc),
        worklog.LINE_MATCH: line_match_text(doc),
        worklog.LINE_BELOW: line_below_text(doc),
    }


def write_worklog(doc: Document, out: Path, report: export.ExportReport) -> str:
    """作業ログに1行書く。書けなければ理由の文を返す（書けたら空）

    作業ログのファイルを Excel で開いているなどで書けなくても、出力の結果は必ず知らせるため、例外を外へ出さない。
    """
    try:
        worklog.append(worklog_record(doc, out, report))
    except Exception as e:  # noqa: BLE001  どんな理由でも、結果の文に入れて知らせる
        traceback.print_exception(e)
        return f"{type(e).__name__}: {e}"
    return ""


def check_pages(doc: Document) -> list[tuple[str, list[int]]]:
    """確かめるべきページ：種類（ラベルの文。tr を通す前の文）と、そのページ（1始まり）の組。ページのない種類は除く

    種類の決め方とラベルの文は、墨消しの画面の一覧のラベル（reviewtext.page_parts）と同じ。1つのページが2つ以上の
    種類に入ることがある（様式不明のページは、要確認と墨消しなしにも入る）。
    """
    kinds = [
        (uitext.REVIEW_LABEL, review_pages(doc.pages)),
        (uitext.ORIENTATION_LABEL, orientation_pages(doc.pages)),
        (formstore.UNKNOWN, unknown_pages(doc.pages)),
        (NO_MASK, no_mask_pages(doc)),
        (uitext.FAILED_LABEL, failed_pages(doc)),
    ]
    return [(label, pages) for label, pages in kinds if pages]


def page_list(nums: list[int]) -> str:
    """作業ログに書くページの番号（1つずつ「, 」でつなぐ。Excel で特定のページを検索しやすいよう、続く番号も
    まとめない）"""
    return ", ".join(str(n) for n in nums)


def used_forms_text(doc: Document) -> str:
    """作業ログの「使った様式」の列：墨消しPDFに入れたページで、範囲を使った様式"""
    used: dict[tuple, FormSpec] = {}
    for st in doc.kept:
        for spec in st.result.used if st.result else ():
            used.setdefault((spec.id, spec.revision), spec)
    return ", ".join(spec.log_label() for spec in used.values())


def points_text(doc: Document) -> str:
    """作業ログの「Alignment points」の列：墨消しPDFに入れたページのうち、見本との位置合わせで判別したページの、
    位置合わせの1段目の整合した点の数。「ページの番号: 点」を「, 」でつなぐ。帳票が2枚以上写ったページは、見つけた
    順の点を「/」でつなぐ"""
    return ", ".join(
        f"{st.number + 1}: {'/'.join(str(n) for n in st.result.points)}"
        for st in doc.kept
        if st.result is not None and st.result.points
    )


def line_match_text(doc: Document) -> str:
    """作業ログの「Line match」の列：墨消しPDFに入れたページのうち、バリエーションと決めたページの、罫線の一致度
    （見本の側の割合とページの側の割合を、この順に「/」でつなぐ。小数2桁）。「ページの番号: 値」を「, 」でつなぐ。
    見本と位置合わせできて、罫線の対応で置き直したページは、「Alignment points」の列にも値がある"""
    return ", ".join(
        f"{st.number + 1}: {'/'.join(f'{v:.2f}' for v in st.result.line_match)}"
        for st in doc.kept
        if st.result is not None and st.result.line_match is not None
    )


def line_below_text(doc: Document) -> str:
    """作業ログの「Line match below limit」の列：墨消しPDFに入れたページのうち、様式不明のままになり、罫線の一致度が
    下限に届かなかった候補があるページの、一致度と下限。「ページの番号: 見本の側の割合/ページの側の割合 < 下限
    (様式の識別子 r版)」を「, 」でつなぐ（小数2桁。例：「4: 0.41/0.55 < 0.70 (nta-fuyou-r9 r1)」）。その様式の用紙が
    様式不明になるときに、下限をどこまで下げれば判別されるかがわかる"""
    out = []
    for st in doc.kept:
        below = st.result.line_below if st.result is not None else None
        if below is not None:
            out.append(
                f"{st.number + 1}: {below.template:.2f}/{below.page:.2f} < "
                f"{below.limit:.2f} ({below.form_id} r{below.revision})"
            )
    return ", ".join(out)


# 作業ログに書けなかったときの1行（墨消しの画面の出力の結果と、コマンドラインからの出力）と、作業ログの場所の行
LOG_FAILED = "　　作業ログに書けませんでした（{error}）。{file} を開いているソフトがあれば閉じてください。"
LOG_PATH = "作業ログ：{file}"


def result_warns(doc: Document, log_error: str) -> bool:
    """出力の結果で、1ファイルの記号を「⚠」にする：表示していないページがあるか、作業ログに書けなかった（log_error は
    その理由）。result_text の記号と、出力の結果の画面を警告にするかの判断（export_result の problems）の両方に使う
    （2つが食い違わないように）"""
    return bool(unviewed_pages(doc)) or bool(log_error)


def result_text(
    doc: Document, out: Path, report: export.ExportReport, log_error: str = ""
) -> str:
    """出力の結果の画面に出す、1ファイル分の文。log_error は、作業ログに書けなかった理由。入力と出力は、開いた
    フォルダと出力先のフォルダからの相対パス"""
    unviewed = unviewed_pages(doc)
    mark = "⚠" if result_warns(doc, log_error) else "✓"
    lines = [
        tr(
            "{mark} {name} → {out}　墨消しした範囲：{count}か所",
            mark=mark,
            name=doc.label,
            out=str(doc.folder / out.name),
            count=report.masked,
        )
    ]
    # 削除は意図した操作なので、それだけでは記号を「⚠」にしない
    if deleted := deleted_pages(doc):
        lines.append(tr("　　削除したページ：{pages}", pages=ranges_text(deleted)))
    if unviewed:
        lines.append(
            tr(
                "　　表示していないページ：{pages}（確かめずに出力しました）",
                pages=ranges_text(unviewed),
            )
        )
    # 記号（✓・⚠）は変えない。控除証明書など、もともと番号のない用紙のページは多いため
    if nomask := [n for n in no_mask_pages(doc) if n not in deleted_pages(doc)]:
        lines.append(
            tr("　　墨消しする範囲のないページ：{pages}", pages=ranges_text(nomask))
        )
    if log_error:
        lines.append(tr(LOG_FAILED, error=log_error, file=worklog.LOG_NAME))
    if pages := review_pages(doc.pages):
        lines.append(
            tr(
                "　　要確認のページ：{pages}"
                "（墨消しの範囲が欄に合っていないおそれがある欄か、様式を判別できずに墨消ししていない用紙がある"
                "ページ）",
                pages=ranges_text(pages),
            )
        )
    return "\n".join(lines)


def failure_text(doc: Document, error: str) -> str:
    return tr("✗ {name}：出力できませんでした。{error}", name=doc.label, error=error)


def all_deleted_text(doc: Document) -> str:
    return i18n.bullet(
        tr("{name}：全ページを削除したため、出力しませんでした。", name=doc.label)
    )


def skipped_text(doc: Document) -> str:
    return tr(
        "✗ {name}：処理できないページがあるため、出力しませんでした。"
        "そのページを削除すると、出力できます。",
        name=doc.label,
    )


def too_long_result(doc: Document) -> str:
    """出力の結果の画面の、出力先のパスが長すぎて出力しなかったファイルの文"""
    return tr(
        "✗ {name}：出力先のパスが長すぎるため、出力しませんでした。", name=doc.label
    )


@dataclass(frozen=True)
class ExportResult:
    """出力の結果のダイアログに出すもの"""

    text: str
    warning: bool  # 警告のダイアログにする（⚠ や ✗ の付いたファイルがある）
    # ダイアログに置く、フォルダを開くボタン（ボタンの文, フォルダ, 開けなかったときのメッセージに入れるフォルダの名前）
    folders: list[tuple[str, Path, str]]


def export_result(
    out_dir: Path,
    results: list[str],
    problems: list[bool],
    docs: list[Document],
    log_dir: Path,
    too_long: Sequence[Document] = (),
) -> ExportResult:
    """出力の結果の画面の文・警告にするか・フォルダを開くボタン。results・problems は、出力したファイルごとの結果の
    文と、「⚠」「✗」にしたか。log_dir は作業ログのフォルダ（worklog.log_dir()。パスを組み立てるだけでフォルダは
    作らないので、警告にしないときも求めてよい）。too_long は、出力先のパスが長すぎて出力しなかったファイル"""
    skipped = [skipped_text(d) for d in skipped_docs(docs)]
    skipped += [too_long_result(d) for d in too_long]
    # 全ページを削除したファイルは、意図した操作なので「⚠」「✗」にしない
    deleted = [all_deleted_text(d) for d in deleted_docs(docs)]
    lines = [tr("出力先：{folder}", folder=out_dir), *results, *skipped, *deleted]
    # 結果の画面に、出力先のフォルダを開くボタンを置く。⚠ や ✗ があるときは、作業ログのフォルダを開くボタンも
    # 置く（作業ログのファイルそのものは開かない。Excel で開いたままにすると、次の出力で作業ログに書けないため）
    # 3つ目は、開けなかったときのメッセージ（widgets.ResultDialog の「{what}を開けませんでした」）に入れる語句
    folders = [
        (
            tr("出力先のフォルダを開く"),
            out_dir,
            tr("出力先のフォルダ", context="文の中"),
        )
    ]
    warning = any(problems) or bool(skipped)
    if warning:
        lines.append(tr("⚠ や ✗ の付いたファイルを確かめてください。"))
        lines.append(tr(LOG_PATH, file=log_dir / worklog.LOG_NAME))
        folders.append(
            (
                tr("作業ログのフォルダを開く"),
                log_dir,
                tr("作業ログのフォルダ", context="文の中"),
            )
        )
    return ExportResult("\n".join(lines), warning, folders)


def ranges_text(nums: list[int]) -> str:
    """ページの番号を、続くものは「3〜5」とまとめて並べる（例：1、3〜5、8）

    画面に出す番号はすべてこの形にする。作業ログは、Excel で特定のページを検索しやすいよう、
    1つずつ「, 」でつなぐ（page_list）。
    """
    parts, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            parts.append(
                str(n) if n == start else tr("{start}〜{end}", start=start, end=n)
            )
            start = None
    return i18n.join(parts)
