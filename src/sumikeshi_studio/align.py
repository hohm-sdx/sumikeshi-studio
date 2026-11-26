# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""様式の見本の画像とページの位置合わせと、位置合わせで決める向き

様式の見本の画像とページの特徴点を対応させ、見本からページへの変換を求める（2段で求める）。
向きを直す前のページで位置合わせが成り立てば、その変換から90度単位の向きを決める（upright_group）。
見本の画像の特徴点は、解析のプロセスで画像のファイルごとに1回だけ求めて覚えておく（_loaded）。
ほかのモジュールは、ここの関数を `from . import align` で読み込み、`align.register(…)` のようにモジュールを
通して呼ぶ（モジュールを通して呼ぶと、テストで関数を置き換えたときに、置き換えたものが呼ばれるため）。

座標の呼び方（解析の部分のコメントで共通）：
- 見本の座標：様式の欄（formstore.Field）の座標。見本の画像を作業画像の解像度（WORK_DPI）にした大きさで、変換 H の
  入力の座標
- 見本の画像の画素：見本の画像（Template.image。TEMPLATE_SCALE の解像度で、作業画像の半分）の画素
- 作業画像の座標：ページの向きと傾きを直した作業画像（WORK_DPI）の座標で、変換 H の出力の座標
"""

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from . import formstore
from .formstore import (
    MAX_IMAGE_BYTES,
    TEMPLATE_SCALE,
    Field,
    FormError,
    FormSpec,
)
from .i18n import tr

ORB_FEATURES = 6000  # 位置合わせに使う特徴点の数
MATCH_RATIO = 0.75  # 対応する特徴点の選び方（1番目と2番目に近い点の距離の比）
RANSAC_PX = 3.0  # 対応の整合を判断する距離（見本の画像の画素。作業画像では2倍）
# 位置合わせが成り立ったとみなす、整合した特徴点の数（1段目）。配布用パッケージの3様式で、回帰テストの公開様式の試験画像
# （6様式 × 5通りの傾き。記入なし）では、同じ種類の組が1,045〜2,523点、違う種類の組と見本のない用紙（従たる給与の
# 申告書）の組が最大103点。見本のスキャンは、1〜3ページが775〜1,056点、4ページ（様式不明）が0点
MIN_INLIERS = 150
# 位置合わせの2段目（_refine）。1段目の変換で見本の特徴点をページに移し、移した位置からこの範囲
# （見本の画像の画素。作業画像ではこの2倍）にあるページの特徴点の中だけで対応を選び直して、変換を求め直す。
# 範囲を狭めて2回行う。保険料控除申告書の白黒2値では、1段目の欄のあたりのずれが最大33画素（作業画像）だったので、
# 1回目は48画素（作業画像）まで探す。近くの中で、いちばん近い点の距離が2番目のこの割合未満で、
# 距離（ハミング距離）がこの上限以下のものを対応にする
REFINE_RADII = (24, 10)
REFINE_RATIO = 0.8
REFINE_MAX_DISTANCE = 80
# 見本のページを移したときの面積の比（大きさが大きく違えば失敗とみなす）。下限は、写真の中に小さく
# 写った帳票を受け付けるため 0.2 とする（写真の長辺の50%に写り、台形のゆがみが15%のとき 0.23）。
# 0.7 にすると、長辺の85%以下に写った帳票を位置合わせできない
AREA_RATIO = (0.2, 1.4)

# 類似した様式。2つの様式の見本の四隅の重なりの面積を、小さいほうの
# 面積で割った値がこれ以上なら、同じ用紙の部分とする（令和8年分と令和9年分の扶養控除等申告書では、31件とも1.0）
SAME_PAPER = 0.5


# ---------------------------------------------------------------- 見本


@dataclass(frozen=True, eq=False)
class Template:
    spec: FormSpec
    image: np.ndarray  # 見本の画像（TEMPLATE_SCALE の解像度、グレー）
    keypoints: tuple
    descriptors: np.ndarray

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def fields(self) -> tuple[Field, ...]:
        """見本の座標の墨消しする範囲"""
        return self.spec.fields


# (見本の画像のパス, 版, 保存した日時, 保存した人) → (見本の画像, 特徴点, 記述子)。登録した様式は版ごとに画像の
# ファイル名が違うため、ほかの人が様式を保存しても取り違えない。版と保存した日時・保存した人も鍵に入れ、同じ名前の画像
# が書き直されても前の画像を使わないようにする。
# 一度読んだ画像は、その後に様式のフォルダにつながらなくなっても使える
_loaded: dict[tuple, tuple] = {}


def templates(specs: Sequence[FormSpec]) -> list[Template]:
    """様式の見本を返す。見本の画像を読めなければ FormError"""
    return [Template(s, *_template_image(s)) for s in specs]


def _template_image(spec: FormSpec) -> tuple:
    path = spec.image_path()
    key = (str(path), spec.revision, spec.updated_at, spec.updated_by)
    if key not in _loaded:
        image = read_template_image(spec)
        kp, desc = orb().detectAndCompute(image, None)
        _loaded[key] = (image, kp, desc)
    return _loaded[key]


def read_template_image(spec: FormSpec) -> np.ndarray:
    """様式の見本の画像（TEMPLATE_SCALE の解像度、グレー）を読む。読めなければ FormError

    様式の一覧を読むときに最後まで確かめた画像でも、その後に置き換わることがあるので、読むたびに、ファイルの大きさを
    確かめ、読んだ同じバイト列を PNG として最後まで確かめてから（formstore.check_png）OpenCV に渡す（PNG でない画像を、
    OpenCV の中のほかの形式の部品に渡さないため。展開すると大きくなる画像を OpenCV で展開すると、数十秒かかるため）"""
    path = spec.image_path()
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:  # 大きすぎる画像は読まない
            raise FormError(
                tr(
                    "様式「{name}」の見本の画像が大きすぎます。（{path}）",
                    name=spec.name,
                    path=path,
                )
            )
        raw = path.read_bytes()
    except OSError as e:
        raise FormError(
            tr(
                "様式「{name}」の見本の画像を読めません。（{path}）",
                name=spec.name,
                path=path,
            )
        ) from e
    image = None
    try:
        formstore.check_png(raw)
    except formstore.PngError:
        pass
    else:
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FormError(
            tr(
                "様式「{name}」の見本の画像を、画像として読めません。（{path}）",
                name=spec.name,
                path=path,
            )
        )
    return image


def orb():
    return cv2.ORB_create(ORB_FEATURES)


def page_features(gray: np.ndarray) -> tuple:
    """ページを見本と同じ解像度に縮め、特徴点を求める。すべての様式との位置合わせで使い回す"""
    s = TEMPLATE_SCALE
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return orb().detectAndCompute(small, None)


# good_matches が覚える照合。画面のプロセスでは別のスレッドからも呼ばれうるので、スレッドごとに持つ
_memo = threading.local()


def good_matches(tpl: Template, features: tuple) -> list:
    """見本の特徴点とページの特徴点（page_features）の対応のうち、1番目と2番目に近い点の距離の比が MATCH_RATIO
    未満のもの。同じ特徴点のオブジェクトについては覚えて使い回す（向きを決めるときと、バリエーションの候補を絞る
    ときで、同じ照合をくり返さないため）"""
    memo = getattr(_memo, "page", None)
    if memo is None or memo[0] is not features:
        memo = _memo.page = (features, [])
    # 見本の記述子のオブジェクトを持ったまま is で比べる（id だけでは、解放された見本の id が使い回されうる）
    for desc, good in memo[1]:
        if desc is tpl.descriptors:
            return good
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(tpl.descriptors, features[1], k=2)
    good = [
        p[0]
        for p in pairs
        if len(p) == 2 and p[0].distance < MATCH_RATIO * p[1].distance
    ]
    memo[1].append((tpl.descriptors, good))
    return good


def register(tpl: Template, features: tuple) -> tuple[np.ndarray | None, int]:
    """見本からページへの変換（作業画像の解像度の射影変換）と、整合した特徴点の数を返す

    features は page_features で求めたページの特徴点。
    """
    s = TEMPLATE_SCALE
    kp, desc = features
    if desc is None or tpl.descriptors is None:
        return None, 0
    good = good_matches(tpl, features)
    if len(good) < MIN_INLIERS:
        return None, 0
    src = np.float32([tpl.keypoints[g.queryIdx].pt for g in good])
    dst = np.float32([kp[g.trainIdx].pt for g in good])
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
    inliers = int(mask.sum()) if mask is not None else 0
    if H is None or inliers < MIN_INLIERS:
        return None, inliers
    scale = np.diag([s, s, 1.0])
    H = np.linalg.inv(scale) @ H @ scale  # 作業画像の解像度の変換にする
    if not plausible(tpl, H):
        return None, inliers
    return H, inliers


def refined(found: tuple, features: tuple) -> tuple:
    """(見本, 変換, 整合した点の数) の変換を、2段目の位置合わせ（_refine）で求め直したもの"""
    tpl, H, inliers = found
    return (tpl, *_refine(tpl, features, H, inliers))


def _refine(
    tpl: Template, features: tuple, H: np.ndarray, inliers: int
) -> tuple[np.ndarray, int]:
    """位置合わせの2段目：H（register の変換）で見本の特徴点をページに移し、移した位置の近くにあるページの特徴点の
    中だけで対応を選び直して、変換を求め直す。求め直せなければ、H をそのまま返す

    1段目は、見本とページの全部の特徴点から対応を選ぶので、同じ形がくり返す所（表のます目など）の対応は、
    いちばん近い点と2番目に近い点の距離が近く、捨てられる。保険料控除申告書の白黒2値では、整合した点がページの
    上の題名のあたりに集まり、個人番号欄の近くに1点もなく、欄の位置を外挿して数十画素ずれた。近くに限って選ぶと、
    くり返しの形でも取り違えにくく、欄の近くにも整合した点ができる。
    """
    s = TEMPLATE_SCALE
    scale = np.diag([s, s, 1.0])
    Hs = (
        scale @ H @ np.linalg.inv(scale)
    )  # 見本の画像の画素 → ページ（見本と同じ縮尺）の画素
    kp, desc = features
    if desc is None or tpl.descriptors is None:
        return H, inliers
    tp = np.float32([k.pt for k in tpl.keypoints])
    pp = np.float32([k.pt for k in kp])
    best, count = None, inliers
    for radius in REFINE_RADII:
        src, dst = _nearby_pairs(tp, tpl.descriptors, pp, desc, Hs, radius)
        if len(src) < MIN_INLIERS:
            break
        H2, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
        if H2 is None or int(mask.sum()) < MIN_INLIERS:
            break
        Hs, best, count = H2, H2, int(mask.sum())
    if best is None:
        return H, inliers
    Hw = np.linalg.inv(scale) @ best @ scale
    if not plausible(tpl, Hw):
        return H, inliers
    return Hw, count


def _nearby_pairs(
    tp: np.ndarray,
    tdesc: np.ndarray,
    pp: np.ndarray,
    pdesc: np.ndarray,
    Hs: np.ndarray,
    radius: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Hs で移した見本の点（tp）から上下左右 radius 以内にあるページの点（pp）の中で、記述子が最も近いものを対応に
    する。比べてよい組の行列を作って OpenCV の照合に渡し、まとめて計算する（点を1つずつ調べるより速い）"""
    proj = cv2.perspectiveTransform(tp[None], Hs)[0]
    allowed = _nearby_mask(proj, pp, radius)
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(tdesc, pdesc, k=2, mask=allowed)
    src, dst = [], []
    for p in pairs:
        if not p or p[0].distance > REFINE_MAX_DISTANCE:
            continue
        if len(p) == 2 and p[0].distance >= REFINE_RATIO * p[1].distance:
            continue
        src.append(tp[p[0].queryIdx])
        dst.append(pp[p[0].trainIdx])
    return np.float32(src).reshape(-1, 2), np.float32(dst).reshape(-1, 2)


