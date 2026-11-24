# 墨消しスタジオ（Sumikeshi Studio）

![墨消しスタジオで、架空の記入の帳票を開き、墨消しする欄を確かめて、墨消しした PDF を出力するところ](assets/demo.webp)

英語版の README は [README.en.md](README.en.md) です。

事前に登録しておいた様式の設定により、その様式で記入された用紙の欄を自動で墨消し（黒塗り）するツールです。スキャンした PDF あるいは用紙を撮影した写真から、様式で決めた墨消しの対象の欄を自動で検出し、その欄を墨消しした PDF を出力します。インターネットに接続せずに動作します。

## 特徴

- **登録した様式の特定の欄を自動で墨消し**：記入のない帳票を様式として登録し、墨消しする欄を決めておくと、その様式の用紙を開いたときに欄を自動で見つけて墨消しします。
- **様式のバリエーションも見分ける**：欄の大きさや位置が見本と少し違う用紙も、同じ様式として見分けて墨消しします。
- **スキャンした PDF にも、スマートフォンで撮影した画像にも対応**：PDF と画像（JPEG・PNG・TIFF・BMP）を開けます。用紙が傾いて写った画像も扱えます。
- **複数の様式を一度に処理**：種類の違う帳票が混ざった PDF や、複数のファイル・フォルダをまとめて開き、ページごとに様式を見分けて墨消しします。
- **墨消しする範囲を修正できる**：自動で決めた墨消しする範囲は、画面で修正できます。枠を動かしたり、大きさを変えたり、範囲を足したり、欄ごとに墨消しする・しないを切り替えたりできます。自動で判断しきれないページには「⚠ 要確認」が付きます。
- **PDF に文字のデータを残さない**：出力は画像だけの PDF です。元の PDF ファイルの文字のデータは残しません。
- **外部に通信しない**：すべて PC の中だけで動作します。

## 動作環境

| 項目 | 内容 |
|---|---|
| OS | Windows 11 Pro（64ビット）で動作を確認しています。Windows のバージョンごとの動作は確かめていません。 |
| インターネット接続 | インターネット接続は不要です。 |
| Python | Python で動作します。配布用パッケージには Python 3.14 が含まれます。 |
| CPU とメモリ | CPU とメモリに余裕がある場合（論理 CPU が4つ以上、空きメモリが4GB以上）は、並列で動作します。 |

## インストール

### 配布用パッケージからインストールする

