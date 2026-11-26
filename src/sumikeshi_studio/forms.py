# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""帳票の種類の判別と、様式の見本の画像との位置合わせ

配布用パッケージに入れるサンプルの様式は、国税庁の公開様式から作った次の3種類の申告書（個人番号欄のある主な帳票）。ほかの帳票は、
画面から様式を登録すれば、同じように墨消しする。
- 給与所得者の扶養控除等（異動）申告書（見本は令和9年分）
- 給与所得者の保険料控除申告書（見本は令和8年分）
- 給与所得者の基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼
  所得金額調整控除申告書（見本は令和8年分）

まず様式の見本の画像とページの特徴点を対応させ、位置合わせが成り立った様式で種類を判別し、
様式に決めた墨消しする範囲をページに移す。どの様式とも成り立たなかったページは「様式不明」とし、題名は読まない。
題名は、同じ用紙の部分に2つ以上の様式が成り立ったときだけ読む。
年分だけを確かめることはしない。類似した様式が2つ以上成り立ったときは、題名の語（年分の語を入れることがある）で
決める（titles.choose）。欄の中身は読まず、様式にある欄はすべて墨消しする。
配布用パッケージに入れる3様式の見本は、国税庁の公開様式（samples/20xxbun_xx.pdf）から tools/make_templates.py で作る。

