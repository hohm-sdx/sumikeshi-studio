# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""起動の画面と様式の画面に出す、様式の一覧と様式の操作の文と条件

画面の部品（Qt）を使わないので、画面を起動せずにテストできる（tests/test_formstext.py）。状態のラベルの種類は、
uitext の文字（GOOD・ERROR など）で返し、画面の側で widgets.Tone にする。文を返す関数は、今の画面の言語の文を
返す（i18n.tr）。定数は日本語の文のまま持ち、画面に出す側が tr を通す。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import formstore, i18n, uitext
from .formstore import FolderChoice, FormSpec, Loaded, Problem
from .i18n import tr
from .uitext import ERROR, GOOD, NEUTRAL

if TYPE_CHECKING:
    from pathlib import Path

    from .formsave import Saved

# 「題名での判別」のまとまりの見出し
WORDS_HEADING = "題名に次の語があれば、この様式と判別します"
EXCLUDES_HEADING = "ただし、題名に次の語があれば、この様式と判別しません"
SUMMARY_NOTE = (
    "「使う」のチェックを外した様式の帳票は、この PC では自動で墨消しされません。"
)
# 一覧の下の、件数の文の直後に置く説明（秒数は書かない）
TIME_NOTE = "使う様式が多いほど、解析に時間がかかります。"
MIN_DPI = 150  # 登録に使うページの解像度の下限（下回れば警告する）
# 保存の前の確認で、見本の画像と元の画像の整合した特徴点がこれ未満なら、位置合わせの手がかりが少ないと警告する。
# スキャンしたページで残る点は、この確認の点の約1割まで減る（ぼけたスキャンや 100dpi のスキャン）ので、位置合わせが
# 成り立つ下限の10倍にする
FEW_POINTS = 1500
# 様式のフォルダの出どころ（formstore.ORIGIN_USER・ORIGIN_DEFAULT）の、画面に出す文
ORIGIN_TEXTS = {
    formstore.ORIGIN_USER: "選んだフォルダ",
    formstore.ORIGIN_DEFAULT: "既定の場所",
}

# ---------------------------------------------------------------- 様式の一覧


def form_rows(loaded: Loaded) -> list[tuple[str, FormSpec | None, Problem | None]]:
    """様式の一覧の行：(識別子, 読めた様式, 読めない様式のファイル)。読めた様式のあとに、読めないファイルを並べる"""
    rows = [(f.id, f, None) for f in loaded.forms]
    return rows + [(p.form_id, None, p) for p in loaded.problems]


def load_failed(error, choice: FolderChoice | None = None) -> Loaded:
    """様式を読む処理が例外で終わったときの、読んだ結果の代わり。choice は読もうとした様式のフォルダ（渡さないと、
    一覧の上に「様式のフォルダ：None（）」と出る）"""
    folder, origin = (choice.path, choice.origin) if choice is not None else (None, "")
    return Loaded(
        folder, origin, error=tr("様式を読めませんでした。{error}", error=error)
    )


def form_status(
    spec: FormSpec | None, problem: Problem | None = None, off: bool = False
) -> tuple[str, str]:
    """様式の一覧の状態のラベル（起動の画面と様式の編集の画面。off は、この PC で使わない様式）"""
    if spec is None:
        return tr("✕ 読めません（{reason}）", reason=problem.reason), ERROR
    if off:
        return tr("✕ この PC では使わない"), ERROR
    if not spec.editable:
        return (
            tr("✓ 使える（新しいバージョンで保存。このバージョンでは編集できません）"),
            GOOD,
        )
    return tr("✓ 問題なし"), GOOD


def saved_text(spec: FormSpec | None) -> str:
    """起動の画面の一覧の、様式の名前の下に出す保存の情報。読めない様式と、保存の記録のない様式は空"""
    if spec is None or not (spec.updated_at or spec.updated_by):
        return ""
    return tr("{when} {user} が保存", when=spec.updated_at[:10], user=spec.updated_by)


def form_cells(spec: FormSpec | None, problem) -> list[str]:
    """様式の画面の一覧の、行の文（名前・版・更新日時・更新した人・範囲の数・消した範囲の数・題名の語・状態）"""
    if spec is None:
        name = problem.name or problem.file
        return [name, "", "", "", "", "", "", form_status(None, problem)[0]]
    return [
        spec.name,
        tr("第{revision}版", revision=spec.revision),
        spec.updated_at.replace("T", " ")[:16],
        spec.updated_by,
        str(len(spec.fields)),
        str(len(spec.erase)),
        i18n.join(spec.title_words),
        form_status(spec)[0],
    ]