配布用パッケージは、Python・ライブラリ・ツール本体を入れた ZIP です（作り方は「[開発](#開発)」）。最新の配布用パッケージは、[Releases のページ](https://github.com/hohm-sdx/sumikeshi-studio/releases)にあります。

> [!IMPORTANT]
> インターネット（Releases のページなど）からダウンロードした ZIP は、展開する前に、ZIP を右クリック →「プロパティ」→「全般」のタブに「このファイルは他のコンピューターから取得したものです」といったセキュリティの警告がある場合は「許可する」にチェック →「OK」とします。ダウンロードした ZIP には Windows が「インターネットから取得した」マークを付け、マークの付いたまま展開すると、スマート アプリ コントロールが `setup.bat` を止めることがあります。ZIP ファイルのプロパティの「全般」を開いても「このファイルは他のコンピューターから取得したものです」という警告がない場合は、この手順は要りません。止められたときは、展開したフォルダを削除し、ZIP に「許可する」を付けてから展開し直します。

1. ZIP をすべて展開します（展開する場所はどこでもかまいません。ただし、すべてのユーザー向けのインストールは、ネットワーク上のフォルダからはできません）。
2. 展開した `sumikeshi-studio` フォルダの `setup.bat` をダブルクリックし、インストールの種類を選びます（何も入力せずに Enter を押すと 1）。1（自分だけ）は、管理者権限なしで、展開したフォルダを `%LOCALAPPDATA%\Programs\SumikeshiStudio` に移します（別のドライブならコピーして元を削除する）。2（この PC のすべてのユーザー）は、管理者権限で `C:\Program Files\SumikeshiStudio` にコピーします。どちらも、Windows の「インストールされているアプリ」に登録し、スタートメニューとデスクトップにショートカットを作ります。名前は、日本語の Windows では「墨消しスタジオ」、それ以外の Windows では「Sumikeshi Studio」です。インストールの画面の文は、ツールで選んだ言語（選んでいなければ Windows の表示の言語）で出ます。
3. 様式のフォルダがない、あるいは様式が1つも登録されていないときは、サンプルとして国税庁の様式（3種類の申告書）を入れるかどうかを尋ねられます（自分だけのインストールではインストールのときに、すべてのユーザー向けのインストールでは各ユーザーの最初の起動のときに尋ねられます）。ここで登録した様式は、後で削除できます（起動の画面の「様式を編集」で削除します）。
4. スタートメニューかデスクトップのショートカットから起動します。

新しいバージョンへの更新は、新しい ZIP を展開して `setup.bat` を実行し、前と同じインストールの種類（自分だけ／この PC のすべてのユーザー）を選びます。違う種類を選ぶと、何も変えずに止まります。アンインストールは、「インストールされているアプリ」か、インストールしたフォルダの `uninstall.bat` から行います。アンインストールで消すもの、複数のユーザーで使うときの様式のフォルダなど、ユーザー向けの説明は ZIP の中の README（日本語版 [windows/README-ja.txt](windows/README-ja.txt)、英語版 [windows/README-en.txt](windows/README-en.txt)）にあります。

## 使い方

1. スタートメニューかデスクトップのショートカットから起動すると、起動の画面が出ます。様式のフォルダと、この PC で墨消しに使う様式の一覧が表示されます。
2. 「ファイルを開く」か「フォルダを開く」を押して、PDF・画像のファイルやフォルダを選びます（起動の画面にドラッグしても開けます）。墨消しの画面が開き、全ページを解析して、墨消しする欄をオレンジの枠で表示します。
3. 左の一覧でページを移りながら、墨消しする範囲を確かめ、必要なら修正します。欄をクリックすると墨消しする・しないが切り替わります。何もない所をドラッグすると範囲を足せ、枠の中や角をドラッグすると、枠を動かしたり大きさを変えたりできます。「⚠ 要確認」「⚠ 向き」のラベルが付いたページは、ページの上の帯に出る理由を読み、特に注意して確かめます。操作の一覧は「操作の説明」のボタンで出ます。
4. 「墨消しPDFを出力」（Ctrl+S）を押し、出力先のフォルダを選びます。入力のファイル1つにつき、「元の名前_redacted.pdf」が1つできます。

様式を追加・編集するときは、起動の画面の「様式を編集」を押します。

### コマンドラインから出力する（画面を出さない）

画面を出さずに、ファイルを解析して墨消しした PDF を出力することもできます（例：`sumikeshi.bat C:\scan --out C:\out`）。人が画面で確かめないので、出力した後に、標準出力と作業ログに出た確かめるべきページを確かめます。引数の一覧は `--help` で出ます。

## 開発

開発と自動テストは Linux（Debian 13）で行っています。環境は uv（0.13.6。入れ方は [uv の文書](https://docs.astral.sh/uv/getting-started/installation/)）で作ります。環境を作るときと、配布用パッケージを作るときは、インターネット接続が要ります。

```bash
uv sync --locked                                       # 開発の環境を作る（.venv に Python 3.14 とライブラリが入る）
uv run python -m sumikeshi_studio                      # ツールを起動する
uv run pytest -n 4 -m "regression or not regression"   # すべてのテストを流す
uv run ruff format src tests tools && uv run ruff check src tests tools  # コードを整形して確かめる
uv run python tools/make_dist.py                       # 配布用パッケージ（dist/ の ZIP）を作る
```

- 画面のテストには、Qt が読み込む次のライブラリが要ります（Debian・Ubuntu のパッケージの名前）：libgl1、libglx0、libglvnd0、libegl1、libopengl0、libxkbcommon0、libdbus-1-3、libfreetype6、libfontconfig1。ないときは、画面のテストは失敗せずに飛ばされます（`-rs` で確かめる）。
- 配布用パッケージを作るときに、中の .exe・.dll・.pyd の電子署名を確かめるため、osslsigncode（Windows のプログラムの署名を確かめる道具）が要ります。
- 依存するライブラリのライセンスと、配布に必要な表示は「[ライセンス](#ライセンス)」にあります。

## フォルダ構成

| パス | 内容 |
|---|---|
| `src/sumikeshi_studio/` | ツール本体 |
| `src/sumikeshi_studio/bundled_forms/` | 配布用パッケージに入れる3つのサンプルの様式（国税庁の公開様式から作った見本の画像と、様式の情報の `.json`）。様式のフォルダに様式がなければ、インストールのとき（すべてのユーザー向けのインストールでは、各ユーザーの最初の起動のとき）に入れるかどうかを尋ねられます |
| `src/sumikeshi_studio/icons/` | ツールのアイコンの元の絵（SVG）と、そこから作ったアイコンのファイル（ショートカットとアプリの一覧が指す `.ico`）と画面に付ける PNG |
| `tools/make_templates.py` | サンプルの様式を作るスクリプト |
| `tools/make_icons.py` | ツールのアイコンのファイルと PNG を、元の絵の SVG から作るスクリプト（作ったファイルが元の絵と合っていることは `tests/test_icons.py` が確かめる） |
| `tools/make_dist.py` | 配布用パッケージを作るスクリプト |
| `tools/fetch_sources.py` | 配布用パッケージに入る LGPL の部品（Qt・GEOS）のソースのアーカイブをダウンロードし、SHA-256 を照合するスクリプト（Releases のページに添えるもの） |
| `tools/make_qt_licenses.py` | 配布用パッケージに残す Qt の DLL に入っている第三者の部品の許諾文（`licenses/qt/`）と、対応表の行を、Qt のソース（qtbase）から作るスクリプト |
| `tools/make_test_pdfs.py` | テストの検査用の PDF（`tests/data/試験_*.pdf` のうち5つ）を作るスクリプト |
| `tests/` | 自動テスト |
| `tests/data/` | テストデータ（公開様式を描いた画像、架空の番号などを書いた画像、検査用の PDF。番号・氏名・会社名はすべて架空。`tests/data/README.md`） |
| `windows/` | 配布用パッケージに入れるインストール用のファイルと説明書 |
| `dist/` | 配布用パッケージの出力先（git 管理外） |
| `LICENSE` | このツールのライセンス（AGPL-3.0 の全文） |
| `LICENSE-EXCEPTION.txt` | AGPL-3.0 の第7条にもとづく追加の許可 |
| `licenses/` | 配布用パッケージに入れる、依存先のライセンスの全文と対応表（「ライセンス」の節） |
| `pyproject.toml`、`uv.lock`、`.python-version` | 依存するライブラリとバージョン |
| `samples/` | 国税庁が公開している様式（記入なし）。直下の PDF の取得元は「国税庁の様式の出典」の節 |
| `samples/2025bun_01.pdf` | 扶養控除等（異動）申告書 令和7年分 |
| `samples/2026bun_01.pdf` | 扶養控除等（異動）申告書 令和8年分 |
| `samples/2027bun_01.pdf` | 扶養控除等（異動）申告書 令和9年分 |
| `samples/2026bun_04.pdf` | 保険料控除申告書 令和8年分 |
| `samples/2026bun_06.pdf` | 基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書 令和8年分 |
| `samples/2026bun_08.pdf` | 従たる給与についての扶養控除等（異動）申告書 令和8年分 |

## 国税庁の様式の出典

`samples/` の直下の PDF は、国税庁ホームページの公開様式（記入なし）そのものです。配布用パッケージに入れる3つのサンプルの様式の見本の画像（`src/sumikeshi_studio/bundled_forms/` の PNG）と、テストの画像（`tests/data/` の `公開様式_*`・`架空記入_*`）は、Hideki Ohmura が、国税庁ホームページの公開様式を加工して作成しました。国税庁が作成したものではありません。

これらのファイル（国税庁の公開様式と、それを加工して作成したもの）は、AGPL-3.0 の対象ではなく、国税庁ホームページの[利用規約](https://www.nta.go.jp/chuijiko/copy.htm)（[公共データ利用規約（第1.0版）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)）のもとで使えます。

出典：国税庁ホームページ。使った公開様式（PDF）は次のとおりです。

- [2025bun_01.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2025bun_01.pdf)（扶養控除等（異動）申告書 令和7年分）
- [2026bun_01.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_01.pdf)（扶養控除等（異動）申告書 令和8年分）
- [2027bun_01.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2027bun_01.pdf)（扶養控除等（異動）申告書 令和9年分）
- [2026bun_04.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_04.pdf)（保険料控除申告書 令和8年分）
- [2026bun_06.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_06.pdf)（基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書 令和8年分）
- [2026bun_08.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_08.pdf)（従たる給与についての扶養控除等（異動）申告書 令和8年分）
- [2027bun_09.pdf](https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2027bun_09.pdf)（公的年金等の受給者の扶養親族等申告書 令和9年分。`tests/data/` の画像だけに使いました）

## 貢献と連絡

- 外部からのコードの貢献（プルリクエスト）は受け付けません。
- サポート・使い方の質問・不具合の報告は受け付けません。受け付けるのは、GitHub の非公開の脆弱性の報告だけです。受け方は [SECURITY.md](SECURITY.md) にあります。

## ライセンス

Copyright (C) 2026 Hideki Ohmura

このツールは、GNU Affero General Public License 3.0 のみ（AGPL-3.0-only）のもとで配ります。全文は [LICENSE](LICENSE) にあります。AGPL-3.0 の第7条にもとづく追加の許可があり、このツールを Microsoft Visual C++ のランタイムや、Intel IPP ICV を含む OpenCV と組み合わせて配ることを認めています（[LICENSE-EXCEPTION.txt](LICENSE-EXCEPTION.txt)）。配布用パッケージでは、`licenses\sumikeshi-studio\` に同じ全文と追加の許可が入ります。

このツールは無保証です。法律が許す範囲で、作者はこのツールについて何の保証もせず、このツールを使ったことで生じた損害について責任を負いません（AGPL-3.0 の第15条・第16条）。

配布用パッケージに入る依存先のライセンスの全文と、依存先ごとのバージョン・ライセンス・全文の場所の一覧は、配布用パッケージの `licenses\` にあります（一覧は `licenses\index.txt`）。配布用パッケージを作るときに、`tools/make_dist.py` が、依存先の配布物にあるライセンスのファイルと、リポジトリの `licenses/` の全文をコピーして作ります。リポジトリの `licenses/` には、依存先が自分では持っていない全文（GPL-3.0・LGPL-3.0・Apache-2.0 と、配布用パッケージに入れる Qt の部品の許諾文）と、全文ごとの対象を書いた対応表（`licenses/targets.tsv`）を置いています。Qt の部品の許諾文と対応表のその行は、`tools/make_qt_licenses.py` が Qt のソースの `qt_attribution.json` から作ります。

配布に必要な表示：

- Qt for Python（PySide6・shiboken6 6.12.0）は、画面の部品として、GNU LGPL 3.0 のもとで使っています。LGPL 3.0 は GPL 3.0 に許可を足したもので（冒頭の「incorporates the terms and conditions of version 3 of the GNU General Public License, supplemented by the additional permissions listed below」）、第4条 b（「Accompany the Combined Work with a copy of the GNU GPL and this license document.」）が、ライブラリと組み合わせた著作物に GPL 3.0 と LGPL 3.0 の全文を添えることを求めているため、配布用パッケージの `licenses\GPL-3.0.txt`・`licenses\LGPL-3.0.txt` に全文（gnu.org の原文）を入れ、配布の README（日本語版・英語版）にその旨と全文の場所を書いています。配布物に入っている `LicenseRef-Qt-Commercial.txt` はそのまま残しますが、Qt を商用ライセンスで使っているのではありません。Qt の DLL に入っている第三者の部品の許諾文は、配布用パッケージの `licenses\qt\` にあります。Qt のソース（PySide6・shiboken6 の pyside-setup と、Qt の qtbase）は、公開のリポジトリの、配布用パッケージと同じバージョンの Releases のページに置きます（`tools/fetch_sources.py` でダウンロードし、SHA-256 を照合したもの）。
- GEOS：RapidOCR が使う図形の計算のライブラリ shapely に、GEOS 3.14.1 が入っていて、GNU LGPL 2.1 のもとで使っています。許諾文は、配布用パッケージの `licenses\` の shapely の項目にあります（一覧は `licenses\index.txt`）。GEOS のソースは、Qt のソースと同じく、公開のリポジトリの、配布用パッケージと同じバージョンの Releases のページに置きます。
- FreeType：PDFium（pypdfium2）、Pillow（`PIL\_imagingft`）、Qt（Qt6Gui）のそれぞれに、FreeType 2.14.3 が入っています。FreeType の許諾文（The FreeType Project License の「2. Redistribution」）は、バイナリで配るときに、配布物の説明書きに、FreeType Team の成果に一部基づくことを書くよう求めているので、配布の README（`README-ja.txt`・`README-en.txt`）と、この README に、許諾文が例として示す次の文を書きます。`<year>` は、許諾文の「Please replace `<year>` with the value from the FreeType version you actually use.」に従い、FreeType 2.14.3 のソースの著作権の表示（「Copyright (C) 1996-2026」）の年にしました。
  - Portions of this software are copyright © 1996-2026 The FreeType Project (www.freetype.org). All rights reserved.
- pypdfium2 の配布物は、PyPI のものではなく、pypdfium2 5.14.0 のソースの配布物と、pdfium-binaries の chromium/8138 の PDFium から、こちらで作ったものです（`tools/build_pypdfium2.py`。作ったものは `wheels/` に置き、`uv.lock` に SHA-256 を書きます）。PyPI の pypdfium2 5.14.0 の PDFium（156.0.8076）には、脆弱性の修正5件が入っていないためです。Python の部分は PyPI の 5.14.0 と同じで、違うのは PDFium の部品と、その関数の定義（`pypdfium2_raw\bindings.py`）と、許諾文です。
- RapidOCR に同梱の OCR のモデルは、PaddleOCR（Baidu）のモデルを変換したもので、Apache-2.0 で配られています（配布用パッケージの `licenses\rapidocr\MODEL_LICENSES.md`）。
- Visual C++ のランタイム：配布用パッケージには、Microsoft Visual C++ のランタイムの DLL が入っています。どれも配布用パッケージの部品（Python とライブラリ）の配布物に入っているもので、ファイルとその場所、どの配布物から来たか（`tools/make_dist.py` がコピーしたものは、コピー元も）は、配布用パッケージの一覧 `licenses\index.txt` にあります。Microsoft の条件は、配布用パッケージの `licenses\Microsoft-Visual-C++-Runtime.txt` にあります（英語の本文と日本語訳です。英語の文を正とします）。配布用パッケージのユーザーは、その条件に同意したものとする形にしています（インストールのときに同意を求める画面は作らない）。