使う様式（様式のフォルダの様式。formstore.FormSpec）は、解析の依頼ごとに受け取る。
見本の画像の特徴点は、解析のプロセスで画像のファイルごとに1回だけ求めて覚えておく。
見本との位置合わせと向きは align.py に、用紙の曲がりの補正は bend.py に、写真の中の用紙は paper.py に、
題名で様式を決める処理は titles.py に、範囲が隣の行に置かれている見込みの高い欄の見分けは rowshift.py に、
様式の登録・編集のときの確認は formcheck.py にある。
"""

import math
import traceback
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import NamedTuple

import cv2
import numpy as np

from . import align, bend, frames, i18n, ocr, paper, rowshift, titles, variants
from .formstore import (
    TEMPLATE_SCALE,
    UNKNOWN,
    Field,
    FormSpec,
    frame_pairs,
)

# 位置合わせできた帳票が作業画像のこの割合より狭いときは、ほかの用紙が写っていないかを調べる。
# スキャンでは帳票が画像のほぼ全体を占め、ふつうは調べない（傾きを直して画像が広がると調べるが、
# 回帰テストの3度・8度の12件では、ほかの用紙は見つからなかった）
COVER_FULL = 0.9
MAX_FORMS = 4  # 1ページで位置合わせする帳票の数の上限


# 配布用パッケージに入れる3様式の名前（tools/make_templates.py とテストで使う）
FUYOU = "扶養控除等申告書"
HOKEN = "保険料控除申告書"
KIHAI = "基・配・特・所の申告書"
# どの様式とも位置合わせできなかったページの「⚠要確認」の文
UNKNOWN_NOTE = "様式を判別できず、墨消ししていません"
# バリエーション（variants）として範囲を置いたページの「⚠要確認」の文。確かでない欄があっても名前は挙げない（罫線の
# 対応で置いた範囲は、どれもずれているおそれがあるため）
VARIANT_NOTE = "『{name}』のバリエーションのため、範囲がずれているおそれがあります"


class BelowLimit(NamedTuple):
    """罫線の一致度が下限に届かなかった候補の、作業ログに書く値"""

    form_id: str  # 様式の識別子
    revision: int  # 様式の版
    template: float  # 見本の側の割合
    page: float  # ページの側の割合
    limit: float  # その様式の下限


@dataclass(frozen=True)
class Located:
    """1ページの判別と位置合わせの結果"""

    # 判別した様式の名前（バリエーションは、罫線の並びで決めた見本の様式の名前）。どの様式にも決まらなければ UNKNOWN
    form: str
    fields: list[Field]  # 墨消しする範囲
    note: str = ""  # 画面で確かめてほしいこと。空なら、様式どおりに墨消しできる
    # 範囲を使った様式（見本との位置合わせが成り立った様式か、バリエーションと決めた見本の様式）
    used: tuple[FormSpec, ...] = ()
    # 印刷された枠の線が見つからない辺がある範囲の名前（見つからない辺は、見本の位置を移したまま。曲がりの補正を
    # したときは補正した位置、バリエーションは罫線の対応で置いた位置）
    unframed: tuple[str, ...] = ()
    # 枠の線を越えて書かれた記入がある範囲の名前
    crossed: tuple[str, ...] = ()
    # 1枚目の用紙の部分で見本に類似した様式（点の多い順）。様式の登録・置き換えの
    # ときの確認に使う
    similar: tuple[FormSpec, ...] = ()
    # 位置合わせの1段目（align.register）の整合した点の数。位置合わせできた用紙ごとに、見つけた順（同じ様式の用紙が
    # 2枚写っていれば2つで、used の数と違う。作業ログに書く）
    points: tuple[int, ...] = ()
    # 用紙についての注記（類似した様式を題名で決められない、帳票が2枚以上写っている、ほかの用紙が写っている）がある
    paper_notes: bool = False
    # 用紙についての注記が、様式を判別できない用紙も写っていること（OTHER_PAPER_NOTE）の1つだけ。
    # 行の高さ・列の幅が見本と違う用紙では、位置合わせの変換で移した見本の四隅がページの一部しか覆わず、残りを
    # ほかの用紙とみて、この注記が付くことがある
    other_paper_only: bool = False
    # 写真と判定したページ（_is_photo）
    photo: bool = False
    # バリエーションとして、見本の罫線の並びで範囲を置いた（variants）
    variant: bool = False
    # バリエーションの、罫線の一致度（variants.score の見本の側の割合とページの側の割合。見本と同じ画像でどちらも1.0）
    line_match: tuple[float, float] | None = None
    # 様式不明のままになったページで、罫線の一致度が下限に届かなかった候補（作業ログに書く）
    line_below: BelowLimit | None = None


def unknown() -> Located:
    """どの様式とも位置合わせできなかったページの結果（様式不明。範囲はなく、「⚠要確認」を付ける）"""
    return Located(form=UNKNOWN, fields=[], note=i18n.tr(UNKNOWN_NOTE))


def locate(
    gray: np.ndarray,
    engine: ocr.Engine,
    specs: Sequence[FormSpec],
    hint: FormSpec | None = None,
    group: Sequence[tuple[FormSpec, int]] = (),
    features: tuple | None = None,
    hint_found: tuple | None = None,
) -> Located:
    """向きと傾きを直したページ画像から、墨消しする範囲を求める

    様式（specs）と位置合わせし、成り立った様式（整合した特徴点が
    最も多いもの）で種類を判別する（成り立つ条件は、整合した特徴点が align.MIN_INLIERS 点以上。根拠はそのコメント）。
    どの見本とも成り立たなかったページは、題名を読まずに「様式不明」とし、「⚠要確認」とする（題名だけが合っていても、
    特徴点が合わないなら、様式を特定できなかったものとして扱う）。

    位置合わせできた帳票が画像より狭いとき（写真など）は、その外にほかの用紙が写っていないかを
    調べる。写っていれば、その部分でもう一度位置合わせし、成り立てばその帳票の欄も墨消しする。
    成り立たない用紙が残れば「⚠要確認」とする。1枚の写真に帳票を2枚写したとき、1枚だけを墨消しして、
    もう1枚の番号が残ることを示すため。

    位置合わせが成り立った様式は、2段目の位置合わせ（_refine）で変換を求め直してから、範囲を移す。
    移した範囲は、欄の近くで印刷された枠の線を探し、墨消しの端を線の中心に合わせる（frames.py。墨消しを枠から
    はみ出さないため。線が見つからない辺は、移した位置のまま）。
    線が見つからない辺のある範囲と、写真と判定したページの枠のない範囲は、用紙の曲がりを補った欄ごとの変換
    （bend.py）で移し直してから、線を探し直す（_unbent）。
    線が見つからない辺のある範囲と、枠の線を越えて書かれた記入がある範囲と、写真と判定したページの枠のない範囲は、
    範囲の名前を入れて「⚠要確認」とする（写真の枠のない範囲は、線で位置を確かめられず、曲がりの補正だけで位置が
    決まるため）。写真と判定したページで、見つけた上か下の線が変換で傾き、線の中心が範囲の辺より TILT_REVIEW を
    超えて外に出る所がある長方形の範囲も、名前を入れて「⚠要確認」とする（長方形の範囲では、傾いた線と辺のあいだに
    すき間ができ、記入が残ることがあるため）。4辺とも線の中心を結ぶ四隅にした範囲には付けない（範囲の辺が線に沿うので、
    すき間はほとんどできない。欄の片方の端で、辺が線の内側の縁より2画素ほど内側に入ることはある）。上下の線だけを探す
    欄は、四隅にしても付ける（左右の辺を線で確かめていないため）。
    欄の周りの見本の印刷が、置いた位置より縦に半行以上離れた位置でよく合う範囲（隣の行に置かれている見込みが高い。
    rowshift）も、名前を入れて「⚠要確認」とする（範囲は変えない）。これらの範囲の名前は、範囲が欄に合っていない
    おそれのある欄として、1つの文に重ねずに挙げる（field_note）。
    墨消しを自動で広げることはしない。

    hint は、向きを決めるときに位置合わせが成り立った様式（align.upright_group）。先に試し、成り立てば、ほかの様式とは
    位置合わせしない。1ページの解析で全部の様式と2回位置合わせすると、様式1つにつき
    1ページ約0.1秒延びるため。向きを決めるときに整合した点が最も多かった様式なので、
    ふつうは全部の様式と位置合わせしたときと同じ様式に決まる。
    group は、upright_group で hint と同じ用紙の部分に成り立った様式と点（hint を含む）。2つ以上あれば、題名で決める
    （令和8年分と令和9年分のような類似した様式）。
    features は gray の align.page_features、hint_found は hint の gray の上の (変換, 点)。向きを決めるときに同じ画素の
    画像で求めてあれば渡す（求め直さない）。
    """
    tpls = align.templates(specs)
    if features is None:
        features = align.page_features(gray)
    best, candidates = _first_paper(tpls, features, hint, group, hint_found)
    if best is None:
        return unknown()
    papers, similar, notes = _papers(gray, engine, tpls, features, best, candidates)
    kinds = [kind for kind, _ in notes]
    placed = _placed(gray, papers)
    texts = [text for _, text in notes] + _field_notes(placed)
    return Located(
        form=papers[0].tpl.name,
        fields=placed.fields,
        # 注記は句点のない文なので、句点（英語は「. 」）でつなぐ
        note=i18n.tr("。", context="注記の区切り").join(texts),
        used=tuple(dict.fromkeys(p.tpl.spec for p in papers)),
        unframed=tuple(dict.fromkeys(placed.unframed)),
        crossed=tuple(dict.fromkeys(placed.crossed)),
        similar=similar,
        points=tuple(p.points for p in papers),
        paper_notes=bool(kinds),
        other_paper_only=len(papers) == 1 and kinds == [PaperNote.OTHER_PAPER],
        photo=placed.photo,
    )


class Framed(NamedTuple):
    """framed の結果"""

    field: Field  # 枠の線（の中心）に合わせた範囲
    frame: frames.Frame  # 見つけた枠の線
    crossed: tuple[str, ...]  # 枠の線を越えた記入がある辺
    tilt: float  # 線の中心が範囲の辺より外に出る量（ページの画素）
    # 線は見つかったが、欄の線か確かめられない（範囲は線に合わせ、線が見つからない欄として名前を挙げる）
    unsure: bool = False


class _Placed(NamedTuple):
    """_placed の結果。名前の一覧は、どれも範囲の名前"""

    fields: list[Field]
    unframed: list[str]  # 枠の線が見つからない辺のある範囲
    crossed: list[str]  # 枠の線を越えた記入のある範囲
    frameless: list[str]  # 写真と判定したページの、枠のない範囲
    tilted: list[str]  # 線が傾いて辺より外に出る範囲
    shifted: list[str]  # 隣の行に置かれている見込みの高い範囲（rowshift）
    photo: bool  # 写真と判定したページ


OTHER_PAPER_NOTE = "様式を判別できない用紙も写っていて、その用紙は墨消ししていません"


class PaperNote(Enum):
    """用紙についての注記の種類。置き直しを試すかは、文でなく種類で決める（文は翻訳され、数を入れることもあるため）"""

    SIMILAR = auto()  # 類似した様式を題名で決められない
    MANY = auto()  # 用紙が多く、MAX_FORMS 枚を超える分は墨消ししていない
    OTHER_PAPER = auto()  # 様式を判別できない用紙も写っている（OTHER_PAPER_NOTE）
    PAPERS = auto()  # 帳票が2枚以上写っている


class _Paper(NamedTuple):
    """位置合わせできた用紙1枚"""

    tpl: align.Template
    H: np.ndarray  # 見本から作業画像への変換（2段目で求め直したもの）
    # 1段目の整合した点の数。2段目は点の数を求め直すので、その前の値を持つ（成り立つ下限と比べられる数）
    points: int


def _paper(found: tuple, features: tuple) -> _Paper:
    """1段目で成り立った (見本, 変換, 点) を、2段目で変換を求め直した用紙にする"""
    tpl, H, _ = align.refined(found, features)
    return _Paper(tpl, H, found[2])


def _first_paper(
    tpls: Sequence[align.Template],
    features: tuple,
    hint: FormSpec | None,
    group: Sequence[tuple[FormSpec, int]],
    hint_found: tuple | None,
) -> tuple[tuple | None, list[tuple]]:
    """1枚目の用紙：整合した点が最も多い (見本, 変換, 点) と、同じ用紙の部分で成り立った様式
    [(見本, 作業画像の上の変換か None, 点)]。どの様式とも成り立たなければ (None, [])"""
    by_spec = {t.spec: t for t in tpls}
    if hint is not None and (tpl := by_spec.get(hint)):
        H, inliers = (
            hint_found if hint_found is not None else align.register(tpl, features)
        )
        if H is not None:
            best = (tpl, H, inliers)
            # upright_group で同じ用紙の部分に成り立ったほかの様式。変換は、題名でその様式に決めたときだけ求め直す
            others = [
                (by_spec[s], None, n) for s, n in group if s != hint and s in by_spec
            ]
            return best, [best, *others]
    aligned = align.align_all(features, tpls)
    best = align.strongest(aligned)
    if best is None:
        return None, []
    return best, align.paper_group(best, aligned)


def _papers(
    gray: np.ndarray,
    engine: ocr.Engine,
    tpls: Sequence[align.Template],
    features: tuple,
    best: tuple,
    candidates: list[tuple],
) -> tuple[list[_Paper], tuple[FormSpec, ...], list[tuple[PaperNote, str]]]:
    """ページに写った用紙（1枚目と、その外に写ったほかの用紙）と、1枚目の類似した様式と、用紙についての注記
    [(種類, 文)]"""
    # 同じ用紙の部分に2つ以上の様式が成り立てば、題名で決める
    best, similar_note, similar = titles.choose(gray, features, candidates, engine)
    # 類似した様式がなければ、年分の違う帳票もその様式に決まる（扶養控除等申告書の令和7・8・9年分の公開様式は、
    # 個人番号欄の位置が同じで、令和7・8年分も令和9年分の見本と位置合わせが成り立つ）
    papers = [_paper(best, features)]
    quads = [align.page_quad(papers[0].tpl, papers[0].H)]
    notes = [(PaperNote.SIMILAR, similar_note)] if similar_note else []
    if align.quad_area(quads[0]) < COVER_FULL * gray.size:
        while paper.other_paper(gray, quads):
            if len(papers) >= MAX_FORMS:
                # 残りの用紙は位置合わせしていないので、様式を判別できない用紙（OTHER_PAPER_NOTE）とは言えない
                notes.append(
                    (
                        PaperNote.MANY,
                        i18n.tr(
                            "用紙が多いため、{count}枚を超える分は墨消ししていません",
                            count=MAX_FORMS,
                        ),
                    )
                )
                break
            more = None
            outside = paper.features_outside(features, quads, gray.shape)
            aligned = align.align_all(outside, tpls)
            if (strongest := align.strongest(aligned)) is not None:
                group_more = align.paper_group(strongest, aligned)
                chosen, more_note, _ = titles.choose(gray, outside, group_more, engine)
                more = _paper(chosen, outside)
                if more_note:
                    notes.append((PaperNote.SIMILAR, more_note))
            if more is None:
                notes.append((PaperNote.OTHER_PAPER, i18n.tr(OTHER_PAPER_NOTE)))
                break
            papers.append(more)
            quads.append(align.page_quad(more.tpl, more.H))
    if len(papers) > 1:
        notes.insert(
            0,
            (
                PaperNote.PAPERS,
                i18n.tr(
                    "帳票が{count}枚写っています（{names}）",
                    count=len(papers),
                    names=i18n.join(p.tpl.name for p in papers),
                ),
            ),
        )
    return papers, similar, notes


def _placed(gray: np.ndarray, papers: Sequence[_Paper]) -> _Placed:
    """用紙ごとの欄を、枠の線に合わせて置き、隣の行に置かれている見込みの高い欄を調べる（rowshift）"""
    fields, unframed, crossed, frameless, tilted, shifted = [], [], [], [], [], []
    on_page = not _is_photo(papers)
    for t, H, _ in papers:
        done = []
        for f in t.fields:
            pairs = frame_pairs(f)
            try:
                result = framed(gray, f, H, pairs, t.image, on_page=on_page)
            except Exception:  # noqa: BLE001  1つの欄の失敗で、ページ全体を失敗にしない
                # 移した位置のまま墨消しし、「枠の線を見つけられませんでした」の要確認にする
                traceback.print_exc()
                result = _unframed(f, H, pairs)
            done.append(result)
        done = _unbent(gray, t, H, done, on_page)
        if not on_page:
            frameless += [f.name for f in t.fields if not frame_pairs(f)]
        for got in done:
            fields.append(got.field)
            if got.frame.missing or got.unsure:
                unframed.append(got.field.name)
            if got.crossed:
                crossed.append(got.field.name)
            # 4辺とも線で決めて四隅にした欄には付けない（範囲の辺が線に沿うので、線と辺のあいだにすき間はほとんどできない）。
            # 上下の線だけを探す欄は、四隅にしても、左右の辺が線で確かめていない位置なので付ける
            if got.tilt > TILT_REVIEW and not _four_sides(got):
                tilted.append(got.field.name)
        try:
            shifted += rowshift.shifted(gray, t, H, [got.field for got in done])
        except Exception:  # noqa: BLE001  調べられなくても、置いた範囲はそのまま使える
            traceback.print_exc()
    return _Placed(fields, unframed, crossed, frameless, tilted, shifted, not on_page)


def _four_sides(got: Framed) -> bool:
    """範囲を、4辺とも見つけた線の中心を結ぶ四隅にした欄か"""
    return got.field.corners is not None and set(got.frame.found) == set(frames.PAIRS)


def _field_notes(placed: _Placed) -> list[str]:
    """欄についての注記：範囲が欄に合っていないおそれのある欄の名前を、1つの文に挙げる。名前は、確かめる必要の高い順
    （線を越えた記入 → 隣の行に置かれている見込みの高い欄 → 線が見つからない欄 → 写真の枠のない欄 → 線が傾いた欄）に、
    重ねずに並べる"""
    names = _merged(
        placed.crossed,
        placed.shifted,
        placed.unframed,
        placed.frameless,
        placed.tilted,
    )
    if not names:
        return []
    # 「すべての欄」とするかは、ページの全部の欄の名前（同じ様式が2枚写っていれば同じ名前が重なる）と数で決める
    return [field_note(names, [f.name for f in placed.fields])]


def _merged(*groups: Sequence[str]) -> list[str]:
    """欄の名前の一覧をまとめる。名前は初めに出てきた順で、数は一覧ごとの数の多いほう（同じ様式が2枚写っていると、
    同じ名前の欄が2つあるため）"""
    counts: dict[str, int] = {}
    for group in groups:
        for name in dict.fromkeys(group):
            counts[name] = max(counts.get(name, 0), list(group).count(name))
    return [name for name, n in counts.items() for _ in range(n)]


def below_limit(loc: Located, below: variants.Below) -> Located:
    """様式不明のままになったページの結果 loc に、罫線の一致度が下限に届かなかった候補を持たせたもの"""
    spec, match = below
    value = BelowLimit(
        spec.id, spec.revision, match.template, match.page, spec.line_match_min
    )
    return replace(loc, line_below=value)


def from_variant(v: variants.Variant, other_paper: bool = False) -> Located:
    """バリエーションと決めた結果（variants.judge・relocate）を、ページの判別の結果にする。罫線の対応で置いた範囲は、
    見本と画素で合わせていないので、いつも要確認にする。枠の線を越えた記入がある欄は、欄の文を続けて、欄の名前を
    挙げる（枠の線が見つからない欄の名前は挙げない。バリエーションの文が、全部の範囲がずれているおそれを言っている）。
    other_paper は、様式を判別できない用紙も写っているか（OTHER_PAPER_NOTE を先に付ける）"""
    notes = [i18n.tr(OTHER_PAPER_NOTE)] if other_paper else []
    notes.append(i18n.tr(VARIANT_NOTE, name=v.spec.name))
    if v.crossed:
        notes.append(field_note(v.crossed, [f.name for f in v.fields]))
    return Located(
        form=v.spec.name,
        fields=list(v.fields),
        note=i18n.tr("。", context="注記の区切り").join(notes),
        used=(v.spec,),
        unframed=v.unframed,
        crossed=v.crossed,
        paper_notes=other_paper,
        other_paper_only=other_paper,
        variant=True,
        line_match=tuple(v.match),
    )


def other_paper_beside(
    gray: np.ndarray, quad: np.ndarray, cover: np.ndarray | None = None
) -> bool:
    """作業画像 gray で、用紙1枚の四隅 quad の外に、印字のある用紙が写っているか（位置合わせできた用紙で調べるときと
    同じ判定）。用紙が作業画像の COVER_FULL 以上を覆うか（覆えば調べない）は、cover（省くと quad）で見る。quad を
    帳票自身の余白の印刷まで広げたときに、広げた分で COVER_FULL を超えて、調べずに済ませないため"""
    cover = quad if cover is None else cover
    return align.quad_area(cover) < COVER_FULL * gray.size and paper.other_paper(
        gray, [quad]
    )


# 枠の線と、枠の線を越えた記入は、欄の周り（欄から FRAME_PAD 画素まで）の画像で探す。
# ページの上の帳票が見本とほぼ同じ大きさ（倍率が 1 ± SAME_SIZE）なら、ページの画像をそのまま使う（スキャン・PDF）。
# 見本の座標に移した画像で探すと、スキャンから作った見本のゆがみ（見本のスキャンでは約0.3〜0.5度の傾き）が線に移り、
# 線に接したり線を越えたりする数字がある欄で、線を見つけられないことが増えるため。
# 大きさが違う（写真など）ときは、見本の座標に移した画像で、200dpi のページと同じ画素の数で探す。
# 写真の長辺の50%に写った扶養控除等申告書をページの上で探すと、枠の線と、そのすぐ下の注意書きの文字の行を
# 1本の太い線とみなした
# 線を探すときに欄の周りに足す余白（画素。ページの画像で探すときは作業画像の画素、見本の座標に移した画像で探すときは
# 見本の座標の画素）。線を探す範囲・線が欄の外まで続くかを見る所・越えた記入を探す帯が収まる大きさ
FRAME_PAD = frames.DEPTH_V + max(frames.OUTSIDE[1], frames.REACH) + 4
SAME_SIZE = 0.1
# 写真と判定したページで、上か下の線の中心が、線の両端の平均の位置より外に出る量（ページの作業画像の画素）がこれを
# 超える欄に要確認を付ける。写真の作業画像の線の太さは約2画素で、長方形の範囲では、中心がこれ（太さの半分）を超えて
# 外に出ると、線の内側の縁と範囲の辺のあいだにすき間ができ、線に接して書いた記入の端が墨消しの外に出るため。4辺とも
# 線の中心を結ぶ四隅にした欄には付けない（範囲の辺が線に沿うので、すき間はほとんどできない。欄の片方の端で、辺が線の
# 内側の縁より2画素ほど内側に入ることはある）。上下の線だけを探す欄は、四隅に
# しても付ける（左右の辺は線で確かめていない位置で、曲がった用紙では左右の端の外に記入が残ることがあるため）
TILT_REVIEW = 1.0


def _is_photo(found: Sequence[tuple]) -> bool:
    """位置合わせできた帳票（[(見本, 変換, 点)]）のどれかの欄の倍率が 1 ± SAME_SIZE を外れるか（写真と判定する）。
    写真と判定したページでは、全欄の線を見本の座標に移した画像で探す。写真では遠近で欄ごとに倍率が変わり、倍率が
    1 に近い欄もページの上では線が傾いているので、欄ごとに探す画像を選ぶと、見本の座標でなら見つかる組を落とすため"""
    return any(abs(_scale(f, H) - 1) > SAME_SIZE for t, H, _ in found for f in t.fields)


# 曲がりの補正で欄が動いた量の上限：同じ大きさの欄の向きに、その欄までの距離のこの割合。同じ大きさの欄は枠の線も
# 同じなので、補正で欄がその欄の位置まで動くと、そこで線がすべて見つかり、「線が見つからない」要確認が付かないまま、
# 元の欄の記入が墨消しの外に出る。隣の欄の線が見つかるのは、隣の欄の線の中心が、動いた先の欄の辺から線を探す幅
# （横の線は frames.DEPTH_H。縦の線は DEPTH_V）以内に入ったときで、同梱の様式で最も近い組（中心の距離56画素）では、
# その向きに0.86倍以上動いたとき。動いた量は、その欄の向きの成分で測る。写真の強い曲がりでは、正しい補正でも、
# 隣の欄までの距離ほど横に動くことがあるため。この値は、線を探す幅が frames.DEPTH_H のときのもので、幅を広げて
# 探し直すとき（BENT_DEPTHS_H）は、幅ごとに、隣の欄の線が入らないことを別に確かめる（_room_to_same_size）
BEND_LIMIT = 0.8
# 写真と判定したページで、曲がりの補正の後も上と下の線が見つからない欄（補正の変換を求められなかった欄を含む）の、
# 線を探し直す範囲（frames.find の depth_h。
# 順に試し、初めに見つかった線を使う）。補正の後の位置が本当の線から frames.DEPTH_H の前後までずれて残ることがある
# ため。1つの広い値にしないのは、線が見つかるかが範囲の広さに対して一様でないため（同じ欄で、10で見つかり、12で
# 見つからず、16で見つかる例がある）。スキャンのページで探し直さないのは、行の高さが見本と違う用紙で、広げた範囲に
# 入った隣の行の線に合い、範囲がずれたまま「線が見つからない」要確認が付かなくなるため。
# 写真でも、補正で動いた後の欄から同じ大きさの欄までの残りの距離が「探す範囲 + frames.DEPTH_H」より小さい範囲では
# 探さない（その範囲に隣の欄の線が入る。16画素まで広げると、最も近い組（56画素）では、0.71倍動いただけで入る）
BENT_DEPTHS_H = (10, 12, 16)
# 曲がりの補正の変換を求められなかった欄を、線を探す範囲を広げて探し直して見つけた上と下の線の縁（内側と外側）が、移した欄の
# 辺からこれ（見本の座標の画素）を超えて離れていたら、範囲は線に合わせ、線が見つからない欄として名前を挙げる（_searched_wider）。
# ふつうの範囲で上と下の線を探す深さ（frames.DEPTH_H）と同じ値にした。その中の線は、位置合わせの変換で移した欄の線と
# みなせるが、その外の線は、欄でない同じ高さの隣の行の線のこともあるため。扶養控除等申告書の「B 源泉控除対象親族 1」を
# 46画素下にずらした写真では、見つけた線の縁が11画素離れ、基・配・特・所の申告書のずらさない「配偶者」では2画素だった
WIDER_APART = 8


def _same_size_moves(
    i: int, fields: Sequence[Field], H: np.ndarray, Hf: np.ndarray
) -> list[tuple[float, float]]:
    """曲がりの補正の変換 Hf で、欄 fields[i] の中心が、同じ大きさの欄それぞれの向きに動いた量：
    [(その欄までの距離に対する割合, その欄までの距離)]

    動きは、Hf で移した四隅を変換 H で見本の座標に戻して測る（補正は四隅を見本の座標で動かすので、写真の倍率に
    よらない。距離も見本の座標）。同じ大きさは、fields[i] が線を探す組の向きの大きさ（上下の組なら高さ、左右の組
    なら幅）の差が、線の組として受け入れる差（frames.SIZE_TOL + frames.OVERDRAWN）以下のもの。それより大きさが
    違う欄の線は、組にならないため。線を探さない欄（枠のない欄）と、同じ大きさの欄がない欄は、空。
    """
    f = fields[i]
    pairs = frame_pairs(f)
    if not pairs:
        return []
    corners = np.float64(
        [[[f.x, f.y]], [[f.x + f.w, f.y]], [[f.x + f.w, f.y + f.h]], [[f.x, f.y + f.h]]]
    )
    back = cv2.perspectiveTransform(
        cv2.perspectiveTransform(corners, Hf), np.linalg.inv(H)
    )
    shift = (back - corners).reshape(-1, 2).mean(axis=0)
    tol = frames.SIZE_TOL + frames.OVERDRAWN
    out = []
    for j, g in enumerate(fields):
        if j == i:
            continue
        if "top_bottom" in pairs and abs(g.h - f.h) > tol:
            continue
        if "left_right" in pairs and abs(g.w - f.w) > tol:
            continue
        to = np.array([g.x + g.w / 2 - f.x - f.w / 2, g.y + g.h / 2 - f.y - f.h / 2])
        out.append((float(shift @ to) / float(to @ to), float(np.hypot(*to))))
    return out


def _toward_same_size(
    i: int, fields: Sequence[Field], H: np.ndarray, Hf: np.ndarray
) -> bool:
    """曲がりの補正の変換 Hf で、欄 fields[i] の中心が、同じ大きさの欄のどれかの向きに、その欄までの距離の
    BEND_LIMIT 倍以上動いたか（_same_size_moves）"""
    return any(k >= BEND_LIMIT for k, _ in _same_size_moves(i, fields, H, Hf))


def _room_to_same_size(
    i: int, fields: Sequence[Field], H: np.ndarray, Hf: np.ndarray
) -> float:
    """曲がりの補正の変換 Hf で動いた後の欄 fields[i] から、同じ大きさの欄までの残りの距離のうち、いちばん小さいもの
    （見本の座標の画素。その欄の向きの成分。同じ大きさの欄がなければ無限大）"""
    return min(
        (d * (1 - k) for k, d in _same_size_moves(i, fields, H, Hf)), default=math.inf
    )


def _unbent(
    gray: np.ndarray, t: align.Template, H: np.ndarray, done: list, on_page: bool
) -> list:
    """見本 t の欄ごとの framed の結果 done のうち、線が見つからない辺のある欄と、写真と判定したページ（on_page が
    False）の枠のない欄を、用紙の曲がりを補った欄ごとの変換（bend.transforms）で求め直したもの

    線がすべて見つかった欄は、線で位置を確かめられたので変えない。求め直す欄がなければ、区画を照合しない（照合は
    ページ全体の区画で行い、1ページの解析がスキャンで約2割、写真で約3割延びるため）。求め直せなかった欄と、
    補正で同じ大きさの欄の向きに、その欄までの距離の BEND_LIMIT 倍以上動いた欄は、今の変換の結果のままにする。
    写真と判定したページでは、求め直しても上と下の線が見つからない欄と、補正の変換を求められなかった欄を、線を
    探す範囲を BENT_DEPTHS_H の順に広げて探し直す（_searched_wider）。
    """
    redo = [
        i
        for i, (f, got) in enumerate(zip(t.fields, done))
        if got.frame.missing or (not frame_pairs(f) and not on_page)
    ]
    if not redo:
        return done
    try:
        moved = bend.transforms(gray, t.image, H, [t.fields[i] for i in redo])
    except Exception:  # noqa: BLE001  補えなくても、今の変換の結果で墨消しする
        traceback.print_exc()
        return done
    done = list(done)
    for i, Hf in zip(redo, moved):
        f = t.fields[i]
        pairs = frame_pairs(f)
        try:
            if Hf is None:
                # 補正の変換を求められなかった欄は、今の変換のまま、範囲だけを広げて探し直す
                if not on_page:
                    done[i] = _searched_wider(gray, t, i, H, H, done[i], unbent=True)
                continue
            if _toward_same_size(i, t.fields, H, Hf):
                continue  # 同じ大きさの欄の位置へ動いたおそれがあるので、補正を使わない
            result = framed(gray, f, Hf, pairs, t.image, on_page=on_page)
            if not on_page:
                result = _searched_wider(gray, t, i, H, Hf, result)
            done[i] = result
        except Exception:  # noqa: BLE001  1つの欄の失敗で、ページ全体を失敗にしない
            traceback.print_exc()
    return done


def _searched_wider(
    gray: np.ndarray,
    t: align.Template,
    i: int,
    H: np.ndarray,
    Hf: np.ndarray,
    result: Framed,
    unbent: bool = False,
) -> Framed:
    """写真と判定したページで、見本 t の欄 i を変換 Hf で移して上と下の線が見つからなかった結果 result を、線を探す
    範囲を BENT_DEPTHS_H の順に広げて探し直したもの（見つからなければ result のまま）。Hf は曲がりの補正の変換で、
    求められなかった欄（unbent）では位置合わせの変換 H。広げた範囲に同じ大きさの隣の欄の線が入る広さでは、探さない。

    unbent の欄で、見つけた上と下の線の縁が、移した欄の辺から WIDER_APART を超えて離れていたら（_apart）、範囲は
    線に合わせ、線が見つからない欄として名前を挙げる（unsure）。補正で動いた量がないので、同じ大きさの欄までの距離は
    位置合わせの変換の誤差を含まず、広げた範囲に、欄でない同じ高さの隣の行（扶養控除等申告書の B 欄のすぐ下の
    生年月日の行など）の線が入っても分からないため。位置合わせの変換の誤差が大きいときは、正しい欄の線に置けた欄にも
    名前が挙がることがある（要確認が余計に付く向き）"""
    f = t.fields[i]
    pairs = frame_pairs(f)
    if "top_bottom" not in pairs or "top_bottom" in result.frame.found:
        return result
    room = _room_to_same_size(i, t.fields, H, Hf)
    for depth in BENT_DEPTHS_H:
        if room < depth + frames.DEPTH_H:
            break
        wider = framed(gray, f, Hf, pairs, t.image, on_page=False, depth_h=depth)
        if "top_bottom" in wider.frame.found:
            if unbent and _apart(f, wider.frame) > WIDER_APART:
                return wider._replace(unsure=True)
            return wider
    return result


def _apart(f: Field, frame: frames.Frame) -> float:
    """見本の座標に移した欄の周りの画像（_framed_on_template。欄は FRAME_PAD から始まる）で見つけた上と下の線の縁
    （内側と外側）のうち、移した欄の上と下の辺から最も離れたものの距離（見本の座標の画素）。線の中心でなく縁で測る
    のは、太い線ほど中心より縁のほうが辺から離れるので、中心で測るより離れていると判断しやすく、広げて見つけた線が
    隣の行の線かもしれないときに、名前を挙げる側に倒すため"""
    top = max(abs(v - FRAME_PAD) for v in frame.top)
    bottom = max(abs(v - (FRAME_PAD + f.h)) for v in frame.bottom)
    return max(top, bottom)


def framed(
    gray: np.ndarray,
    f: Field,
    H: np.ndarray,
    pairs: tuple[str, ...],
    template: np.ndarray | None = None,
    inside: bool = False,
    on_page: bool | None = None,
    depth_h: int = frames.DEPTH_H,
) -> Framed:
    """見本の座標の欄 f を変換 H でページに移し、印刷された枠の線（の中心）に合わせたものと、見つけた枠の線と、
    枠の線を越えた記入がある辺と、線の中心が範囲の辺より外に出る量

    pairs は線を探す組（formstore.frame_pairs）。見つからない辺と、探さない組の辺は、移した四隅を囲む長方形
    （move_field）の辺のまま。返す範囲の frame は、線が見つかった組。template（見本の画像。Template.image）を渡すと、
    線を探した画像で、枠の線を越えた記入を探す（frames.crossings）。inside は frames.find の inside（記入のない
    用紙で、内側に描いた範囲の線も探す。新しい用紙への置き換えだけで使う）。on_page は線を探す画像で、True なら
    ページの画像、False なら見本の座標に移した画像。None なら、欄の倍率が 1 ± SAME_SIZE ならページの画像にする。
    depth_h は、上と下の線を探す範囲（frames.find の depth_h）。
    線の中心が範囲の辺より外に出る量は、見本の座標で探したときに、見つけた上と下の線が変換 H でページの上で傾き、
    線の両端のどちらかで、線の中心が範囲の辺（両端の平均の位置）より外に出る量の大きいほう（ページの画素）。
    ページの画像で探したときと、上と下の線が見つからないときは 0。
    """
    if not pairs:  # 枠のない範囲：線を探さない（関係のない線に合わせない）
        return _unframed(f, H, ())
    if on_page is None:
        on_page = abs(_scale(f, H) - 1) <= SAME_SIZE
    if on_page:
        return _framed_on_page(gray, f, H, pairs, template, inside, depth_h)
    return _framed_on_template(gray, f, H, pairs, template, inside, depth_h)


def _unframed(f: Field, H: np.ndarray, pairs: tuple[str, ...]) -> Framed:
    """線を探さずに、移した位置のままにした欄（探す組の辺は、線が見つからないとする）"""
    none = frames.Frame(None, None, None, None, pairs)
    return Framed(replace(move_field(f, H), frame=()), none, (), 0.0)


def _checked(
    field: Field, frame: frames.Frame, moved: Field
) -> tuple[Field, frames.Frame]:
    """線に合わせた欄の幅か高さが1画素未満になった組は、線が見つからなかったものとし、移した位置に戻す
    （墨消しの大きさが0の範囲を作らないため）"""
    edge = {
        "left": field.x,
        "top": field.y,
        "right": field.x + field.w,
        "bottom": field.y + field.h,
    }
    lost = {}
    for a, b, start, size in (
        ("left", "right", moved.x, moved.w),
        ("top", "bottom", moved.y, moved.h),
    ):
        if edge[b] - edge[a] < 1:
            edge[a], edge[b] = start, start + size
            lost[a] = lost[b] = None
    if not lost:
        return field, frame
    frame = replace(frame, **lost)
    checked = Field(
        field.name,
        edge["left"],
        edge["top"],
        edge["right"] - edge["left"],
        edge["bottom"] - edge["top"],
        frame.found,
    )
    return checked, frame


def _framed_on_page(gray, f, H, pairs, template, inside=False, depth_h=frames.DEPTH_H):
    """framed のうち、ページの画像で探すもの。線を探す基準は、移した欄の辺ごとの両端の平均の位置とする
    （移した四隅を囲む長方形は、変換のわずかな傾きの分だけ外に広がり、外まで描いた範囲で線が探す範囲から外れた）"""
    moved = move_field(f, H)
    height, width = gray.shape
    if (
        moved.x >= width
        or moved.y >= height
        or moved.x + moved.w <= 0
        or moved.y + moved.h <= 0
    ):
        # 欄が画像の外（帳票の下か右が切れて写っている）。線を探さず、移した位置のままにする
        return _unframed(f, H, pairs)
    pts = np.float32(
        [[[f.x, f.y], [f.x + f.w, f.y], [f.x + f.w, f.y + f.h], [f.x, f.y + f.h]]]
    )
    tl, tr, br, bl = cv2.perspectiveTransform(pts, H)[0]
    left, right = (tl[0] + bl[0]) / 2, (tr[0] + br[0]) / 2
    top, bottom = (tl[1] + tr[1]) / 2, (bl[1] + br[1]) / 2
    pad = FRAME_PAD
    x0, y0 = max(int(moved.x) - pad, 0), max(int(moved.y) - pad, 0)
    x1 = min(math.ceil(moved.x + moved.w) + pad, gray.shape[1])
    y1 = min(math.ceil(moved.y + moved.h) + pad, gray.shape[0])
    crop = gray[y0:y1, x0:x1]
    rect = (float(left - x0), float(top - y0), float(right - left), float(bottom - top))
    frame = frames.find(crop, rect, pairs, inside, depth_h=depth_h)
    crossed = ()
    if template is not None:
        to_crop = np.array([[1.0, 0, -x0], [0, 1.0, -y0], [0, 0, 1.0]]) @ H
        crossed = frames.crossings(
            crop, rect, frame, _template_crop(template, to_crop, crop.shape)
        )
    edge = {
        "left": moved.x,
        "top": moved.y,
        "right": moved.x + moved.w,
        "bottom": moved.y + moved.h,
    }
    for side in frames.SIDES:
        if (line := frame.side(side)) is not None:
            edge[side] = (line[0] + line[1]) / 2 + (
                x0 if side in ("left", "right") else y0
            )
    field = Field(
        f.name,
        edge["left"],
        edge["top"],
        edge["right"] - edge["left"],
        edge["bottom"] - edge["top"],
        frame.found,
    )
    field, frame = _checked(field, frame, moved)
    return Framed(field, frame, crossed, 0.0)


def _framed_on_template(
    gray, f, H, pairs, template, inside=False, depth_h=frames.DEPTH_H
):
    """framed のうち、欄の周りを見本の座標に移した画像で探すもの。見つけた線の中心をページの上に戻す。
    線が見つかった辺は、辺の両端を移した位置の平均にする"""
    pad = FRAME_PAD
    x0, y0 = f.x - pad, f.y - pad
    w, h = round(f.w + 2 * pad), round(f.h + 2 * pad)
    to_page = H @ np.array([[1.0, 0, x0], [0, 1.0, y0], [0, 0, 1.0]])
    crop = cv2.warpPerspective(
        gray, to_page, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderValue=255,
    )  # fmt: skip
    rect = (pad, pad, f.w, f.h)
    frame = frames.find(crop, rect, pairs, inside, blurred=True, depth_h=depth_h)
    crossed = ()
    if template is not None:
        to_crop = np.array([[1.0, 0, -x0], [0, 1.0, -y0], [0, 0, 1.0]])
        crossed = frames.crossings(
            crop, rect, frame, _template_crop(template, to_crop, crop.shape)
        )
    moved = move_field(f, H)
    edge = {
        "left": moved.x,
        "top": moved.y,
        "right": moved.x + moved.w,
        "bottom": moved.y + moved.h,
    }
    fx, fy, fw, fh = frames.fit(rect, frame)
    corners = np.float32(
        [[fx, fy], [fx + fw, fy], [fx + fw, fy + fh], [fx, fy + fh]]
    ) + np.float32([x0, y0])
    tl, tr, br, bl = cv2.perspectiveTransform(corners[None], H)[0]
    ends = {
        "left": (tl, bl, 0),
        "right": (tr, br, 0),
        "top": (tl, tr, 1),
        "bottom": (bl, br, 1),
    }
    for side in frames.SIDES:
        if frame.side(side) is not None:
            a, b, k = ends[side]
            edge[side] = float((a[k] + b[k]) / 2)
    field = Field(
        f.name,
        edge["left"],
        edge["top"],
        edge["right"] - edge["left"],
        edge["bottom"] - edge["top"],
        frame.found,
    )
    field, frame = _checked(field, frame, moved)
    tilt = max(
        (abs(float(b[1] - a[1])) / 2 for side, a, b in (("top", tl, tr), ("bottom", bl, br))
         if frame.side(side) is not None),
        default=0.0,
    )  # fmt: skip
    if not frame.missing:
        # 探した組の線がすべて見つかった欄は、墨消しの範囲を線の中心を結ぶ四隅にする（辺を両端の平均の位置に置いた
        # 長方形では、遠近で傾いた線と辺のあいだに、欄の内側の記入が残るか、線の外の隣の欄を塗るため）
        corners = tuple((float(x), float(y)) for x, y in (tl, tr, br, bl))
        field = replace(field, corners=corners)
    return Framed(field, frame, crossed, tilt)


def _scale(f: Field, H: np.ndarray) -> float:
    """変換 H の、欄 f の中心での倍率（作業画像の上の大きさ ÷ 見本の座標での大きさ。面積の比の平方根）"""
    cx, cy = f.x + f.w / 2, f.y + f.h / 2
    step = 100.0
    pts = np.float32([[[cx, cy], [cx + step, cy], [cx, cy + step]]])
    o, ax, ay = cv2.perspectiveTransform(pts, H)[0]
    (a, c), (b, d) = ax - o, ay - o
    return math.sqrt(abs(float(a * d - b * c))) / step


def _template_crop(
    image: np.ndarray, to_crop: np.ndarray, shape: tuple[int, ...]
) -> np.ndarray:
    """見本の画像（TEMPLATE_SCALE の解像度）を、変換 to_crop（見本の座標 → 切り出した画像の座標）で、
    大きさ shape（高さ, 幅）の画像に移したもの（画素の中心を合わせる）"""
    k = 1 / TEMPLATE_SCALE
    c = (k - 1) / 2
    M = to_crop @ np.array([[k, 0, c], [0, k, c], [0, 0, 1.0]])
    return cv2.warpPerspective(
        image, M, (shape[1], shape[0]), flags=cv2.INTER_LINEAR, borderValue=255
    )


def _all_fields(names: Sequence[str], every: Collection[str]) -> bool:
    """要確認の文で、欄の名前を並べず「すべての欄」とするか：ページの全部の範囲（every。2つ以上）にあたるとき
    （線が消えた白黒2値の扶養控除等申告書では10欄の名前が並ぶため）。names と every は欄ごとの名前で、同じ様式が
    2枚写っていれば同じ名前が重なる。数でも比べるので、2枚のうち1枚の欄だけなら「すべて」にしない"""
    return (
        len(names) > 1 and len(names) == len(every) and sorted(names) == sorted(every)
    )


def _listed(names: Sequence[str]) -> dict:
    """要確認の文に並べる名前（重なりを除き、出てきた順）と数"""
    unique = list(dict.fromkeys(names))
    return {"names": i18n.quoted(unique), "count": len(unique)}


def field_note(names: Sequence[str], every: Collection[str]) -> str:
    """範囲が欄に合っていないおそれのある欄の「⚠要確認」の文（枠の線を越えた記入、隣の行に置かれている見込みの高い
    欄、枠の線が見つからない欄、写真の枠のない欄、写真で線が傾いた欄）。欄の名前は「」で囲んで並べる。何を確かめるかや
    操作の案内は書かない（ラベルが「要確認」で、起きているおそれのあることだけを書く）"""
    if _all_fields(names, every):
        return i18n.tr("すべての欄の範囲が、欄に合っていないおそれがあります")
    return i18n.tr(
        "{names}の欄の範囲が、欄に合っていないおそれがあります", **_listed(names)
    )


def fit_to_frame(
    base: np.ndarray,
    base_is_work: bool,
    rect: tuple[float, float, float, float],
    pairs: tuple[str, ...] = tuple(frames.PAIRS),
) -> tuple[tuple[float, float, float, float], tuple[str, ...]]:
    """範囲を描いた元の画像で、範囲に印刷された枠の線を探し、線が見つかった組の辺を線の中心に合わせた範囲と、
    線が見つかった組を返す（範囲を描く画面）。pairs は探す組

    範囲を描く画面は、チェックがあれば、描いた範囲と大きさを変えた範囲を、返した範囲にする（配布用パッケージの3様式と同じ位置に
    する）。内側に描いたまま保存すると、解析で、欄の端に寄せて書いた数字の縦の画を線に選んだり、線の組が大きさの
    許しから外れて線を探さなくなったりして、数字の一部が墨消しの外に残ることがあるため。線は、範囲が探す範囲
    （frames.DEPTH_V・DEPTH_H）の分まで内側に描かれていても探す（frames.find の inside）。合わせないときも、見つかった
    組を記録にする。
    base_is_work なら base は作業画像。そうでなければ base は見本の画像で、範囲の周りを作業画像の大きさに拡大して
    調べる。見本の画像は、範囲の中を塗っても範囲の辺に沿った枠の線を残してある（formcheck.fill_ranges）。前の
    バージョンで保存した見本の画像は、範囲にかかる枠の線も塗ってあるので、線は見つからない（範囲は動かさない）。
    """
    if base_is_work:
        frame = frames.find(base, rect, pairs, inside=True)
        return frames.fit(rect, frame), frame.found
    x, y, w, h = rect
    pad = FRAME_PAD
    s = TEMPLATE_SCALE
    x0, y0 = max(math.floor((x - pad) * s), 0), max(math.floor((y - pad) * s), 0)
    x1 = min(math.ceil((x + w + pad) * s), base.shape[1])
    y1 = min(math.ceil((y + h + pad) * s), base.shape[0])
    if x1 <= x0 or y1 <= y0:  # 範囲が見本の画像の外（空の配列を OpenCV に渡さない）
        return rect, ()
    crop = cv2.resize(
        base[y0:y1, x0:x1], None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_LINEAR
    )
    dx, dy = x0 / s, y0 / s
    local = (x - dx, y - dy, w, h)
    frame = frames.find(crop, local, pairs, inside=True)
    fx, fy, fw, fh = frames.fit(local, frame)
    return (fx + dx, fy + dy, fw, fh), frame.found


def move_field(f: Field, H: np.ndarray) -> Field:
    """見本の座標の欄を、作業画像の上に移す（移した四隅を囲む長方形にする）"""
    pts = np.float32(
        [[[f.x, f.y]], [[f.x + f.w, f.y]], [[f.x + f.w, f.y + f.h]], [[f.x, f.y + f.h]]]
    )
    q = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    x0, y0 = q.min(axis=0)
    x1, y1 = q.max(axis=0)
    return Field(f.name, float(x0), float(y0), float(x1 - x0), float(y1 - y0))
