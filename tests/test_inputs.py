# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""入力のファイルを開いて画像にする処理と、出力の名前のテスト（OCRを使わないので短時間で終わる）"""

import io
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, TiffImagePlugin
from scan import DATA, SAMPLES, page_image
from userforms import bundled_forms

from sumikeshi_studio import export, imaging, pipeline


def test_tiff_pages_have_resolution_of_each_image(tmp_path):
    """大きさの違う画像を持つ TIFF も、画像ごとに解像度を求める"""
    path = tmp_path / "two.tif"
    big, small = Image.new("L", (2339, 1654), 255), Image.new("L", (1754, 1240), 255)
    big.save(path, save_all=True, append_images=[small])
    assert [round(p.dpi) for p in imaging.open_pages(path)] == [200, 150]


def test_only_first_image_of_multi_picture_jpeg_is_opened(tmp_path):
    """複数の画像を持つ JPEG（MPO 形式）は、1枚目だけを開く"""
    path = tmp_path / "photo.jpg"
    main, extra = Image.new("RGB", (4032, 3024), "white"), Image.new("RGB", (1008, 756))
    main.save(path, format="MPO", save_all=True, append_images=[extra])
    with Image.open(path) as im:
        assert (im.format, im.n_frames) == ("MPO", 2)
    pages = imaging.open_pages(path)
    assert [(p.index, round(p.dpi)) for p in pages] == [(0, 345)]  # 長辺を A4 とみなす


UNOPENED = {
    "試験_中身がWebP.jpg": "WEBP",
    "試験_中身がJPEG2000.jpg": "JPEG2000",
    "試験_中身がAVIF.jpg": "AVIF",
    "試験_中身がGIF.png": "GIF",
}


@pytest.mark.parametrize("name", sorted(UNOPENED))
def test_image_of_other_format_is_not_opened_by_its_suffix(name):
    """中身が JPEG（MPO を含む）・PNG・TIFF・BMP でない画像は、名前の拡張子が .jpg・.png でも開かず、ほかの開けない
    画像と同じ「画像を開けません」にする（Pillow は中身で形式を決め、WebP・JPEG 2000・AVIF・GIF も開けるため）"""
    path = DATA / name
    with Image.open(path) as im:
        assert (
            im.format == UNOPENED[name]
        )  # 中身はその形式（tools/make_test_images.py）
    with pytest.raises(imaging.InputError, match=rf"^画像を開けません: {name}$"):
        imaging.open_pages(path)


def test_image_replaced_by_other_format_after_opening_is_not_read(tmp_path):
    """開いた後に、中身が WebP の画像に置き換わったファイルも、解析と出力で読まない（解析の依頼の失敗になり、その
    ページは「処理できません」になる）"""
    path = tmp_path / "scan.jpg"
    Image.new("RGB", (64, 48), "white").save(path, format="JPEG")
    (page,) = imaging.open_pages(path)
    path.write_bytes((DATA / "試験_中身がWebP.jpg").read_bytes())
    with pytest.raises(Exception, match="cannot identify image file"):
        imaging.render(page)


@pytest.mark.parametrize("kind", ["JPEG", "PNG", "TIFF", "BMP"])
def test_images_of_supported_formats_open(tmp_path, kind):
    """JPEG・PNG・TIFF・BMP の画像は、拡張子が別の開ける形式のものでも、中身の形式で開く"""
    path = tmp_path / "scan.png" if kind != "PNG" else tmp_path / "scan.jpg"
    Image.new("RGB", (64, 48), "white").save(path, format=kind)
    (page,) = imaging.open_pages(path)
    assert imaging.render(page).shape == (48, 64, 3)


def test_output_paths_avoid_same_name(tmp_path):
    inputs = [Path("a/scan.pdf"), Path("b/scan.pdf"), Path("a/SCAN.jpg"), Path("c.pdf")]
    got = export.output_paths(inputs, tmp_path)
    assert [p.name for p in got] == [
        "scan_redacted.pdf",
        "scan_redacted_2.pdf",
        "SCAN_redacted_3.pdf",
        "c_redacted.pdf",
    ]
    assert all(p.parent == tmp_path for p in got)