def _nearby_mask(proj: np.ndarray, pp: np.ndarray, radius: float) -> np.ndarray:
    """比べてよい組の行列（行は移した見本の点 proj、列はページの点 pp。uint8）。上下左右とも radius 以内の組を1にする

    ページの点を一辺 radius+1 の升に分け、移した点の升とその周りの8つの升にあるページの点だけを、同じ式で調べる。
    radius 以内の点は、隣までの升にしかない。移した位置が数でない点（NaN・無限大）と、ページの点の範囲から
    radius+1 より外にある点は、どの組も radius 以内にならないので調べない。結果の行列は、全部の組を調べたときと
    同じ
    """
    allowed = np.zeros((len(proj), len(pp)), np.uint8)
    if len(proj) == 0 or len(pp) == 0:
        return allowed
    cell = float(radius) + 1.0
    low = pp.min(axis=0) - radius - 1
    high = pp.max(axis=0) + radius + 1
    inside = (
        np.isfinite(proj).all(axis=1)
        & (proj >= low).all(axis=1)
        & (proj <= high).all(axis=1)
    )
    rows_in = np.flatnonzero(inside)
    if len(rows_in) == 0:
        return allowed
    # 升の番号（x, y）。周りの升の番号が負にならないよう、範囲の左上の1つ外の升を0にする
    origin = np.floor(low.astype(np.float64) / cell) - 1
    page_cell = (np.floor(pp.astype(np.float64) / cell) - origin).astype(np.int64)
    proj_cell = (np.floor(proj[rows_in].astype(np.float64) / cell) - origin).astype(
        np.int64
    )
    # 升を1つの番号（x × 幅 + y）にし、ページの点をその番号の順に並べる。幅は、y の升の番号の最大に、下の升の
    # 分の余裕を足したもの。こうすると、同じ x の列の y−1〜y+1 の升が、続いた番号の範囲になる
    width = int(max(page_cell[:, 1].max(), proj_cell[:, 1].max())) + 3
    key = page_cell[:, 0] * width + page_cell[:, 1]
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    rows, cols = [], []
    for dx in (-1, 0, 1):
        first = np.searchsorted(
            sorted_key, (proj_cell[:, 0] + dx) * width + proj_cell[:, 1] - 1, "left"
        )
        last = np.searchsorted(
            sorted_key, (proj_cell[:, 0] + dx) * width + proj_cell[:, 1] + 1, "right"
        )
        counts = last - first
        # 移した点ごとに、升の中のページの点の位置（order の中の first〜last）を1列に並べる
        rows.append(np.repeat(rows_in, counts))
        offsets = np.arange(counts.sum()) - np.repeat(
            np.cumsum(counts) - counts, counts
        )
        cols.append(order[np.repeat(first, counts) + offsets])
    r, c = np.concatenate(rows), np.concatenate(cols)
    near = (np.abs(proj[r, 0] - pp[c, 0]) <= radius) & (
        np.abs(proj[r, 1] - pp[c, 1]) <= radius
    )
    allowed[r[near], c[near]] = 1
    return allowed