@dataclass(frozen=True)
class ListActions:
    """様式の一覧のボタンを押せるかと、一覧の下の文（登録・置き換えのボタンを出すかは、一覧を置いた画面で決まる）"""

    edit: bool
    replace: bool
    delete: bool
    register: bool
    # 一覧の下の文。None なら変えない（様式を読み書きしている途中は、その処理の文を出したまま）
    status: str | None


def list_actions(
    form_id: str | None,
    spec: FormSpec | None,
    problem: Problem | None,
    busy: bool,
    loaded: Loaded | None,
) -> ListActions:
    """様式の一覧で選んだ行（選んでいなければ、どれも None）と、様式を読み書きしている途中か（busy）から、
    ボタンを押せるかと一覧の下の文を決める"""
    idle = not busy
    # 新しいバージョンのツールで保存された様式は、編集・置き換え・削除のどれもしない
    # （古いバージョンの判断で、新しいバージョンで足した項目や新しい様式を消さないため）
    newer = (spec is not None and not spec.editable) or (
        problem is not None and problem.newer
    )
    can = idle and not newer
    status = None
    if idle:
        error = loaded.error if loaded is not None else ""
        status = error or (tr(formstore.NEWER_FORM) if newer else "")
    return ListActions(
        edit=can and spec is not None,
        replace=can and spec is not None,
        delete=can and bool(form_id),
        register=idle,
        status=status,
    )


# ---------------------------------------------------------------- 起動の画面


def can_blackout(loaded: Loaded | None, loading: bool) -> bool:
    """墨消しの画面を開ける：様式を読み終わり、この PC で使う様式をすべて読めた"""
    return (
        not loading
        and loaded is not None
        and not loaded.error
        and not loaded.blocking_text()
    )


def folder_label(path: Path | None, origin: str) -> str:
    """起動の画面と様式の画面の、様式のフォルダのラベル（場所と、その出どころ）"""
    origin_text = tr(ORIGIN_TEXTS[origin]) if origin in ORIGIN_TEXTS else origin
    return tr("様式のフォルダ：{path}（{origin}）", path=path, origin=origin_text)


def status_message(loaded: Loaded | None, loading: bool, preload_error: str) -> str:
    """一覧の下に出す文（様式を読んでいる・読めない・様式がない・起動の準備ができなかった）

    様式がないときは、ファイルは開けるが、どのページも様式不明になることを示す（起動のたびに押させないよう、確認の
    メッセージにはしない）。「使う」をすべて外したときは、様式があるので示さない。
    """
    if loading:
        status = tr(uitext.FORMS_READING)
    elif loaded is None:
        status = ""
    elif no_forms(loaded):
        status = tr(NO_FORMS)
    else:
        status = loaded.error or loaded.blocking_text()
    if preload_error:
        failed = tr(
            "{failed}。{error}", failed=tr(uitext.PRELOAD_FAILED), error=preload_error
        )
        status = f"{failed}\n{status}".strip()
    return status


NO_FORMS = (
    "様式のフォルダに様式がありません。このままファイルを開くと、どのページも「様式不明」になり、墨消しする欄が"
    "自動では決まりません。「サンプルの様式を入れる」で、国税庁の様式を入れられます。ほかの帳票は、"
    "「様式を編集」の「新しい様式を登録」で登録できます。"
)


def no_forms(loaded: Loaded | None) -> bool:
    """様式のフォルダを読めて、様式も読めない様式のファイルも1つもない（「サンプルの様式を入れる」を出すとき）"""
    return (
        loaded is not None
        and not loaded.error
        and not loaded.forms
        and not loaded.problems
    )


# ---- サンプルの様式を入れる（起動の画面）

ADD_SAMPLES = "サンプルの様式を入れる"  # ボタン
SAMPLES_QUESTION = "様式のフォルダ（{folder}）に様式がありません。サンプルとして、次の国税庁の様式を入れますか？"
SAMPLES_ADDING = "サンプルの様式を入れています…"
SAMPLES_FAILED = (
    "サンプルの様式を入れられませんでした。途中まで入れた様式は削除しました。"
)
SAMPLES_ADDED = "様式のフォルダ（{folder}）に、サンプルの様式を入れました。"