# ---------------------------------------------------------------- PDF を開く・画像にする（pypdfium2）


def _inline_image_pdf(path: Path) -> Path:
    """ページの中身に直接書いた画像（インライン画像。200×100画素を 360×180pt に貼る）だけの PDF を書く"""
    w, h = 200, 100
    content = (
        b"q 360 0 0 180 100 300 cm BI /W %d /H %d /CS /G /BPC 8 ID " % (w, h)
        + bytes(w * h)
        + b" EI Q"
    )
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    return _write_pdf(path, objs)


def _write_pdf(path: Path, objs: list[bytes]) -> Path:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref,
    )
    path.write_bytes(bytes(out))
    return path


def _px_per_inch(px_w: int, px_h: int, width_pt: float, height_pt: float) -> float:
    """画素数 px_w×px_h の画像を、width_pt×height_pt（ポイント）に貼ったときの1インチあたりの画素数"""
    return (px_w * px_h / ((width_pt / 72) * (height_pt / 72))) ** 0.5


def test_pdf_resolution_of_inline_form_and_rotated_images(tmp_path):
    """貼られた画像の解像度を、インライン画像、Form XObject の中の画像、/Rotate のページでも正しく求める

    Form XObject を縮めて貼ったときは、Form XObject の変換行列を掛け合わせないと、解像度を小さく見積もる。
    PDF は tests/data/ の検査用の PDF（1000×1400画素の JPEG を A4 に貼ったもの、それを Form XObject として
    半分の大きさで貼ったもの、/Rotate 90 を付けたもの）。JPEG は縦横の比を保って貼ったので、595×833pt
    （A4 の高さ 842pt より短い）に貼られている。
    """
    plain = _px_per_inch(1000, 1400, 595, 833)
    expected = {
        DATA / "試験_画像1枚.pdf": plain,
        DATA / "試験_FormXObject縮小.pdf": _px_per_inch(1000, 1400, 297.5, 416.5),
        DATA / "試験_回転90.pdf": plain,
        _inline_image_pdf(tmp_path / "inline.pdf"): _px_per_inch(200, 100, 360, 180),
    }
    for f, dpi in expected.items():
        with imaging.PDF_LOCK, imaging.open_pdf(f) as pdf:
            page = pdf[0]
            try:
                measured = imaging._page_parts(page).px_per_in
            finally:
                page.close()
        assert round(measured, 1) == round(dpi, 1), f.name
    # 描く解像度：インライン画像（40dpi）はページの面積の半分未満なので、下限の200dpi で描く
    pages = [imaging.resolve(p) for f in expected for p in imaging.open_pages(f)]
    assert [round(p.dpi, 1) for p in pages] == [
        round(plain, 1),
        round(_px_per_inch(1000, 1400, 297.5, 416.5), 1),
        round(plain, 1),
        200.0,
    ]


def test_rotated_pdf_page_is_rendered_turned():
    """/Rotate 90 のページは、回した向きで画像にする"""
    (p,) = imaging.open_pages(DATA / "試験_回転90.pdf")
    rgb = imaging.render(p, dpi=72 / p.zoom)  # 1ポイントを1画素
    assert abs(rgb.shape[0] - 595) <= 1 and abs(rgb.shape[1] - 842) <= 1  # 横長
    assert rgb.shape[2] == 3


def test_form_field_values_are_rendered():
    """入力欄の値も画像に描く（PyMuPDF と同じ。pypdfium2 は init_forms をしないと描かない）"""
    import numpy as np

    (p,) = imaging.open_pages(
        DATA / "試験_入力欄.pdf"
    )  # 300,300〜500,330pt に値「1234」
    gray = imaging.render(p, dpi=144).mean(axis=2)
    assert np.count_nonzero(gray[600:660, 600:1000] < 128) > 300