def upright_group(
    gray: np.ndarray, specs: Sequence[FormSpec], features: tuple | None = None
) -> tuple[int, tuple, tuple[tuple[FormSpec, int], ...]] | None:
    """向きを直す前のページ画像を様式と位置合わせし、成り立てば、
    ページを正しい向きにするために反時計回りに90度回す回数と、成り立った様式を返す。成り立たなければ None

    特徴点（ORB）と射影変換による位置合わせは、ページが回っていても傾いていても成り立つ。見本の横の向きが
    ページの上で何度に写っているかから、90度単位の向きを決める。OCR で向きを判定するより、帳票の
    ページで1ページ約0.4〜1.1秒短い。傾きは求めない（変換から求めた傾きは、公開様式と
    見本のスキャンの印刷の違いで、今の推定と最大約0.2度ずれたため）。
    同じ用紙の部分に成り立ったほかの様式と点（点の多い順。成り立った様式を含む）も返す（位置合わせは全部の
    様式と行っているので、増える処理はない）。成り立った様式は (見本, 変換, 点) で返す（回さないページでは、範囲を
    求めるときに位置合わせし直さずに使うため）。features は page_features(gray) の結果（求めてあれば渡す）。
    """
    if features is None:
        features = page_features(gray)
    aligned = align_all(features, templates(specs))
    best = strongest(aligned)
    if best is None:
        return None
    tpl, H, _ = best
    th, tw = tpl.image.shape
    mid = np.float32([[[0, th / 2]], [[tw, th / 2]]]) / TEMPLATE_SCALE
    (x0, y0), (x1, y1) = cv2.perspectiveTransform(mid, H).reshape(-1, 2)
    angle = math.degrees(
        math.atan2(y0 - y1, x1 - x0)
    )  # 反時計回りを正（画像の y は下向き）
    group = tuple((t.spec, n) for t, _, n in paper_group(best, aligned))
    return round(-angle / 90) % 4, best, group