# すべてのユーザー向けの導入を起動したときに、この利用者の自分だけの導入もあるときのお知らせ
MIXED_NOTICE = "このアカウントには、自分だけの墨消しスタジオもあります（{folder}）。"
MIXED_INFO = (
    "すべてのユーザー向けのものと2つあると、どちらで起動したかで、使うバージョンが違うことがあります。使わないほうを、"
    "「インストールされているアプリ」からアンインストールしてください。"
)


def samples_question(folder: Path, items: list[str]) -> tuple[str, str]:
    """サンプルの様式を入れるかの確認の本文と説明（入れる様式の名前と年分を、1行に1つずつ並べる）"""
    return tr(SAMPLES_QUESTION, folder=folder), "\n".join(
        i18n.bullet(item) for item in items
    )


# ---- 起動の画面で様式のフォルダを替えたとき

COPYING = "様式をコピーしています…"
RESET_QUESTION = "様式のフォルダを既定の場所に戻しますか？"  # 「既定の場所に戻す」の確認のメッセージの本文
COPY_FAILED = (
    "様式をコピーできませんでした。様式のフォルダは前のフォルダのままです。原因を取り除いてから、"
    "もう一度お試しください。"
)


def copy_question(
    reset: bool, old: Path, new: Path, count: int
) -> tuple[str, str, tuple[str, str, str]]:
    """様式のない新しいフォルダ new に、前のフォルダ old の様式（count 件）をコピーするかを尋ねる文（本文・説明・
    ボタン）。reset なら「既定の場所に戻す」の形（new が既定の場所、old が今のフォルダ）"""
    if reset:
        info = tr(
            "既定の場所（{default}）には様式がありません。今のフォルダ（{current}）の様式{count}件を、既定の場所に"
            "コピーできます。今のフォルダの様式は消えません。",
            default=new,
            current=old,
            count=count,
        )
        return (
            tr(RESET_QUESTION),
            info,
            (tr("コピーして戻す"), tr("コピーせずに戻す"), tr("戻さない")),
        )
    info = tr(
        "選んだフォルダ（{new}）には様式がありません。前のフォルダ（{old}）の様式{count}件をコピーできます。"
        "前のフォルダの様式は消えません。",
        new=new,
        old=old,
        count=count,
    )
    return (
        tr("前のフォルダの様式を、選んだフォルダにコピーしますか？"),
        info,
        (tr("コピーする"), tr("コピーしない"), tr("変更しない")),
    )


def switched_text(old: Path, new: Path, count: int) -> tuple[str, str]:
    """「変更」で選んだフォルダに様式があったときに、そのフォルダの様式を使うことを示す文（本文・説明）"""
    return tr("選んだフォルダの様式を使います。"), tr(
        "選んだフォルダ（{new}）には様式が{count}件あります。前のフォルダ（{old}）の様式は消えませんが、"
        "この PC では使わなくなります。",
        new=new,
        old=old,
        count=count,
    )


def reset_info(default: Path, current: Path, count: int) -> str:
    """「既定の場所に戻す」の確認のメッセージの説明。既定の場所に様式があれば（count 件）、その様式を使うことを示す"""
    if count:
        return tr(
            "既定の場所（{default}）の様式{count}件を使います。今のフォルダ（{current}）の様式は消えませんが、"
            "この PC では使わなくなります。",
            default=default,
            current=current,
            count=count,
        )
    return tr(
        "既定の場所：{default}。今のフォルダ（{current}）の様式は消えませんが、この PC では使わなくなります。",
        default=default,
        current=current,
    )


def summary_text(loaded: Loaded | None, loading: bool, rows: int) -> str:
    """一覧の下の、使う件数と説明（rows は一覧の行の数）"""
    if loaded is None or loading:
        return ""
    used = tr(
        "{count}件のうち{used}件を使います。", count=rows, used=len(loaded.active)
    )
    return i18n.sentences(used, tr(TIME_NOTE), tr(SUMMARY_NOTE))


# 起動の画面の、起動の準備の状態のラベル（準備しているあいだ。画面を作ったときの初めの文にも使う）
PREPARING = "準備しています…"