def test_pdf_open_errors_and_japanese_name(tmp_path):
    """壊れた PDF、パスワード付きの PDF、ページのない PDF は理由を示して開かない。日本語の名前のファイルも開ける"""
    import pytest

    broken = tmp_path / "壊れた.pdf"
    broken.write_bytes(b"not a pdf")
    with pytest.raises(imaging.InputError, match="壊れている可能性"):
        imaging.open_pages(broken)
    with pytest.raises(imaging.InputError, match="パスワード付き"):
        imaging.open_pages(DATA / "試験_パスワード付き.pdf")
    empty = _write_pdf(
        tmp_path / "空.pdf",
        [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [] /Count 0 >>"],
    )
    with pytest.raises(imaging.InputError, match="ページがありません"):
        imaging.open_pages(empty)
    named = _inline_image_pdf(tmp_path / "日本語の名前の申告書.pdf")
    assert len(imaging.open_pages(named)) == 1


def _photo_pdf(path: Path, px: tuple[int, int], size_pt: tuple[float, float]) -> Path:
    """px（幅, 高さ）の画素の JPEG を、size_pt（幅, 高さ。ポイント）の大きさで A4 のページに貼った PDF"""
    _, jpg = cv2.imencode(".jpg", np.full((px[1], px[0], 3), 200, np.uint8))
    with imaging.PDF_LOCK:
        pdf = pdfium.PdfDocument.new()
        page = pdf.new_page(595, 842)
        obj = pdfium.PdfImage.new(pdf)
        obj.load_jpeg(io.BytesIO(jpg.tobytes()), inline=True)
        obj.set_matrix(pdfium.PdfMatrix().scale(*size_pt))
        page.insert_obj(obj)
        page.gen_content()
        page.close()
        pdf.save(path)
        pdf.close()
    return path


@pytest.mark.parametrize(
    ("px", "size_in", "dpi"),
    [
        (
            (4032, 3024),
            (3.37, 2.13),
            600,
        ),  # 高い解像度の写真を個人番号カードの大きさで貼った（1,303dpi）
        ((95, 95), (2, 2), 200),  # 粗い画像（47.5dpi）がページの一部だけ
        ((1240, 1754), (8.27, 11.69), 150),  # 用紙全体の150dpi の画像は、そのまま
        ((2480, 3508), (8.27, 11.69), 300),
    ],
)
def test_pdf_resolution_has_upper_and_lower_bounds(px, size_in, dpi, tmp_path):
    """PDF を描く解像度は600dpi まで。画像がページの面積の半分未満なら200dpi 以上"""
    path = _photo_pdf(tmp_path / "p.pdf", px, (size_in[0] * 72, size_in[1] * 72))
    (p,) = imaging.open_pages(path)
    assert round(imaging.resolve(p).dpi) == dpi


def _background_pdf(path: Path, px: tuple[int, int]) -> Path:
    """ページ全体を覆う px（幅, 高さ）の画素の粗い画像の上に、公開様式の扶養控除等申告書のページ（ベクター）を
    Form XObject として重ねた PDF（ベクターの内容は書き直さない）"""
    _, jpg = cv2.imencode(".jpg", np.full((px[1], px[0], 3), 250, np.uint8))
    with imaging.PDF_LOCK:
        src = pdfium.PdfDocument(SAMPLES / "2027bun_01.pdf")
        w, h = src[0].get_size()
        pdf = pdfium.PdfDocument.new()
        page = pdf.new_page(w, h)
        obj = pdfium.PdfImage.new(pdf)
        obj.load_jpeg(io.BytesIO(jpg.tobytes()), inline=True)
        obj.set_matrix(pdfium.PdfMatrix().scale(w, h))
        page.insert_obj(obj)
        page.insert_obj(src.page_as_xobject(0, pdf).as_pageobject())
        page.gen_content()
        page.close()
        pdf.save(path)
        pdf.close()
        src.close()
    return path


@pytest.mark.parametrize("px", [(16, 12), (800, 600)])
def test_pdf_with_coarse_background_is_drawn_at_least_work_resolution(px, tmp_path):
    """文字や図形のあるページに、ページ全体を覆う粗い背景の画像があっても、下限の200dpi 以上で描く（画像の解像度
    （1.41dpi・70dpi）で描くと、様式不明になるか枠の線が見つからない）"""
    (p,) = imaging.open_pages(_background_pdf(tmp_path / "bg.pdf", px))
    assert imaging.resolve(p).dpi == imaging.PDF_MIN_DPI
    res = pipeline.analyze(p, formset=bundled_forms())
    assert res.form == "扶養控除等申告書"
    assert res.unframed == ()


def test_pdf_without_images_is_drawn_at_work_resolution(tmp_path):
    """画像のない PDF は、作業の解像度（200dpi）で描く（今までと同じ）"""
    path = _write_pdf(
        tmp_path / "text.pdf",
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        ],
    )
    (p,) = imaging.open_pages(path)
    assert imaging.resolve(p).dpi == imaging.WORK_DPI