def align_all(features: tuple, tpls: list[Template]) -> list[tuple]:
    """全部の見本と位置合わせし、成り立った見本の一覧 [(見本, 変換, 点の数)]（tpls の順）"""
    aligned = []
    for tpl in tpls:
        H, inliers = register(tpl, features)
        if H is not None:
            aligned.append((tpl, H, inliers))
    return aligned


def strongest(aligned: list[tuple]) -> tuple | None:
    """点の数が最も多いもの。同じなら、先に並んだもの"""
    best = None
    for item in aligned:
        if best is None or item[2] > best[2]:
            best = item
    return best


def _same_paper(a: tuple, b: tuple) -> bool:
    """2つの (見本, 変換, 点) の見本の四隅が、同じ用紙の部分か（重なりの面積 ÷ 小さいほうの面積が SAME_PAPER 以上）"""
    qa = page_quad(a[0], a[1]).astype(np.float32)
    qb = page_quad(b[0], b[1]).astype(np.float32)
    if not (cv2.isContourConvex(qa) and cv2.isContourConvex(qb)):
        return False
    smaller = min(cv2.contourArea(qa), cv2.contourArea(qb))
    if smaller <= 0:
        return False
    inter, _ = cv2.intersectConvexConvex(qa, qb)
    return inter / smaller >= SAME_PAPER