def ready_status(preload_error: str, ready: bool) -> tuple[str, str]:
    """起動の準備の状態のラベル"""
    if preload_error:
        return f"✕ {tr(uitext.PRELOAD_FAILED)}", ERROR
    if ready:
        return tr("✓ 準備ができました"), GOOD
    return tr(PREPARING), NEUTRAL


# ---------------------------------------------------------------- 様式の編集の画面


def pick_page_status(analyzing: bool, registering: bool | None, pages: int) -> str:
    """様式の編集の画面の下の文。registering は、用紙のページを選ぶのを待っているときに、登録なら True、
    置き換えなら False。待っていなければ None。pages は開いた用紙のページ数"""
    if analyzing:
        return tr("ページを解析しています（向きと傾きを直しています）…")
    if registering is not None and pages > 1:
        if registering:
            return tr("様式にするページを選び、「このページを使う」を押してください")
        return tr("新しい用紙にするページを選び、「このページを使う」を押してください")
    return ""


# ---------------------------------------------------------------- 様式の操作の結果の文


def done_with_warnings(done: str, warnings: list[str]) -> str:
    """様式の保存・削除などはできたが、その後の処理でできなかったことがあるときの文"""
    return "\n".join(
        [
            tr("{done}。ただし、次のことができませんでした。", done=done),
            *(i18n.bullet(w) for w in warnings),
        ]
    )


def unclear_text(name: str, line: str, deletions: tuple[str, ...]) -> str:
    """保存の直前に、類似した様式と題名で見分けられないときの文。line は題名の語に入れる
    1行の案、deletions は消す行

    案を作れないとき（line が ""。登録済みの様式と同じ年分の見本から登録したときなど）は、年分を足しても見分けられない
    ので、例を出さずに、その様式を編集するよう伝える（例を出すと、例のとおりにしても見分けられず、誤った
    操作に導くため）。
    """
    if not line:
        return tr(
            "この様式の見本は、様式『{name}』の見本に類似していて、2つの見本の題名からは見分けられませんでした。"
            "同じ帳票なら、新しく登録せずに、様式『{name}』を編集してください。",
            name=name,
        )
    text = tr(
        "この様式の見本は、様式『{name}』の見本に類似しています。"
        "類似した様式どうしは、題名でどちらの様式かを決めます。"
        "「{heading}」の欄に、年分と帳票の名前をスペースで区切った1行（案：{line}）を入れてください。",
        name=name,
        heading=tr(WORDS_HEADING),
        line=line,
    )
    delete = ""
    if deletions:
        delete = tr(
            "{rows}の行は、『{name}』の見本の題名にもあるので消してください。",
            rows=i18n.quoted(deletions),
            name=name,
        )
    return i18n.sentences(text, delete, tr("保存しますか？"))


def similar_text(similar: tuple[FormSpec, ...]) -> str:
    """登録を始めるときに、選んだページが登録済みの様式の見本に類似していたときの文（similar は点の多い順）"""
    first, *rest = similar
    text = tr(
        "選んだページは、登録済みの様式『{name}』の見本に類似しています。",
        name=first.name,
    )
    if not rest:
        return text
    others = tr(
        "様式{names}の見本にも類似しています。",
        names=i18n.quoted([s.name for s in rest], "『』"),
        count=len(rest),
    )
    return i18n.sentences(text, others)


def replace_text(spec: FormSpec, other: FormSpec) -> str:
    """置き換えるときに、選んだページがほかの様式の見本により類似していたときの文"""
    return tr(
        "選んだページは、置き換える様式『{name}』よりも、様式『{other}』の見本に類似しています。"
        "置き換える様式が『{name}』で正しいか確かめてください。",
        name=spec.name,
        other=other.name,
    )


# ---------------------------------------------------------------- 範囲を描く画面の保存の前の確認
# 画面の側は、文をダイアログに出し、解析のプロセス・裏のスレッドに頼む順だけを持つ


def save_problem(name: str, others: Sequence[FormSpec], has_fields: bool) -> str | None:
    """保存できない理由（名前と範囲）。今の順に調べ、最初に当たったものの文。なければ None"""
    if not name:
        return tr("様式の名前を入れてください。")
    if name in formstore.RESERVED_NAMES:
        return tr("「{name}」は様式の名前に使えません。", name=name)
    if formstore.has_control(name):  # 読み込むときに読めない様式になるため
        return tr("様式の名前に使えない文字（制御文字）があります。")
    if any(f.name == name for f in others):
        return tr(
            "「{name}」という名前の様式がすでにあります。別の名前にしてください。",
            name=name,
        )
    if not has_fields:
        return tr("墨消しする範囲を1つ以上描いてください。")
    return None