def test_export_of_capped_pdf_is_600dpi(tmp_path):
    """上限で描いた PDF の出力は、600dpi 相当の画素の数になる（1,303dpi では描かない）"""
    path = _photo_pdf(tmp_path / "card.pdf", (4032, 3024), (3.37 * 72, 2.13 * 72))
    (page,) = imaging.open_pages(path)
    work = np.full((2339, 1654), 255, np.uint8)  # A4 の作業画像（200dpi）
    res = pipeline.PageResult(
        quarter_turns=0,
        skew=0.0,
        orientation_confident=True,
        work=work,
        boxes=[],
        form="様式不明",
    )
    export.export([(page, res)], tmp_path / "out.pdf")
    with imaging.PDF_LOCK, imaging.open_pdf(tmp_path / "out.pdf") as pdf:
        out = pdf[0]
        try:
            (obj,) = list(out.get_objects())
            width, _ = obj.get_px_size()
        finally:
            out.close()
    assert abs(width - round(595 / 72 * 600)) <= 1


@pytest.mark.parametrize("suffix", [".png", ".tif"])
def test_16_bit_gray_image_is_drawn_like_8_bit(suffix, tmp_path):
    """16ビットのグレーの画像（I;16）は、上位8ビットのグレーとして描く（値を255で切り詰めて、ほぼ真っ白にしない）。
    32ビットの整数（I）で値が255以下の画像は、8ビットの値のまま描く"""
    g = page_image("2027bun_01")
    eight = tmp_path / f"eight{suffix}"
    Image.fromarray(g).save(eight, dpi=(200, 200))
    sixteen = tmp_path / f"sixteen{suffix}"
    Image.fromarray(g.astype(np.uint16) * 257).save(sixteen, dpi=(200, 200))
    with Image.open(sixteen) as im:
        assert im.mode.startswith("I;16")
    expected = imaging.render(imaging.open_pages(eight)[0])
    assert np.array_equal(imaging.render(imaging.open_pages(sixteen)[0]), expected)
    wide = tmp_path / "wide.tif"
    Image.fromarray(g.astype(np.int32), mode="I").save(wide, dpi=(200, 200))
    assert np.array_equal(imaging.render(imaging.open_pages(wide)[0]), expected)


def _colorless_inputs(tmp_path) -> dict[str, Path]:
    """色のないページの入力：グレーの PNG、白黒2値の TIFF（G4）、16ビットのグレーの PNG、グレーの画像だけの PDF、
    白黒2値（CCITT G4）の画像だけの PDF"""
    import scan

    g = page_image("2027bun_01")
    out = {}
    out["gray.png"] = tmp_path / "gray.png"
    Image.fromarray(g).save(out["gray.png"], dpi=(200, 200))
    out["bilevel.tif"] = tmp_path / "bilevel.tif"
    Image.fromarray(np.where(g < 160, 0, 255).astype(np.uint8)).convert("1").save(
        out["bilevel.tif"], compression="group4", dpi=(300, 300)
    )
    out["gray16.png"] = tmp_path / "gray16.png"
    Image.fromarray(g.astype(np.uint16) * 257).save(out["gray16.png"], dpi=(200, 200))
    out["gray.pdf"] = scan._scan(g, tmp_path / "gray.pdf", 0.8, 0)
    out["bilevel.pdf"] = scan._scan_bilevel(
        scan.written_page("2027bun_01", 300), tmp_path / "bilevel.pdf", 0.8, 0, 160
    )
    return out