def paper_group(best: tuple, aligned: list[tuple]) -> list[tuple]:
    """best と同じ用紙の部分に成り立ったもの（best を先頭に、ほかは点の多い順。同じなら先に並んだもの）"""
    others = [a for a in aligned if a is not best and _same_paper(best, a)]
    return [best, *sorted(others, key=lambda a: -a[2])]


def page_quad(tpl: Template, H: np.ndarray) -> np.ndarray:
    """見本のページの四隅を、作業画像の上に移したもの（4×2）"""
    th, tw = tpl.image.shape
    corners = np.float32([[[0, 0]], [[tw, 0]], [[tw, th]], [[0, th]]]) / TEMPLATE_SCALE
    return cv2.perspectiveTransform(corners, H).reshape(-1, 2)


def quad_area(quad: np.ndarray) -> float:
    return float(cv2.contourArea(quad))


def plausible(tpl: Template, H: np.ndarray) -> bool:
    """見本のページを H（作業画像の解像度の変換）で移した形が、用紙として成り立つか

    四隅がどれも変換の同じ側にあり（消失線をまたがない）、移した四隅が、角の順のまま凸の四角形になり（辺が交差
    せず、裏返しでもない）、面積の比が AREA_RATIO の中にあること。整合した点が少なく用紙の1か所に集まると、
    その近くだけを合わせて四隅が遠くへ飛ぶ変換が求まり、面積の比だけでは見分けられないことがあるため。
    """
    th, tw = tpl.image.shape
    w, h = tw / TEMPLATE_SCALE, th / TEMPLATE_SCALE
    corners = np.float64([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]])
    depth = corners @ np.asarray(H, np.float64)[2]
    if not (np.all(depth > 0) or np.all(depth < 0)):
        return False
    quad = page_quad(tpl, H).astype(np.float64)
    edges = np.roll(quad, -1, axis=0) - quad
    nxt = np.roll(edges, -1, axis=0)
    turns = edges[:, 0] * nxt[:, 1] - edges[:, 1] * nxt[:, 0]
    if not np.all(turns > 0):
        return False
    return (
        AREA_RATIO[0] <= quad_area(quad.astype(np.float32)) / (w * h) <= AREA_RATIO[1]
    )