def not_aligned_text(inliers: int, needed: int) -> str:
    """保存の前の確認で、新しい見本の画像と元の画像を位置合わせできなかったときの文。整合した特徴点が足りていても、
    移した形や大きさが用紙として成り立たなければ位置合わせは成り立たない（align.plausible）ので、そのときは点の数を
    足りないとは言わない"""
    if inliers >= needed:
        return tr(
            "この画像では、元の画像と位置合わせした大きさが合いません"
            "（元の画像と整合した特徴点：{inliers}点）。"
            "保存しません。消す範囲を狭くするか、別のページから登録してください。",
            inliers=inliers,
        )
    return tr(
        "この画像では位置合わせの手がかりが足りません"
        "（元の画像と整合した特徴点：{inliers}点。{needed}点以上が要ります）。"
        "保存しません。消す範囲を狭くするか、別のページから登録してください。",
        inliers=inliers,
        needed=needed,
    )


# 登録で、罫線の一致度の下限の初めの値を、使うほかの様式の罫線と比べて求められなかったときに、範囲を描く画面の
# 状態の行に出す文（limit は、そのときの下限。小数2桁）
LIMIT_NOT_COMPARED = (
    "ほかの様式の罫線と比べられなかったので、罫線の一致度の下限を {limit} にしています"
)
# 登録・置き換えで範囲を描く画面を開く前に、続けるかを尋ねる文の最後（警告がなくても出す）
REGISTER_QUESTION = (
    "見本の画像は、墨消しする範囲の外がそのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンから登録してください。\n登録を続けますか？"
)
REPLACE_QUESTION = (
    "見本の画像は、墨消しする範囲の外がそのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンに置き換えてください。\n置き換えを続けますか？"
)


def written_warning(names: Sequence[str]) -> str:
    """記入があるとみた範囲の警告"""
    return tr("範囲{names}に記入があるようです。", names=i18n.quoted(names))


def page_warnings(
    distorted: bool, written: Sequence[str], source_dpi: float | None
) -> list[str]:
    """登録と置き換えで、範囲を描く画面を開く前に確かめてもらう警告（初めの範囲の記入、写真などで歪んだ画像、
    低い解像度）。どれも、続けるかを尋ねるだけで止めない"""
    warnings = []
    if written:
        warnings.append(written_warning(written))
    if distorted:
        warnings.append(
            tr(
                "このページは、用紙が歪んで写っているようです。歪んだ画像を見本にすると、"
                "スキャンしたページで範囲が欄からずれることがあります。"
            )
        )
    if source_dpi is not None and source_dpi < MIN_DPI:
        warnings.append(
            tr(
                "元のページの解像度は {dpi}dpi です。見本には {min_dpi}dpi 以上のスキャンを使ってください。",
                dpi=f"{source_dpi:.0f}",
                min_dpi=MIN_DPI,
            )
        )
    return warnings


def save_warnings(written: Sequence[str], inliers: int) -> list[str]:
    """保存の前に確かめてもらう警告：描いた範囲の記入（登録と置き換え）と、位置合わせの手がかりが少ない様式
    （inliers は、保存の前の確認で、見本の画像と元の画像の整合した特徴点の数）"""
    warnings = [written_warning(written)] if written else []
    if inliers < FEW_POINTS:
        warnings.append(
            tr(
                "この様式は、文字や線が少なく、位置合わせの手がかりが少ないようです（{inliers}点）。"
                "スキャンの状態によっては、様式不明になることがあります。",
                inliers=inliers,
            )
        )
    return warnings


def overwrite_question(spec: FormSpec | None, saved: Saved | None) -> str | None:
    """保存の直前に読み直した版（saved）が、編集を始めたときの版と違うときに、上書きしてよいかを尋ねる文。
    同じなら None"""
    expected = spec.revision if spec is not None else None
    current = saved.revision if saved is not None else None
    if current == expected:
        return None
    if saved is None:
        return tr(
            "この様式は、ほかの人が様式のフォルダから外しています（削除の操作など）。"
            "保存し直しますか？"
        )
    return saved.conflict_text()