def test_colorless_pages_are_drawn_gray_with_same_pixels(tmp_path):
    """色のないページは、初めからグレーで描く（gray が True か None）。画素は、RGB で描いてからグレーにしたときと
    同じ（作業画像の解像度に縮めたときも、元の解像度のときも）"""
    for name, path in _colorless_inputs(tmp_path).items():
        page = imaging.open_pages(path)[0]
        for dpi in (imaging.WORK_DPI, None):
            rgb = imaging.render(page, dpi)
            assert rgb.ndim == 3, name
            want = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            for gray in (True, None):
                got = imaging.render(page, dpi, gray=gray)
                assert got.ndim == 2 and np.array_equal(got, want), (name, dpi, gray)


def test_colored_pages_are_drawn_rgb_then_gray():
    """色のあるページ（カラーの画像、文字や図形のある PDF）は、gray が None なら RGB で描き、True なら RGB で描いて
    縮めてからグレーにする（PDFium のグレーの描き方は OpenCV の換算と違うことがあるため、今までと同じ画素にする）"""
    for path in (
        DATA / "公開様式_2026bun_04_232dpi_カラー.png",
        SAMPLES / "2027bun_01.pdf",
    ):
        page = imaging.open_pages(path)[0]
        rgb = imaging.render(page, imaging.WORK_DPI)
        assert imaging.render(page, imaging.WORK_DPI, gray=None).ndim == 3, path.name
        assert np.array_equal(
            imaging.render(page, imaging.WORK_DPI, gray=True),
            cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY),
        ), path.name


def test_high_resolution_jpeg_is_read_reduced_for_analysis(tmp_path, monkeypatch):
    """600dpi の JPEG を作業画像の解像度で描くときは、JPEG の仕組みで縮めながら読む（Pillow の draft）。
    大きさは元の解像度で読んで縮めたときと同じで、画素の違いは小さい（くっきりした試験画像で平均 2.1）。元の解像度で
    描くとき（出力）は縮めずに読む"""
    import scan

    g = scan.written_page("2027bun_01", 600)
    path = tmp_path / "scan600.jpg"
    Image.fromarray(np.dstack([g] * 3)).save(path, quality=90, dpi=(600, 600))
    page = imaging.open_pages(path)[0]
    from PIL import JpegImagePlugin

    drafts = []
    real = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        result = real(self, mode, size)
        drafts.append(self.size)
        return result

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    small = imaging.render(page, imaging.WORK_DPI, gray=True)
    assert drafts and drafts[0][0] < page_size_px(path)[0]  # 縮めて読んだ
    with Image.open(path) as im:
        full = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
    f = imaging.WORK_DPI / page.dpi
    want = cv2.resize(full, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    assert small.shape == want.shape
    assert np.abs(small.astype(int) - want.astype(int)).mean() < 3
    drafts.clear()
    assert imaging.render(page).shape[:2] == full.shape  # 元の解像度では縮めない
    assert not drafts or drafts[0] == page_size_px(path)


def page_size_px(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def test_reading_reduced_jpeg_changes_analysis_little(tmp_path, monkeypatch):
    """600dpi の JPEG を縮めながら読んでも、解析の結果（様式・向き・範囲の数）は同じで、傾きと範囲の位置の違いは
    小さい"""
    import scan
    from PIL import JpegImagePlugin
    from userforms import bundled

    g = scan.written_page("2027bun_01", 600, bundled("nta-fuyou-r9").fields)
    path = tmp_path / "scan600.jpg"
    Image.fromarray(np.dstack([g] * 3)).save(path, quality=90, dpi=(600, 600))
    page = imaging.open_pages(path)[0]
    reduced = pipeline.analyze(page, formset=bundled_forms())
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", lambda *a: None)
    full = pipeline.analyze(page, formset=bundled_forms())
    assert (reduced.form, reduced.quarter_turns, len(reduced.boxes)) == (
        full.form,
        full.quarter_turns,
        len(full.boxes),
    )
    assert reduced.work.shape == full.work.shape
    assert abs(reduced.skew - full.skew) < 0.01
    diffs = [
        max(abs(a.x - b.x), abs(a.y - b.y), abs(a.w - b.w), abs(a.h - b.h))
        for a, b in zip(reduced.boxes, full.boxes)
    ]
    assert max(diffs) <= 1.0


def test_tiff_reduced_images_and_masks_are_not_pages(tmp_path):
    """TIFF の縮小版（NewSubfileType の1）と透明のマスク（4）は、ページにしない"""
    path = tmp_path / "thumb.tif"
    images = [
        (Image.new("L", (2339, 1654), 255), 0),
        (Image.new("L", (234, 165), 255), 1),  # 縮小版
        (Image.new("L", (2339, 1654), 255), 0),
        (Image.new("1", (2339, 1654), 1), 4),  # 透明のマスク
    ]
    # 画像ごとにタグを付けて書く（ファイルに直接書くと、Pillow が後片付けで閉じたファイルを触って警告が出るため、
    # メモリに書いてから保存する）
    buf = io.BytesIO()
    tf = TiffImagePlugin.AppendingTiffWriter(buf)
    for i, (im, kind) in enumerate(images):
        if i:
            tf.newFrame()
        info = TiffImagePlugin.ImageFileDirectory_v2()
        info[imaging.TIFF_NEW_SUBFILE_TYPE] = kind
        im.save(tf, format="TIFF", tiffinfo=info)
    tf.finalize()
    path.write_bytes(buf.getvalue())
    with Image.open(path) as im:
        assert im.n_frames == 4
    assert [p.index for p in imaging.open_pages(path)] == [0, 2]


def _fax(img: np.ndarray, x_dpi: int, y_dpi: int) -> np.ndarray:
    """200dpi の画像を、横 x_dpi・縦 y_dpi の画像にする（ファクスの標準モードなど）"""
    h, w = img.shape
    size = (round(w * x_dpi / 200), round(h * y_dpi / 200))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("kind", ["tiff", "jpeg_exif6"])
def test_image_with_different_resolutions_is_made_square(kind, tmp_path):
    """縦横で解像度の違う画像は、低いほうの向きを拡大して画素を正方形にし、解像度は高いほうの値にする"""
    img = page_image("2027bun_01")
    fax = _fax(img, 204, 98)
    if kind == "tiff":
        path = tmp_path / "fax.tif"
        Image.fromarray(fax).save(path, dpi=(204, 98))
    else:  # 保存した画素は回してあり、EXIF の向き6で正しい向きになる。保存した向きの横・縦は 98・204dpi
        path = tmp_path / "fax.jpg"
        exif = Image.Exif()
        exif[imaging.EXIF_ORIENTATION] = 6
        stored = np.ascontiguousarray(np.rot90(fax, 1))
        Image.fromarray(stored).save(path, dpi=(98, 204), exif=exif, quality=92)
    (page,) = imaging.open_pages(path)
    assert round(page.dpi) == 204
    rgb = imaging.render(page)
    assert abs(rgb.shape[0] / rgb.shape[1] - img.shape[0] / img.shape[1]) < 0.01
    res = pipeline.analyze(page, formset=bundled_forms())
    assert (res.form, len(res.boxes)) == ("扶養控除等申告書", 10)


def test_opening_pdf_does_not_read_page_contents(monkeypatch):
    """PDF を開く処理（画面とコマンドラインのプロセスで動く）は、ページの中身を読まず（pypdfium2 の get_page。
    FPDF_LoadPage）、ページの数と大きさだけを読む。解像度は決めず（None）、描くときに imaging.resolve でページの中身
    から決める（解析のプロセス）。解析の結果も、決めた解像度を持つ"""
    path = DATA / "試験_画像1枚.pdf"
    loaded = []
    real = pdfium.PdfDocument.get_page

    def get_page(self, index):
        loaded.append(index)
        return real(self, index)

    monkeypatch.setattr(pdfium.PdfDocument, "get_page", get_page)
    imaging._pdf_parts.cache_clear()  # 同じプロセスのほかのテストが読んだページの部品を使わない
    (page,) = imaging.open_pages(path)
    assert loaded == [] and page.dpi is None and page.zoom == 1.0
    resolved = imaging.resolve(page)
    assert loaded == [0] and round(resolved.dpi) == round(1000 / (595 / 72))
    assert imaging.resolve(resolved) is resolved
    res = pipeline.analyze(page, formset=())
    assert res.dpi == resolved.dpi
    # 開いた結果の大きさは、ページを読んで求めた大きさと同じ（A4 とみなすかの倍率に使う）
    (rotated,) = imaging.open_pages(DATA / "試験_回転90.pdf")
    assert (
        rotated.zoom == 1.0
        and imaging.render(rotated, 50).shape[1] > imaging.render(rotated, 50).shape[0]
    )


def test_extreme_resolution_ratio_is_not_used(tmp_path):
    """縦横の解像度の比が ASPECT_MAX（4）を超える記録は、比も解像度も使わず（A4 とみなす）、引き伸ばさない。記録が
    横60・縦0.1dpi（比600）の 1000画素四方の画像を、引き伸ばすと 1000×600,000画素の配列になる。比が4以内なら
    引き伸ばす"""
    path = tmp_path / "ratio.tif"
    Image.new("L", (1000, 1000), 255).save(path, dpi=(60, 0.1))
    (page,) = imaging.open_pages(path)
    assert page.stretch == (1.0, 1.0)
    assert page.dpi == pytest.approx(1000 / imaging.A4_LONG_IN)
    assert imaging.render(page).shape[:2] == (1000, 1000)
    near = tmp_path / "near.tif"
    Image.new("L", (400, 100), 255).save(near, dpi=(100, 400))
    (page,) = imaging.open_pages(near)
    assert page.stretch == (4.0, 1.0) and imaging.render(page).shape[:2] == (100, 1600)


def _tiff_with_large_second_page(path: Path, side: int) -> None:
    """2ページの TIFF の、2ページ目の幅・高さ（と帯の行の数）のタグだけを side に書き換える（ファイルは数百バイト）"""
    import struct

    Image.new("1", (100, 100), 1).save(
        path,
        save_all=True,
        append_images=[Image.new("1", (100, 100), 0)],
        compression="group4",
        dpi=(200, 200),
    )
    data = bytearray(path.read_bytes())
    endian = "<" if data[:2] == b"II" else ">"
    offsets, off = [], struct.unpack(endian + "I", data[4:8])[0]
    while off:
        offsets.append(off)
        n = struct.unpack(endian + "H", data[off : off + 2])[0]
        off = struct.unpack(endian + "I", data[off + 2 + 12 * n : off + 6 + 12 * n])[0]
    second = offsets[1]
    for i in range(struct.unpack(endian + "H", data[second : second + 2])[0]):
        e = second + 2 + 12 * i
        tag, kind = struct.unpack(endian + "HH", data[e : e + 4])
        if tag in (256, 257, 278):  # 幅・高さ・帯の行の数
            fmt = "H" if kind == 3 else "I"
            data[e + 8 : e + 8 + struct.calcsize(fmt)] = struct.pack(endian + fmt, side)
    path.write_bytes(bytes(data))


def test_too_many_pixels_stop_before_making_array(tmp_path, monkeypatch):
    """配列にする画像の画素の数が MAX_PIXELS を超えるページは、配列を取る前に「画像が大きすぎます」で止める（解析の
    依頼の失敗になり、そのページは「処理できません」になる。ほかのページは描ける）。複数ページの TIFF の2ページ目
    （Pillow は開くときに1ページ目だけを見る）と、解像度の比で引き伸ばした後の数にも当てる"""
    path = tmp_path / "large.tif"
    _tiff_with_large_second_page(path, 60000)
    first, second = imaging.open_pages(path)
    assert imaging.render(first, imaging.WORK_DPI).shape[0] > 0
    made = []
    real = np.empty

    def empty(shape, *args, **kwargs):
        made.append(shape)
        return real(shape, *args, **kwargs)

    monkeypatch.setattr(imaging.np, "empty", empty)
    with pytest.raises(
        imaging.InputError, match=r"画像が大きすぎます（60000×60000画素）"
    ):
        imaging.render(second, imaging.WORK_DPI)
    with pytest.raises(imaging.InputError):
        pipeline.analyze(second, formset=bundled_forms())
    assert made == []  # 配列を取る前に止まった
    # 引き伸ばした後の数：保存された 600×600画素は上限より少ないが、比4で引き伸ばすと上限を超える
    monkeypatch.setattr(imaging, "MAX_PIXELS", 1_000_000)
    near = tmp_path / "near.tif"
    Image.new("L", (600, 600), 255).save(near, dpi=(100, 400))
    (page,) = imaging.open_pages(near)
    with pytest.raises(imaging.InputError, match=r"（2400×600画素）"):
        imaging.render(page)


def test_unreadable_page_and_locked_file(tmp_path, monkeypatch):
    """ページを読めない PDF、ほかのソフトが使っているファイル、ないファイルは、理由を分けて示す（読めないフォルダは
    test_folders）

    ページの中身を読む（PDFium の FPDF_LoadPage）のは、解析のプロセスでページを描く解像度を決める処理と描く処理
    だけなので、ページを読めない PDF も開けて、そのページの解析が、PDFium の英語の文でなく、何ページ目を読めないかの
    文で失敗する。ページの大きさも読めない PDF は、開くときに同じ文で失敗する"""
    good = _inline_image_pdf(tmp_path / "good.pdf")
    bad = _inline_image_pdf(tmp_path / "bad.pdf")
    real_get_page = pdfium.PdfDocument.get_page
    opened = {}

    def get_page(self, index):
        if opened.get(id(self)) == "bad.pdf":
            raise pdfium.PdfiumError("Failed to load page.")
        return real_get_page(self, index)

    real_open = imaging._open_pdf

    def open_pdf(path):
        pdf = real_open(path)
        opened[id(pdf)] = path.name
        return pdf

    monkeypatch.setattr(pdfium.PdfDocument, "get_page", get_page)
    monkeypatch.setattr(imaging, "_open_pdf", open_pdf)
    (page,) = imaging.open_pages(bad)
    unreadable = "^PDF の1ページ目を読めません: bad.pdf$"
    with pytest.raises(imaging.InputError, match=unreadable):
        imaging.resolve(page)
    with pytest.raises(imaging.InputError, match=unreadable):
        imaging.render_pdf_page(bad, 0, 1.0)
    with pytest.raises(imaging.InputError, match=unreadable):
        pipeline.analyze(page, formset=bundled_forms())
    (page,) = imaging.open_pages(good)
    assert imaging.resolve(page).dpi > 0

    def no_size(*args):
        return False

    monkeypatch.setattr(imaging.pdfium_c, "FPDF_GetPageSizeByIndexF", no_size)
    with pytest.raises(imaging.InputError, match=unreadable):
        imaging.open_pages(bad)

    with pytest.raises(
        imaging.InputError, match="^ファイルが見つかりません: none.pdf$"
    ):
        imaging.open_pages(tmp_path / "none.pdf")
    jpg = tmp_path / "a.jpg"
    Image.new("L", (100, 140), 255).save(jpg)

    def denied(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(imaging, "open", denied, raising=False)  # PDF は open で開く
    monkeypatch.setattr(imaging.Image, "open", denied)
    for f in (good, jpg):
        with pytest.raises(imaging.InputError, match="ほかのソフトが使っているか"):
            imaging.open_pages(f)
