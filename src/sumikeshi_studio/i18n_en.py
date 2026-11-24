# Copyright (C) 2026 Hideki Ohmura
# SPDX-License-Identifier: AGPL-3.0-only
"""英語の表

鍵は画面に出す日本語の文（src に書いた文のまま）。同じ日本語で別の英語を当てる文は、(文脈, 日本語の文) の組を
鍵にする（i18n.tr の context）。値は英語の文か、数で語の形が変わる文なら（単数, 複数）の組。語は、表の中の
ほかの文の訳と揃える。

文を足す・変えるときに確かめること（tests/test_structure.py が見つける）：
- 鍵の日本語の文が、src のどこかにある（消した文の英語を残さない）
- 鍵と値とで、{name} の名前と数が同じ
- 値に日本語の文字がない
"""

EN: dict[str | tuple[str, str], str | tuple[str, str]] = {
    # ---- i18n・__init__
    "墨消しスタジオ": "Sumikeshi Studio",  # ツールの名前（APP_NAME）
    "・{item}": "- {item}",  # 箇条の1行（i18n.bullet）
    # ---- uitext（2つ以上の画面に出す文）
    "ファイルを開く": "Open files",
    "フォルダを開く": "Open folder",
    "解析しています…": "Analyzing…",
    "表示の準備をしています…": "Preparing the page for display…",
    "このページを表示できませんでした。\n{error}": "This page could not be displayed.\n{error}",
    "様式のフォルダを読んでいます…": "Reading the forms folder…",
    "起動の準備ができませんでした": "Preparation failed",
    "{failed}。{error}": "{failed}. {error}",
    "{failed}。\n{error}": "{failed}.\n{error}",
    # ---- widgets
    "閉じる": "Close",
    "{what}を開けませんでした（{folder}）。共有フォルダにつながっているか確かめてください。": (
        "Could not open {what} ({folder}). Check that you are connected to the shared folder."
    ),
    # ---- version
    "バージョン {version}（{made} 作成）": "Version {version} (built {made})",
    "バージョン {version}（開発用の環境）": "Version {version} (development environment)",
    # ---- crashlog
    "前回の起動で、ツールが閉じる操作をしないまま終わりました（ウィンドウが急に消えた場合など）。\n"
    "記録：{path}\n"
    "この記録は、原因の調査に役立つ場合があります。": (
        "The last time the tool was started, it ended without being closed (for example, the window "
        "suddenly disappeared).\n"
        "Log: {path}\n"
        "This record may help investigate the cause."
    ),
    # ---- ocr
    "OCR のモデルのファイルがありません（{path}）。ツールをインストールし直してください。": (
        "The OCR model file is missing ({path}). Reinstall the tool."
    ),
    # ---- formstore（様式の情報と、読めない様式の理由）
    "新しいバージョンのツールで保存された様式です。編集するには、ツールを新しいバージョンに更新してください。": (
        "This form was saved with a newer version of the tool. To edit it, update the tool to the newer version."
    ),
    "様式不明": "Unknown form",
    "{name}（第{revision}版、{date} {user}）": "{name} (revision {revision}, {date} {user})",
    "{name}（第{revision}版）": "{name} (revision {revision})",
    "{name}（{file}）：{reason}": "{name} ({file}): {reason}",
    "次の様式を読めないため、ファイルを開けません。": (
        "Files cannot be opened because the following forms cannot be read."
    ),
    "様式のフォルダ：{folder}": "Forms folder: {folder}",
    "起動の画面で「様式を編集」を押し、その様式を削除してください"
    "（様式のファイルの誤りを修正してもかまいません）。起動の画面で、その様式の"
    "「使う」のチェックを外しても、ファイルを開けます。": (
        'On the start screen, click "Edit forms" and delete those forms '
        "(you may also fix the errors in the form files). You can also open files by unchecking "
        '"Use" for those forms on the start screen.'
    ),
    "様式：使う{count}": "Forms: {count} in use",
    "・この PC で使わない{count}": ", {count} not used on this PC",
    "様式のフォルダを読めないため、ファイルを開けません。\n"
    "{folder}\n"
    "共有フォルダにつながっているか確かめてください。"
    "様式のフォルダは、起動の画面の「変更」で選び直せます。": (
        "Files cannot be opened because the forms folder cannot be read.\n"
        "{folder}\n"
        "Check that you are connected to the shared folder. "
        'You can select the forms folder again with "Change" on the start screen.'
    ),
    "ファイルを読めません（{error}）": "Cannot read the file ({error})",
    "ファイルが大きすぎます（上限は{limit}MB）": "The file is too large (the limit is {limit} MB)",
    "ファイルの中身が様式の形になっていません": "The file contents are not in the form format",
    "フォーマットの番号（format・usable_from_format）が正しくありません": (
        "Invalid format number (format, usable_from_format)"
    ),
    "新しいバージョンのツールで保存された様式です（フォーマットの番号 {format}）。"
    "ツールを新しいバージョンに更新してください。": (
        "This form was saved with a newer version of the tool (format number {format}). "
        "Update the tool to the newer version."
    ),
    "識別子（id）がファイル名と合いません": "The ID (id) does not match the file name",
    "名前（name）がありません": "The name (name) is missing",
    "名前（name）に使えない文字（制御文字）があります": (
        "The name (name) contains characters that cannot be used (control characters)"
    ),
    "「{name}」は様式の名前に使えません": '"{name}" cannot be used as a form name',
    "版（revision）が正しくありません": "Invalid revision (revision)",
    "見本の画像のファイル名（image）が正しくありません": "Invalid sample image file name (image)",
    "作業画像の大きさ（size）が正しくありません": "Invalid work image size (size)",
    "保存の記録（updated_at・updated_by）が正しくありません": (
        "Invalid save date/time or user (updated_at, updated_by)"
    ),
    "範囲（{key}）が正しくありません": "Invalid areas ({key})",
    "範囲（{key}）が用紙の外にあります（{name}）": "An area ({key}) is outside the paper ({name})",
    "範囲（{key}）の枠の線（frame）が正しくありません": "Invalid frame lines (frame) of the areas ({key})",
    "題名の語（{key}）が正しくありません": "Invalid title words ({key})",
    "見本の罫線（lines）が正しくありません": "Invalid ruled lines of the sample image (lines)",
    "罫線の一致度の下限（line_match）が正しくありません": "Invalid minimum line match (line_match)",
    "見本の罫線（lines）が用紙の外にあるか、正しくありません": (
        "Ruled lines of the sample image (lines) are outside the paper or invalid"
    ),
    # PNG の確認の文は、見本の画像の名前の後につなぐ述語（"The sample image (…) is …"）
    "PNG の画像ではありません": "is not a PNG image",
    "大きすぎます（縦横の上限は{limit}画素）": "is too large (the limit is {limit} pixels per side)",
    "壊れています（CRC が合いません）": "is broken (CRC mismatch)",
    "壊れています（画像のデータを展開できません）": "is broken (the image data cannot be decompressed)",
    "途中で切れているか、壊れています": "is cut off or broken",
    "壊れています（画像のデータの大きさが合いません）": "is broken (the size of the image data does not match)",
    "見本の画像（{file}）を読めません": "Cannot read the sample image ({file})",
    "見本の画像が大きすぎます（ファイルの上限は{limit}MB）": (
        "The sample image is too large (the file size limit is {limit} MB)"
    ),
    "見本の画像（{file}）が{problem}": "The sample image ({file}) {problem}",
    "見本の画像の大きさが、様式の情報（size）と合いません": (
        "The size of the sample image does not match the form information (size)"
    ),
    # ---- formstext（起動の画面と様式の画面の、様式の一覧と様式の操作の文）
    "題名に次の語があれば、この様式と判別します": (
        "If the title contains the following words, the page is identified as this form"
    ),
    "ただし、題名に次の語があれば、この様式と判別しません": (
        "However, if the title contains the following words, the page is not identified as this form"
    ),
    "「使う」のチェックを外した様式の帳票は、この PC では自動で墨消しされません。": (
        'Documents of forms with "Use" unchecked are not redacted automatically on this PC.'
    ),
    "使う様式が多いほど、解析に時間がかかります。": "The more forms you use, the longer the analysis takes.",
    "選んだフォルダ": "selected folder",
    "既定の場所": "default location",
    "様式を読めませんでした。{error}": "Could not read the forms. {error}",
    "✕ 読めません（{reason}）": "✕ Cannot read ({reason})",
    "✕ この PC では使わない": "✕ Not used on this PC",
    "✓ 使える（新しいバージョンで保存。このバージョンでは編集できません）": (
        "✓ Usable (saved with a newer version; cannot be edited with this version)"
    ),
    "✓ 問題なし": "✓ OK",
    "{when} {user} が保存": "Saved by {user} on {when}",
    "様式のフォルダに様式がありません。このままファイルを開くと、どのページも「様式不明」になり、墨消しする欄が"
    "自動では決まりません。「サンプルの様式を入れる」で、国税庁の様式を入れられます。ほかの帳票は、"
    "「様式を編集」の「新しい様式を登録」で登録できます。": (
        'The forms folder has no forms. If you open files now, every page becomes "Unknown form", and the fields '
        'to redact are not set automatically. You can add the NTA forms with "Add sample forms". You can register '
        'other documents with "Register new form" in "Edit forms".'
    ),
    "サンプルの様式を入れる": "Add sample forms",
    "様式のフォルダ（{folder}）に様式がありません。サンプルとして、次の国税庁の様式を入れますか？": (
        "The forms folder ({folder}) has no forms. Add the following National Tax Agency forms as samples?"
    ),
    "入れる": "Add",
    "入れない": "Don't add",
    "サンプルの様式を入れています…": "Adding the sample forms…",
    "サンプルの様式を入れられませんでした。途中まで入れた様式は削除しました。": (
        "Could not add the sample forms. The forms that had been added were deleted."
    ),
    "様式のフォルダ（{folder}）に、サンプルの様式を入れました。": "Added the sample forms to the forms folder ({folder}).",
    "このアカウントには、自分だけの墨消しスタジオもあります（{folder}）。": (
        "Sumikeshi Studio is also installed just for this account ({folder})."
    ),
    "すべてのユーザー向けのものと2つあると、どちらで起動したかで、使うバージョンが違うことがあります。使わないほうを、"
    "「インストールされているアプリ」からアンインストールしてください。": (
        "With two installations, the version you use can differ depending on which one you start. Uninstall the "
        'one you do not use from "Installed apps".'
    ),
    "第{revision}版": "Revision {revision}",
    "様式のフォルダ：{path}（{origin}）": "Forms folder: {path} ({origin})",
    "{count}件のうち{used}件を使います。": (
        "Using {used} of {count} form.",
        "Using {used} of {count} forms.",
    ),
    "準備しています…": "Preparing…",
    "✓ 準備ができました": "✓ Ready",
    "ページを解析しています（向きと傾きを直しています）…": (
        "Analyzing the page (correcting orientation and skew)…"
    ),
    "様式にするページを選び、「このページを使う」を押してください": (
        'Select the page to use for the form and click "Use this page"'
    ),
    "新しい用紙にするページを選び、「このページを使う」を押してください": (
        'Select the page to use as the new sample image and click "Use this page"'
    ),
    "{done}。ただし、次のことができませんでした。": "{done}. However, the following could not be done:",
    "この様式の見本は、様式『{name}』の見本に類似していて、2つの見本の題名からは見分けられませんでした。"
    "同じ帳票なら、新しく登録せずに、様式『{name}』を編集してください。": (
        'The sample image of this form is similar to the sample image of the form "{name}", and the two '
        "cannot be told apart by their titles. If they are the same document, edit the form "
        '"{name}" instead of registering a new one.'
    ),
    "この様式の見本は、様式『{name}』の見本に類似しています。"
    "類似した様式どうしは、題名でどちらの様式かを決めます。"
    "「{heading}」の欄に、年分と帳票の名前をスペースで区切った1行（案：{line}）を入れてください。": (
        'The sample image of this form is similar to the sample image of the form "{name}". '
        "Similar forms are told apart by their titles. "
        'In the "{heading}" box, enter one line with the year and the document name separated by a space '
        "(suggestion: {line})."
    ),
    "{rows}の行は、『{name}』の見本の題名にもあるので消してください。": (
        'Delete the lines {rows}, because they also appear in the title of the sample image of "{name}".'
    ),
    "保存しますか？": "Save anyway?",
    "選んだページは、登録済みの様式『{name}』の見本に類似しています。": (
        'The selected page is similar to the sample image of the registered form "{name}".'
    ),
    "様式{names}の見本にも類似しています。": (
        "It is also similar to the sample image of the form {names}.",
        "It is also similar to the sample images of the forms {names}.",
    ),
    "選んだページは、置き換える様式『{name}』よりも、様式『{other}』の見本に類似しています。"
    "置き換える様式が『{name}』で正しいか確かめてください。": (
        'The selected page is more similar to the sample image of the form "{other}" than to the form '
        'being replaced, "{name}". Check that "{name}" is the right form to replace.'
    ),
    "様式の名前を入れてください。": "Enter a name for the form.",
    "「{name}」は様式の名前に使えません。": '"{name}" cannot be used as a form name.',
    "様式の名前に使えない文字（制御文字）があります。": (
        "The form name contains characters that cannot be used (control characters)."
    ),
    "「{name}」という名前の様式がすでにあります。別の名前にしてください。": (
        'A form named "{name}" already exists. Use a different name.'
    ),
    "墨消しする範囲を1つ以上描いてください。": "Draw at least one redaction area.",
    "この画像では、元の画像と位置合わせした大きさが合いません"
    "（元の画像と整合した特徴点：{inliers}点）。"
    "保存しません。消す範囲を狭くするか、別のページから登録してください。": (
        "When this image is aligned with the original image, the sizes do not match ({inliers} feature points "
        "matched the original image). The form is not saved. Make the erase areas smaller, or register the form "
        "from another page."
    ),
    "この画像では位置合わせの手がかりが足りません"
    "（元の画像と整合した特徴点：{inliers}点。{needed}点以上が要ります）。"
    "保存しません。消す範囲を狭くするか、別のページから登録してください。": (
        "This image does not have enough features for alignment ({inliers} feature points matched the "
        "original image; {needed} or more are needed). The form is not saved. Make the erase areas smaller, "
        "or register the form from another page."
    ),
    "範囲{names}に記入があるようです。": "The areas {names} seem to have entries.",
    "この様式は、文字や線が少なく、位置合わせの手がかりが少ないようです（{inliers}点）。"
    "スキャンの状態によっては、様式不明になることがあります。": (
        "This form has little text and few lines, so there seem to be few cues for alignment ({inliers} points). "
        'Depending on the condition of the scan, pages may become "Unknown form".'
    ),
    "このページは、用紙が歪んで写っているようです。歪んだ画像を見本にすると、"
    "スキャンしたページで範囲が欄からずれることがあります。": (
        "The paper on this page appears to be distorted in the image. If a distorted image is used as the sample "
        "image, the areas may be off the fields on scanned pages."
    ),
    "元のページの解像度は {dpi}dpi です。見本には {min_dpi}dpi 以上のスキャンを使ってください。": (
        "The resolution of the original page is {dpi} dpi. Use a scan of {min_dpi} dpi or higher for the sample image."
    ),
    "この様式は、ほかの人が様式のフォルダから外しています（削除の操作など）。"
    "保存し直しますか？": (
        "Another user has removed this form from the forms folder (for example, by deleting it). "
        "Save it again?"
    ),
    # ---- launcher（起動の画面）
    "様式": "Form",
    "状態": "Status",
    "使う": "Use",
    "PDF・画像のファイルやフォルダを、この画面にドラッグしても開けます": (
        "You can also open PDF and image files or folders by dragging them onto this window"
    ),
    "帳票を墨消しする": "Redact documents",
    "スキャンした帳票の PDF・画像を開き、様式で決めた欄を墨消しします。": (
        "Open scanned PDF or image files of documents, and redact the fields defined in the forms."
    ),
    "フォルダを": "Folder:",
    "変更": "Change",
    "既定の場所に戻す": "Use default location",
    "読み直す": "Reload",
    "様式を編集": "Edit forms",
    "墨消しに使う様式": "Forms used for redaction",
    "「使う」のチェックを外しますか？": 'Uncheck "Use"?',
    "「{name}」のチェックを外すと、この PC では、その帳票のページの欄が自動で墨消しされなく"
    "なります。その帳票のページは「様式不明」になり、「⚠ 要確認」が付きます。": (
        'If you uncheck "{name}", the fields on pages of that document will no longer be redacted '
        'automatically on this PC. Those pages will become "Unknown form" and be marked "⚠ Review".'
    ),
    "チェックを外す": "Uncheck",
    "チェックを外さない": "Keep checked",
    "開く": "Open",
    "まだファイルを開けません。{reason}": "Files cannot be opened yet. {reason}",
    "様式のフォルダを選ぶ": "Select forms folder",
    "様式のフォルダを既定の場所に戻しますか？": "Use the default location for the forms folder?",
    "既定の場所：{default}。今のフォルダ（{current}）の様式は消えませんが、"
    "この PC では使わなくなります。": (
        "Default location: {default}. The forms in the current folder ({current}) are not deleted, "
        "but they will no longer be used on this PC."
    ),
    "戻さない": "Keep current folder",
    # 様式のフォルダを替えたとき（{count} が 1 なら単数の文）
    "前のフォルダの様式を、選んだフォルダにコピーしますか？": (
        "Copy the forms in the previous folder to the selected folder?"
    ),
    "選んだフォルダ（{new}）には様式がありません。前のフォルダ（{old}）の様式{count}件をコピーできます。"
    "前のフォルダの様式は消えません。": (
        (
            "The selected folder ({new}) has no forms. You can copy the {count} form in the previous folder "
            "({old}). The form in the previous folder is not deleted."
        ),
        (
            "The selected folder ({new}) has no forms. You can copy the {count} forms in the previous folder "
            "({old}). The forms in the previous folder are not deleted."
        ),
    ),
    "コピーする": "Copy",
    "コピーしない": "Don't copy",
    "変更しない": "Keep current folder",
    "既定の場所（{default}）には様式がありません。今のフォルダ（{current}）の様式{count}件を、既定の場所に"
    "コピーできます。今のフォルダの様式は消えません。": (
        (
            "The default location ({default}) has no forms. You can copy the {count} form in the current folder "
            "({current}) to the default location. The form in the current folder is not deleted."
        ),
        (
            "The default location ({default}) has no forms. You can copy the {count} forms in the current folder "
            "({current}) to the default location. The forms in the current folder are not deleted."
        ),
    ),
    "コピーして戻す": "Copy and use default",
    "コピーせずに戻す": "Use default without copying",
    "選んだフォルダの様式を使います。": "The forms in the selected folder will be used.",
    "選んだフォルダ（{new}）には様式が{count}件あります。前のフォルダ（{old}）の様式は消えませんが、"
    "この PC では使わなくなります。": (
        (
            "The selected folder ({new}) has {count} form. The forms in the previous folder ({old}) are not "
            "deleted, but they will no longer be used on this PC."
        ),
        (
            "The selected folder ({new}) has {count} forms. The forms in the previous folder ({old}) are not "
            "deleted, but they will no longer be used on this PC."
        ),
    ),
    "既定の場所（{default}）の様式{count}件を使います。今のフォルダ（{current}）の様式は消えませんが、"
    "この PC では使わなくなります。": (
        (
            "The {count} form in the default location ({default}) will be used. The forms in the current folder "
            "({current}) are not deleted, but they will no longer be used on this PC."
        ),
        (
            "The {count} forms in the default location ({default}) will be used. The forms in the current folder "
            "({current}) are not deleted, but they will no longer be used on this PC."
        ),
    ),
    "様式をコピーできませんでした。様式のフォルダは前のフォルダのままです。原因を取り除いてから、"
    "もう一度お試しください。": (
        "Could not copy the forms. The forms folder is still the previous folder. Remove the cause, and then "
        "try again."
    ),
    "様式をコピーしています…": "Copying the forms…",
    "ファイルは墨消しの画面で開いてください。様式の編集の画面を閉じてから、"
    "起動の画面の「ファイルを開く」で開くか、起動の画面にドラッグしてください。": (
        "Open files on the redaction screen. Close the forms screen, and then open the files with "
        '"Open files" on the start screen, or drag them onto the start screen.'
    ),
    "様式のフォルダを読み書きしています。終わってから閉じてください。": (
        "The forms folder is being read or written. Close the window after it finishes."
    ),
    "予期しないエラーが起きました。\n{kind}: {value}": "An unexpected error occurred.\n{kind}: {value}",
    # ---- filetypes・imaging・review（ファイルを開く）
    "PDF・画像 ({patterns})": "PDF and image files ({patterns})",
    "クリップボードの画像": "Clipboard image",
    "ファイルを読めません（ほかのソフトが使っているか、読む権限がありません）: {name}": (
        "Cannot read the file (another program is using it, or you do not have permission to read it): {name}"
    ),
    "ファイルが見つかりません: {name}": "File not found: {name}",
    "フォルダを読めません（{error}）: {name}": "Cannot read the folder ({error}): {name}",
    "この種類のファイルは開けません: {name}": "This type of file cannot be opened: {name}",
    "ページがありません: {name}": "The file has no pages: {name}",
    "PDF の{page}ページ目を読めません: {name}": "Cannot read page {page} of the PDF: {name}",
    "画像を開けません: {name}": "Cannot open the image: {name}",
    "PDFを開けません（壊れている可能性があります）: {name}": "Cannot open the PDF (it may be broken): {name}",
    "パスワード付きのPDFは開けません: {name}": "Password-protected PDFs cannot be opened: {name}",
    "ファイルを読めません: {name}": "Cannot read the file: {name}",
    "開ける PDF・画像のファイルがありませんでした。": "There were no PDF or image files that could be opened.",
    "次のファイルは開けませんでした。": "The following files could not be opened.",
    "次のファイルは開けませんでした。ほかのファイルは開きます。": (
        "The following files could not be opened. The other files will be opened."
    ),
    "墨消し済みのPDFを{count}件除きました": (
        "Skipped {count} already redacted PDF",
        "Skipped {count} already redacted PDFs",
    ),
    "墨消し済みのPDFを{count}件除きました。": (
        "Skipped {count} already redacted PDF.",
        "Skipped {count} already redacted PDFs.",
    ),
    "名前の末尾が「_redacted」か「_redacted_数字」のファイルは、墨消し済みのPDFとみなして"
    "開きません（「ファイルを開く」で選べば開けます）。": (
        'Files whose names end with "_redacted" or "_redacted_<number>" are treated as already redacted '
        'PDFs and are not opened. To open one, select it with "Open files".'
    ),
    "ほか {count}件": "{count} more",
    "選んだフォルダにはサブフォルダがあります。どのフォルダまでのファイルを開きますか？": (
        "The selected folder contains subfolders. How far down do you want to open files?"
    ),
    "直下だけ": "This folder only",
    "1つ下のフォルダまで": "Down to 1 subfolder level",
    "2つ下まで": "Down to 2 levels",
    "3つ下まで": "Down to 3 levels",
    "すべて": "All levels",
    "{label}：{number}件": ("{label}: {number} file", "{label}: {number} files"),
    "{label}：{number}件以上": "{label}: {number} or more files",
    "{label}：数えています…": "{label}: counting…",
    "ファイルを開いています…": "Opening files…",
    "フォルダを読んでいます（{number}件）…": (
        "Reading folders ({number} file found)…",
        "Reading folders ({number} files found)…",
    ),
    "開いています（{done}／{total}件）": "Opening ({done}/{total} files)",
    "開き終わりしだい、今のファイルは閉じます。そのため、今のファイルは編集できません": (
        "The current file is closed as soon as opening finishes. You cannot edit the current file until then"
    ),
    "墨消しなし": "No redaction",
    # ---- workers（解析のプロセスと出力の進み具合）
    "解析のプロセスが途中で終了しました。": "The analysis process ended unexpectedly.",
    "解析のプロセスを起動できませんでした。": "Could not start the analysis process.",
    "{blocked}\n（{error}）": "{blocked}\n({error})",
    "様式を読めないため、ファイルを開けません。\n{error}": (
        "Files cannot be opened because the forms cannot be read.\n{error}"
    ),
    "{index}／{total}ファイル目（{name}）\n": "File {index}/{total} ({name})\n",
    # ---- boxes・commands（範囲の名前と、元に戻す操作の名前。Qt が「元に戻す」の後に続けて出す）
    "手で追加した範囲": "Manually added area",
    "墨消しする・しないの切り替え": "toggle redaction",
    "範囲の追加": "add area",
    "範囲の削除": "delete area",
    "範囲の移動・大きさの変更": "move or resize area",
    "ページの削除": "delete page",
    "ページの削除の取り消し": "restore page",
    # ---- pageview（ページの表示）
    "削除したページ（墨消しPDFに入れません）": "Deleted page (not included in the redacted PDF)",
    "{kind}：見本の画像から消す範囲（墨消しには使わない）": (
        "{kind}: erase area of the sample image (not used for redaction)"
    ),
    "{kind}：{banner}": "{kind}: {banner}",
    "{kind}：{state}（クリックで切り替え）": "{kind}: {state} (click to switch)",
    ("状態", "墨消しする"): "redacted",
    ("状態", "墨消ししない"): "not redacted",
    "墨消しする": "Redact",
    "墨消ししない": "Do not redact",
    "この範囲を削除": "Delete this area",
    "全体を表示": "Fit to window",
    "拡大": "Zoom in",
    "縮小": "Zoom out",
    "{text}（{key}）": "{text} ({key})",
    # ---- reviewtext（墨消しの画面の一覧・画面の下・ページの上の帯・操作の説明）
    "全ページ削除（出力しない）": "All pages deleted (not exported)",
    "✕ 処理できないページあり": "✕ Has failed pages",
    "解析中…": "Analyzing…",
    "確認 {viewed}／{total}": "Viewed {viewed}/{total}",
    "削除 {count}": "Deleted {count}",
    "⚠ 要確認あり": "⚠ Needs review",
    "✓ 出力済み": "✓ Exported",
    "{number}ページ": "Page {number}",
    "削除（出力しない）": "Deleted (not exported)",
    "✕ 処理できません": "✕ Failed",
    "欄 {auto}（解除 {off}）": "Fields {auto} (off {off})",
    "欄 {auto}": "Fields {auto}",
    "追加 {count}": "Added {count}",
    "⚠ 向き": "⚠ Orientation",
    "⚠ 要確認": "⚠ Review",
    "未表示": "Not viewed",
    "{doc}／{docs}ファイル・{page}／{pages}ページ": "File {doc}/{docs}, page {page}/{pages}",
    "帳票：{form}": "Form: {form}",
    "解析中：残り {count}ページ": (
        "Analyzing: {count} page left",
        "Analyzing: {count} pages left",
    ),
    "✕ 処理できなかったページ：{count}": "✕ Failed pages: {count}",
    "そのページを削除しないと、そのファイルは出力しません": (
        "The file is not exported unless you delete those pages"
    ),
    "まだ表示していないページ：{count}": "Pages not viewed: {count}",
    "このまま出力すると、出力の前に警告が出ます": "If you export now, a warning appears before exporting",
    "出力済み：{done}／{count}ファイル": "Exported: {done}/{count} files",
    "使う様式：{count}": "Forms in use: {count}",
    "✕ この PC で使わない様式：{count}": "✕ Forms not used on this PC: {count}",
    "起動の画面で「使う」のチェックを外した様式です。その帳票のページは、"
    "自動で墨消しされません": (
        'Forms with "Use" unchecked on the start screen. '
        "Pages of those documents are not redacted automatically"
    ),
    "削除したページ　墨消しPDFに入れません。削除を取り消すと元に戻ります": (
        'Deleted page: not included in the redacted PDF. Click "Restore page" to include it again'
    ),
    "⚠ 向き　向きが正しくないおそれがあります": "⚠ Orientation: the orientation may be wrong",
    "⚠ 要確認　{note}": "⚠ Review: {note}",
    "{label}　墨消しする範囲がありません": "{label}: there are no redaction areas",
    "操作の説明": "How to use",
    "{key}：{what}": "{key}: {what}",
    "欄・範囲をクリック": "Click a field or area",
    "墨消しする・しないを切り替える": "Turn redaction on or off",
    "何もない所をドラッグ": "Drag on an empty spot",
    "範囲を追加する": "Add an area",
    "枠の角をドラッグ": "Drag a corner of a frame",
    "大きさを変える": "Resize",
    "枠の中をドラッグ": "Drag inside a frame",
    "動かす": "Move",
    "手で追加した範囲を右クリック": "Right-click a manually added area",
    "削除する": "Delete",
    "右のボタンを押しながらドラッグ": "Drag with the right mouse button held down",
    "ページをスクロールする": "Scroll the page",
    "左の一覧でページを選んで Delete": "Select a page in the list on the left and press Delete",
    "ページを削除する（墨消しPDFに入れない）": "Delete the page (not included in the redacted PDF)",
    "PgUp・PgDown、一覧で上下の矢印キー": "PgUp, PgDown, or the up and down arrow keys in the list",
    "前・次のページに移る": "Go to the previous or next page",
    "Ctrl+Z・Ctrl+Y": "Ctrl+Z, Ctrl+Y",
    "元に戻す・やり直す": "Undo, redo",
    "Ctrl+0・Ctrl++・Ctrl+-": "Ctrl+0, Ctrl++, Ctrl+-",
    "全体を表示・拡大・縮小": "Fit to window, zoom in, zoom out",
    "墨消しPDFを出力": "Export redacted PDF",
    "{label}（{count}件）": ("{label} ({count} file)", "{label} ({count} files)"),
    "起動の準備をしています…": "Preparing…",
    "OCR などを読み込んでいます": "Loading OCR and other components",
    "OCR などを読み込んでいます。選んだファイルは、準備が終わりしだい開きます": (
        "Loading OCR and other components. The selected files open when preparation is finished"
    ),
    "読み終わりしだい選んだファイルを開き、今のファイルは閉じます。そのため、今のファイルは編集できません": (
        "The selected files open as soon as reading finishes, and the current file is closed. "
        "You cannot edit the current file until then"
    ),
    # ---- app（墨消しの画面）
    "様式で決めた欄": "Fields defined in the form",
    "墨消ししない（解除した欄）": "Not redacted (fields turned off)",
    "「ファイルを開く」「フォルダを開く」で PDF・画像を開いてください。\n"
    "PDF・画像のファイルやフォルダを、この画面にドラッグしても開けます。": (
        'Open PDF or image files with "Open files" or "Open folder".\n'
        "You can also open PDF and image files or folders by dragging them onto this window."
    ),
    "このページを削除": "Delete page",
    "削除を取り消す": "Restore page",
    "表示しているページを削除する（左の一覧でページを選んで Delete キーを押しても削除できます）": (
        "Delete the page shown (you can also select a page in the list on the left and press the Delete key)"
    ),
    "ファイルとページ": "Files and pages",
    "操作": "Actions",
    "前のページ": "Previous page",
    "次のページ": "Next page",
    "左に90度回す": "Rotate left 90°",
    "右に90度回す": "Rotate right 90°",
    "元に戻す": "Undo",
    "やり直す": "Redo",
    "元に戻す（Ctrl+Z）": "Undo (Ctrl+Z)",
    "やり直す（Ctrl+Y）": "Redo (Ctrl+Y)",
    "凡例": "Legend",
    "削除したので、このファイルのほかのページを出力できます。": (
        "Because this page is deleted, the other pages of this file can be exported."
    ),
    "このページを削除すると、このファイルのほかのページを出力できます。": (
        "If you delete this page, the other pages of this file can be exported."
    ),
    "このページを処理できませんでした。\n{error}\n\n{hint}": "This page could not be processed.\n{error}\n\n{hint}",
    "このページで解除・追加した内容は消え、墨消しする欄を決め直します。回しますか？": (
        "The fields you turned off and the areas you added on this page will be lost, and the fields to redact "
        "will be determined again. Rotate the page?"
    ),
    "{file} の {number}ページを削除しますか？\n"
    "削除したページは、墨消しPDFに入れません（削除は取り消せます）。": (
        "Delete page {number} of {file}?\n"
        "Deleted pages are not included in the redacted PDF (you can restore them)."
    ),
    "墨消しPDFをまだ出力していないファイルが{count}件あります。作業を破棄しますか？": (
        "{count} file has not been exported as a redacted PDF yet. Discard your work?",
        "{count} files have not been exported as redacted PDFs yet. Discard your work?",
    ),
    # ---- export・exportflow（出力の処理と進み具合）
    "墨消しする範囲の大きさが正しくありません（{kind}）": "Invalid size of a redaction area ({kind})",
    "出力先のフォルダを作れませんでした（{error}）: {folder}": (
        "Could not create the output folder ({error}): {folder}"
    ),
    "開いた後に、入力ファイルが変わっています（大きさか更新日時が違います）。"
    "開き直して確かめてから出力してください。": (
        "The input file has changed since it was opened (its size or modification time is different). "
        "Open it again and check it before exporting."
    ),
    "出力先の同じ名前のファイルを、ほかのソフトが開いている可能性があります（{error}）": (
        "Another program may have the file with the same name in the output folder open ({error})"
    ),
    "1ページ目を墨消ししています": "Redacting page 1",
    "{number}ページ目を墨消ししました": "Redacted page {number}",
    "出力しました": "Exported",
    "画像の圧縮に失敗しました": "Could not compress the image",
    "墨消しPDFの出力先のフォルダを選ぶ": "Select output folder for redacted PDFs",
    "出力しています…": "Exporting…",
    # ---- exporttext（出力の前の確認と、出力の結果）
    "{start}〜{end}": "{start}–{end}",
    "　　表示していないページ：{pages}": "    Pages not viewed: {pages}",
    "　　表示していない「⚠ 要確認」のページ：{pages}": '    "⚠ Review" pages not viewed: {pages}',
    "　　表示していない「⚠ 向き」のページ：{pages}": '    "⚠ Orientation" pages not viewed: {pages}',
    "　　表示していない「{label}」のページ：{pages}": '    "{label}" pages not viewed: {pages}',
    "次のファイルには、表示していないページがあります。": "The following files have pages that you have not viewed.",
    "表示していないページは、墨消しの範囲が確認されないまま出力されます。": (
        "Pages not viewed are exported without their redaction areas being checked."
    ),
    "「⚠ 要確認」のページには、墨消しの範囲が欄に合っていないおそれがある欄か、"
    "様式を判別できずに墨消ししていない用紙があります。": (
        '"⚠ Review" pages have fields whose redaction areas may not match the fields, or sheets that are '
        "not redacted because their forms could not be identified."
    ),
    "「⚠ 向き」のページは、向きを自動で判定できず、確かめていません。": (
        'The orientation of "⚠ Orientation" pages could not be determined automatically '
        "and has not been checked."
    ),
    "「{label}」のページは、墨消しする範囲がないまま出力されます。": (
        '"{label}" pages are exported without any redaction areas.'
    ),
    "表示していないページを確かめずに出力しますか？": "Export without checking the pages not viewed?",
    "元のファイルには上書きできません。別のフォルダを選んでください。": (
        "The original files cannot be overwritten. Select a different folder."
    ),
    "次のファイルは、処理できないページがあるため出力しません。ほかのファイルを出力しますか？": (
        "The following files are not exported because they have pages that failed. Export the other files?"
    ),
    "処理できないページを削除すると、そのファイルも出力できます。": (
        "If you delete the pages that failed, those files can also be exported."
    ),
    "出力先に、同じ名前のファイルがすでにあります。上書きしますか？": (
        "Files with the same names already exist in the output folder. Overwrite them?"
    ),
    "{mark} {name} → {out}　墨消しした範囲：{count}か所": (
        "{mark} {name} → {out}  {count} area redacted",
        "{mark} {name} → {out}  {count} areas redacted",
    ),
    "　　削除したページ：{pages}": "    Deleted pages: {pages}",
    "　　表示していないページ：{pages}（確かめずに出力しました）": (
        "    Pages not viewed: {pages} (exported without being checked)"
    ),
    "　　墨消しする範囲のないページ：{pages}": "    Pages without redaction areas: {pages}",
    "　　作業ログに書けませんでした（{error}）。{file} を開いているソフトがあれば閉じてください。": (
        "    Could not write to the work log ({error}). If a program has {file} open, close it."
    ),
    "　　要確認のページ：{pages}"
    "（墨消しの範囲が欄に合っていないおそれがある欄か、様式を判別できずに墨消ししていない用紙がある"
    "ページ）": (
        "    Pages to review: {pages} (pages with fields whose redaction areas may not match the fields, "
        "or with sheets that are not redacted because their forms could not be identified)"
    ),
    "✗ {name}：出力できませんでした。{error}": "✗ {name}: could not be exported. {error}",
    "{name}：全ページを削除したため、出力しませんでした。": (
        "{name}: not exported, because all pages were deleted."
    ),
    "✗ {name}：処理できないページがあるため、出力しませんでした。そのページを削除すると、出力できます。": (
        "✗ {name}: not exported, because it has pages that failed. "
        "If you delete those pages, it can be exported."
    ),
    "✗ {name}：出力先のパスが長すぎるため、出力しませんでした。": (
        "✗ {name}: not exported, because the output path is too long."
    ),
    "出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。": (
        "They can be exported if you choose an output folder near the top of the drive "
        "(for example, directly under the drive)."
    ),
    "出力先のパスが長すぎるため、出力できません。": (
        "The files cannot be exported, because the output paths are too long."
    ),
    "次のファイルは、出力先のパスが長すぎるため出力しません。ほかのファイルを出力しますか？": (
        "The following files are not exported because their output paths are too long. "
        "Export the other files?"
    ),
    "出力先：{folder}": "Output folder: {folder}",
    "出力先のフォルダを開く": "Open output folder",
    (
        "文の中",
        "出力先のフォルダ",
    ): "the output folder",  # widgets の「{what}を開けませんでした」に入れる語句
    "⚠ や ✗ の付いたファイルを確かめてください。": 'Check the files marked "⚠" or "✗".',
    "作業ログ：{file}": "Work log: {file}",
    "作業ログのフォルダを開く": "Open work log folder",
    (
        "文の中",
        "作業ログのフォルダ",
    ): "the work log folder",  # widgets の「{what}を開けませんでした」に入れる語句
    # ---- forms・titles（解析の注記。句点のない文で、「. 」でつなぐ。画面ではページの上の帯の「⚠ Review: 」の後に出す）
    ("注記の区切り", "。"): ". ",
    "『{name}』のバリエーションのため、範囲がずれているおそれがあります": (
        'This sheet is a variation of the form "{name}", so the redaction areas may be misaligned'
    ),
    "様式を判別できず、墨消ししていません": "The form could not be identified, so this page is not redacted",
    "用紙が多いため、{count}枚を超える分は墨消ししていません": (
        "Many sheets are in the image, so the sheets beyond {count} are not redacted"
    ),
    "様式を判別できない用紙も写っていて、その用紙は墨消ししていません": (
        "Another sheet whose form could not be identified is also in the image, and that sheet is not redacted"
    ),
    "帳票が{count}枚写っています（{names}）": (
        "{count} document is in the image ({names})",
        "{count} documents are in the image ({names})",
    ),
    "すべての欄の範囲が、欄に合っていないおそれがあります": (
        "The redaction areas of all fields may not match the fields"
    ),
    "{names}の欄の範囲が、欄に合っていないおそれがあります": (
        "The redaction area of the field {names} may not match the field",
        "The redaction areas of the fields {names} may not match the fields",
    ),
    "様式『{first}』か『{second}』か決められず、『{first}』で墨消ししました": (
        'The form could not be determined between "{first}" and "{second}", so the page was redacted with '
        '"{first}"'
    ),
    "様式{names}のどれか決められず、『{first}』で墨消ししました": (
        'The form could not be determined among {names}, so the page was redacted with "{first}"'
    ),
    # ---- align・analysis（様式の見本の画像を読めないとき）
    "様式「{name}」の見本の画像が大きすぎます。（{path}）": 'The sample image of the form "{name}" is too large. ({path})',
    "様式「{name}」の見本の画像を読めません。（{path}）": 'Cannot read the sample image of the form "{name}". ({path})',
    "様式「{name}」の見本の画像を、画像として読めません。（{path}）": (
        'The sample image of the form "{name}" cannot be read as an image. ({path})'
    ),
    "見本の画像を PNG にできませんでした": "Could not convert the sample image to PNG",
    # ---- formsave・formcheck（様式の保存）
    "この様式のファイルが、ほかの人によって変えられています。上書きしますか？": (
        "The file of this form has been changed by another user. Overwrite it?"
    ),
    "{user}さんが（{when}）に保存しています。上書きしますか？": "{user} saved this form ({when}). Overwrite it?",
    "見本の画像の名前を決められませんでした（{name} など）": (
        "Could not decide the file name of the sample image ({name}, etc.)"
    ),
    "前の版を「history」に移せませんでした": 'Could not move the previous revision to "history"',
    "見本の画像を「history」に移せませんでした": 'Could not move the sample image to "history"',
    "様式の変更の記録（changes.csv）に書けませんでした": "Could not write to the form change log (changes.csv)",
    "{what}（{error}）": "{what} ({error})",
    "新しい様式": "New form",
    # ---- formlist（様式の画面の一覧と操作）
    ("一覧の題名", "様式"): "Forms",
    "名前": "Name",
    "版": "Revision",
    "保存した日時": "Saved at",
    "保存した人": "Saved by",
    "墨消しする範囲": "Redaction areas",
    "見本から消す範囲": "Erase areas",
    "題名の語": "Title words",
    "編集": "Edit",
    "用紙を置き換える": "Replace sample image",
    "削除": "Delete",
    "新しい様式を登録": "Register new form",
    "保存したツール：{version}": "Saved with: {version}",
    "様式「{name}」の見本の画像を、画像として読めません": (
        'The sample image of the form "{name}" cannot be read as an image'
    ),
    "様式のフォルダを読んでいます。": "Reading the forms folder.",
    "様式のフォルダがありません。": "There is no forms folder.",
    "見本の画像を読んでいます…": "Reading the sample image…",
    "見本の画像を読めません。{error}": "Cannot read the sample image. {error}",
    "新しいページを、今の見本と位置合わせしています…": "Aligning the new page with the current sample image…",
    "今の見本と位置合わせできたので、今の範囲を新しいページの上に移しました。位置を確かめてください。": (
        "The new page was aligned with the current sample image, so the current areas were moved onto the new "
        "page. Check their positions."
    ),
    "新しいページで、次の範囲の枠の線が見つかりませんでした：{names}。"
    "「枠の線」の列を確かめてください。": (
        (
            "The frame lines of the following area could not be found on the new page: {names}. Check the "
            '"Frame lines" column.'
        ),
        (
            "The frame lines of the following areas could not be found on the new page: {names}. Check the "
            '"Frame lines" column.'
        ),
    ),
    "今の見本と位置合わせできなかったので、今の範囲の座標をそのまま置きました。"
    "範囲の位置を確かめ、ずれていれば動かしてください。": (
        "The new page could not be aligned with the current sample image, so the current areas were placed at "
        "the same coordinates. Check the positions of the areas, and move them if they are off."
    ),
    "（{error}）": " ({error})",
    "様式「{name}」を削除しますか？\n"
    "様式のファイルは、様式のフォルダの「history」に移します。"
    "様式のフォルダを使う全員の PC で、この様式を使わなくなります。": (
        'Delete the form "{name}"?\n'
        'The form files will be moved to "history" in the forms folder. The form will no longer be used on any '
        "PC that uses the forms folder."
    ),
    "削除しています…": "Deleting…",
    "削除できませんでした。{error}": "Could not delete the form. {error}",
    "削除しました": "Deleted the form",
    # ---- forminstall（導入でサンプルの様式を入れる）
    "{name}（令和{year}年分）": "{name} (Reiwa {year})",
    "様式のフォルダ（{folder}）に様式がありません。サンプルとして、国税庁の様式（{names}）を入れますか？ "
    "入れるときは Y、入れないときは N を入力して Enter を押してください（何も入力せずに Enter を押すと入れます）。"
    "[Y/n]": (
        "The forms folder ({folder}) has no forms. Add the National Tax Agency forms ({names}) as samples? Type Y "
        "to add them or N not to add them, and then press Enter (press Enter without typing anything to add "
        "them). [Y/n]"
    ),
    "様式のフォルダ（{folder}）に、サンプルの様式を入れました：{names}": (
        "Added the sample forms to the forms folder ({folder}): {names}"
    ),
    "サンプルの様式は入れませんでした。あとで入れるときは、様式のフォルダに様式がない状態で、起動の画面の"
    "「サンプルの様式を入れる」を押してください。": (
        'The sample forms were not added. To add them later, click "Add sample forms" on the start screen while '
        "the forms folder has no forms."
    ),
    "様式のフォルダ（{folder}）に様式があるため、サンプルの様式は入れません。": (
        "The sample forms are not added because the forms folder ({folder}) already has forms."
    ),
    "尋ねているあいだに様式のフォルダ（{folder}）に様式が保存されたため、サンプルの様式は入れませんでした。": (
        "The sample forms were not added because forms were saved to the forms folder ({folder}) while you were "
        "being asked."
    ),
    "起動の画面で選んだ様式のフォルダ（{folder}）に届かないため、サンプルの様式は入れませんでした（{error}）。"
    "インストールは続けます。そのフォルダにつながる状態で様式がなければ、起動の画面の「サンプルの様式を入れる」で"
    "入れられます。": (
        "The sample forms were not added because the forms folder selected on the start screen ({folder}) could "
        "not be reached ({error}). Setup continues. When the folder can be reached and has no forms, you can add "
        'them with "Add sample forms" on the start screen.'
    ),
    "既定の様式のフォルダ（{folder}）を読めないため、サンプルの様式は入れませんでした（{error}）。インストールは続けます。": (
        "The sample forms were not added because the default forms folder ({folder}) could not be read ({error}). "
        "Setup continues."
    ),
    "サンプルの様式を入れられませんでした（{error}）。途中まで入れた様式は削除しました。インストールは続けます。": (
        "Could not add the sample forms ({error}). The forms that had been added were deleted. Setup continues."
    ),
    "引数（{arguments}）が決まりに合わないため、サンプルの様式は入れませんでした。--samples は y か n にしてください。": (
        "The sample forms were not added because the arguments ({arguments}) are not valid. Use y or n for "
        "--samples."
    ),
    # ---- formswin（様式の編集の画面）
    "様式の編集": "Edit forms",
    "クリップボードに画像がありません。用紙の画像をコピーしてから、もう一度貼り付けてください。": (
        "There is no image on the clipboard. Copy an image of the sheet, and then paste it again."
    ),
    "様式にする用紙の PDF・画像を選んでください": "Select a PDF or image of the sheet for the form",
    "新しい用紙の PDF・画像を選んでください": "Select a PDF or image of the new sheet",
    "様式にする用紙を、どこから開きますか？": "Where do you want to open the sheet for the form from?",
    "新しい用紙を、どこから開きますか？": "Where do you want to open the new sheet from?",
    "PDF・画像のファイルを選ぶ": "Select a PDF or image file",
    "クリップボードの画像を貼り付ける": "Paste the clipboard image",
    "やめる": "Cancel",
    "はい": "Yes",
    "いいえ": "No",
    "様式『{name}』を開く": 'Open the form "{name}"',
    "新しい様式として登録を続ける": "Continue registering as a new form",
    "用紙：まだ開いていません": "Sheet: not opened yet",
    "用紙：{name}": "Sheet: {name}",
    "このページを使う": "Use this page",
    "クリップボードの画像から新しい様式を登録する": "Register a new form from the clipboard image",
    "「新しい様式を登録」「用紙を置き換える」で、用紙の PDF・画像のファイルを選ぶか、\n"
    "クリップボードの画像を貼り付けてください（Ctrl+V でも貼り付けられます）": (
        'Select a PDF or image file of the sheet with "Register new form" or "Replace sample image", or\n'
        "paste the clipboard image (you can also paste with Ctrl+V)"
    ),
    "クリップボードの画像（{width}×{height}画素）": "Clipboard image ({width}×{height} pixels)",
    "このページを解析できませんでした。{error}": "This page could not be analyzed. {error}",
    "このページを解析できませんでした。\n{error}": "This page could not be analyzed.\n{error}",
    "置き換える": "Replace",
    # ---- formeditor（範囲を描く画面）
    "墨消しする範囲と、見本から消す範囲の外は、見本の画像として、そのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンから登録してください。": (
        "Everything outside the redaction areas and the erase areas is saved as is in the forms folder as the "
        "sample image. Register the form from a scan of a document without entries."
    ),
    "上下・左右": "Top and bottom, left and right",
    "上下": "Top and bottom",
    "左右": "Left and right",
    "なし": "None",
    "枠の線：印刷された枠の線に合わせて墨消しする辺です。"
    "「なし」の範囲は、描いたとおりに墨消しされ、"
    "写真など、帳票が見本と違う大きさで写っているページでだけ「⚠ 要確認」が付きます。": (
        # 折り返す文に Qt が求める幅（sizeHint）は、この文が約240文字を超えると約2倍（470画素ほど）になり、範囲を描く
        # 画面の右の欄（幅420）より広くなるので、それより短くする
        'Frame lines: the sides fitted to the printed frame lines for redaction. Areas with "None" are redacted '
        'as drawn, and marked "⚠ Review" only when the document appears at a different size from the sample '
        "image, as in photos."
    ),
    "範囲を自動で枠の線に合わせる": "Fit areas to frame lines automatically",
    "チェックがあると、墨消しする範囲を描いたときと大きさを変えたときに、線が見つかった辺を線の中心に合わせます。"
    "動かしたときは合わせません。": (
        "When checked, drawing or resizing a redaction area moves the sides with lines found to the line center. "
        "Moving an area does not."
    ),
    "見本に類似した様式が2つ以上あるページで、どの様式かを題名から判別するのに使います。": (
        "Used to identify the form from the title on pages that are similar to the sample images of two or more "
        "forms."
    ),
    "・行ごとに、別々の条件になります。どれか1行に当てはまれば十分です。": (
        "- Each line is a separate condition. It is enough for the title to match any one line."
    ),
    "・1行の中をスペースで区切ると、区切った語がすべて題名にあるときだけ、その行に当てはまります。"
    "例：「令和8年分　扶養控除等」は、「令和8年分」と「扶養控除等」の両方がある題名にだけ当てはまります。": (
        "- If you separate a line with spaces, the title matches the line only when it contains all of the "
        'words. Example: "2026 Withholding" matches only titles that contain both "2026" and "Withholding".'
    ),
    "様式を登録": "Register form",
    "様式の編集：{name}（新しい用紙への置き換え）": "Edit form: {name} (replacing the sample image)",
    "様式の編集：{name}": "Edit form: {name}",
    "墨消しする範囲を描く": "Draw redaction areas",
    "見本から消す範囲を描く": "Draw erase areas of the sample image",
    "何もない所をドラッグ：範囲を描く　角をドラッグ：大きさを変える　"
    "中をドラッグ：動かす　右クリック：削除　右のボタンでドラッグ：スクロール　"
    "Ctrl+Z：元に戻す": (
        "Drag on an empty spot: draw an area  Drag a corner: resize  Drag inside: move  "
        "Right-click: delete  Drag with the right button: scroll  Ctrl+Z: undo"
    ),
    "題名を読む": "Read title",
    ("表の見出し", "範囲"): "Area",
    "名前（枠の説明に出る）": "Name (shown in the frame tooltip)",
    "枠の線": "Frame lines",
    "様式の名前": "Form name",
    "題名での判別": "Identifying by title",
    "この様式のバリエーションの判別": "Variations of this form",
    "この様式と同じ帳票で、欄の追加などのカスタマイズによりレイアウトが少し違う用紙（この様式のバリエーション）を、"
    "罫線の並び方から判別します。": (
        "Sheets of the same document as this form whose layout differs slightly because of customization such as "
        "added fields (variations of this form) are identified by the order of their ruled lines."
    ),
    "この様式の判別に罫線の並び方を使う": "Use the order of ruled lines to identify this form",
    "罫線の一致度の下限": "Minimum line match",
    "・別の帳票がこの様式と判別されるときは、上げてください。": (
        "- Raise it if other documents are identified as this form."
    ),
    "・この様式の用紙が様式不明になるときは、下げてください。": (
        '- Lower it if sheets of this form become "Unknown form".'
    ),
    "・上げても別の帳票が判別されるときは、上のチェックを外してください。": (
        "- If other documents are still identified after you raise it, uncheck the box above."
    ),
    "範囲": "Areas",
    "保存": "Save",
    "消す範囲{number}": "Erase area {number}",
    "範囲{number}": "Area {number}",
    "見本から消す": "Erase",
    "題名を読んでいます…": "Reading the title…",
    "題名を読めませんでした。{error}": "Could not read the title. {error}",
    "題名を読めませんでした。": "Could not read the title.",
    "読めた題名を、「{heading}」の欄の最後の行に入れました。年分と帳票の名前の語だけを"
    "スペースで区切って残し（例：令和8年分　扶養控除等）、ほかの語は消してください。": (
        'The title that was read was added as the last line of the "{heading}" box. Keep only the words for the '
        "year and the document name, separated by spaces (example: 2026 Withholding), and delete the other "
        "words."
    ),
    "保存の前に確かめています（見本の画像、ほかの様式との位置合わせ、類似した様式の題名）…": (
        "Checking before saving (sample image, alignment with other forms, titles of similar forms)…"
    ),
    "保存の前に確かめることができませんでした。{error}": "Could not check before saving. {error}",
    "ほかの様式の罫線と比べられなかったので、罫線の一致度の下限を {limit} にしています": (
        "The ruled lines could not be compared with other forms, "
        "so the minimum line match is set to {limit}."
    ),
    "保存する": "Save",
    "保存しない": "Do not save",
    "様式のフォルダを確かめています…": "Checking the forms folder…",
    "様式のフォルダを読めません。{error}": "Cannot read the forms folder. {error}",
    "保存しています…": "Saving…",
    "保存できませんでした。{error}": "Could not save. {error}",
    "保存しました": "Saved",
    "記入と歪みを確かめられませんでした。{error}": "Could not check for entries and distortion. {error}",
    "新しいページを確かめています…": "Checking the new page…",
    "見本の画像は、墨消しする範囲の外がそのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンから登録してください。\n登録を続けますか？": (
        "Everything outside the redaction areas of the sample image is saved as is in the forms folder. "
        "Register the form from a scan of a document without entries.\nContinue registering?"
    ),
    "見本の画像は、墨消しする範囲の外がそのまま様式のフォルダに保存されます。"
    "記入のない帳票のスキャンに置き換えてください。\n置き換えを続けますか？": (
        "Everything outside the redaction areas of the sample image is saved as is in the forms folder. "
        "Replace it with a scan of a document without entries.\nContinue replacing?"
    ),
    # ---- cli（コマンドラインからの出力。画面を出さない）
    "使い方：<入力のファイル・フォルダ> … --out <出力のフォルダ> [--depth <数> | --recursive] [--overwrite] "
    "[--forms <様式のフォルダ>] [--language ja|en]\n"
    "スキャンした帳票の PDF・画像を、画面を出さずに解析し、様式で決めた欄を墨消しした PDF"
    "（元の名前_redacted.pdf）を出力します。\n"
    "　　<入力のファイル・フォルダ>　PDF・画像のファイルか、フォルダ（フォルダは、直下の PDF・画像を開きます）\n"
    "　　--out <フォルダ>、-o <フォルダ>　出力先のフォルダ（あるフォルダを指定します）\n"
    "　　--depth <数>　フォルダのサブフォルダの中も、その深さまで開きます（0 は直下だけ、1 は1つ下のフォルダまで）。"
    "出力先には、フォルダからの相対パスのフォルダを作ります\n"
    "　　--recursive　フォルダのサブフォルダの中も、すべての深さまで開きます（--depth と一緒には使えません）\n"
    "　　--overwrite　出力先に同じ名前のファイルがあっても上書きします\n"
    "　　--forms <フォルダ>　使う様式のフォルダ（省くと、起動の画面で選んだ様式のフォルダ）\n"
    "　　--language ja|en　文の言語（省くと、画面と同じ言語）\n"
    "　　--help、-h　この使い方を出します\n"
    "確かめるべきページ（⚠ 要確認・⚠ 向き・様式不明・墨消しなし・✕ 処理できません）のあるファイルも出力し、"
    "そのページを知らせます。出力した後に、そのページを確かめてください。\n"
    "終了の番号：0 すべてのファイルを出力した、1 出力しなかったファイルがある、2 処理を始められなかった、"
    "3 途中で止めた": (
        "Usage: <input files or folders> ... --out <output folder> [--depth <number> | --recursive] [--overwrite] "
        "[--forms <forms folder>] [--language ja|en]\n"
        "Analyzes scanned PDF and image files of documents without the screen, and exports PDFs in which the fields "
        "defined in the forms are redacted (original name_redacted.pdf).\n"
        "    <input files or folders>  PDF or image files, or folders (for a folder, the PDF and image files directly "
        "in it are opened)\n"
        "    --out <folder>, -o <folder>  Output folder (an existing folder)\n"
        "    --depth <number>  Also open the subfolders of the folders, down to this depth (0 is the folder only, "
        "1 is down to 1 subfolder level). In the output folder, folders are created with the paths relative to "
        "the opened folders\n"
        "    --recursive  Also open the subfolders of the folders, at all depths (cannot be used with --depth)\n"
        "    --overwrite  Overwrite a file with the same name in the output folder\n"
        "    --forms <folder>  Forms folder to use (if omitted, the forms folder selected on the start screen)\n"
        "    --language ja|en  Language of the messages (if omitted, the same language as the screen)\n"
        "    --help, -h  Show this usage\n"
        "Files with pages to check (⚠ Review, ⚠ Orientation, Unknown form, No redaction, ✕ Failed) are also exported, "
        "and those pages are listed. Check those pages after the export.\n"
        "Exit codes: 0 all files were exported, 1 some files were not exported, 2 processing could not start, "
        "3 stopped partway"
    ),
    "使い方を見るには --help を付けてください。": "For usage, add --help.",
    "--depth には 0 以上の数を指定してください。": "Specify a number of 0 or more for --depth.",
    "--depth と --recursive は、一緒には使えません。": "--depth and --recursive cannot be used together.",
    "入力のファイルかフォルダを指定してください。": "Specify input files or folders.",
    "出力先のフォルダを --out で指定してください。": "Specify the output folder with --out.",
    "出力先のフォルダがありません：{folder}": "The output folder does not exist: {folder}",
    "{option} の後に値を指定してください。": "Specify a value after {option}.",
    "--language には ja か en を指定してください。": "Specify ja or en for --language.",
    "知らない引数です：{arg}": "Unknown argument: {arg}",
    "{name} {version}：{count}件のファイル（全{pages}ページ）を処理します。": (
        "{name} {version}: processing {count} file (total pages: {pages}).",
        "{name} {version}: processing {count} files (total pages: {pages}).",
    ),
    "様式のフォルダ：{folder}（使う様式 {count}件）": (
        "Forms folder: {folder} ({count} form in use)",
        "Forms folder: {folder} ({count} forms in use)",
    ),
    "使う様式が1つもないため、どのファイルも処理しません（様式のフォルダ：{folder}）。": (
        "No files are processed because there are no forms to use (forms folder: {folder})."
    ),
    "[{index}／{total}] {name}（{count}ページ）": (
        "[{index}/{total}] {name} ({count} page)",
        "[{index}/{total}] {name} ({count} pages)",
    ),
    "　　{number}ページ目を解析しました（{done}／{pages}）": "    Analyzed page {number} ({done}/{pages})",
    "　　✓ {out}　墨消しした範囲：{count}か所": (
        "    ✓ {out}  {count} area redacted",
        "    ✓ {out}  {count} areas redacted",
    ),
    "　　確かめるべきページ：": "    Pages to check:",
    "　　　　{label}：{pages}": "        {label}: {pages}",
    "　　　　{label}（墨消しせずに出力しました）：{pages}": "        {label} (exported without redaction): {pages}",
    "　　　　　　{number}ページ目：{error}": "            Page {number}: {error}",
    "　　{message}": "    {message}",
    "　　✗ {name}：出力しませんでした。{reason}": "    ✗ {name}: Not exported. {reason}",
    "出力先に同じ名前のファイルがあります（{out}）。上書きするときは --overwrite を付けてください。": (
        "A file with the same name exists in the output folder ({out}). To overwrite it, add --overwrite."
    ),
    "出力先のパスが長すぎます（{out}）。出力先のフォルダを浅い場所（ドライブのすぐ下など）にすると出力できます。": (
        "The output path is too long ({out}). It can be exported if you choose an output folder "
        "near the top of the drive (for example, directly under the drive)."
    ),
    "出力したファイル：{done}件、出力しなかったファイル：{failed}件、確かめるべきページのあるファイル：{check}件": (
        "Exported files: {done}, files not exported: {failed}, files with pages to check: {check}"
    ),
    "途中で止めました。残りの{count}件のファイルは処理していません。": (
        "Stopped. The remaining {count} file was not processed.",
        "Stopped. The remaining {count} files were not processed.",
    ),
    # ---- nativecheck（導入で、ツールの部品を読み込めるかを確かめたときの文。起動の準備に失敗したときの文にも使う）
    "この画面のスクリーンショットを撮っておくと、原因の調査に役立つ場合があります。": "A screenshot of this window may help investigate the cause.",
    "{library}：{file}": "{library}: {file}",
    "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
    "ツールが使う次の部品を止めました。このままでは、ツールは起動しないか、途中で止まります。": (
        "Windows Smart App Control (or your organization's application control) blocked the following "
        "components that the tool uses. As it is, the tool will not start or will stop partway."
    ),
    "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
    "ツールが使わない次の部品を止めました。ツールの動きには影響しません。": (
        "Windows Smart App Control (or your organization's application control) blocked the following "
        "components that the tool does not use. This does not affect how the tool works."
    ),
    "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
    "ツールの部品（{name}）を止めました。配布用パッケージの ZIP を展開して setup.bat を実行すると、"
    "止められた部品の名前が出ます。": (
        "Windows Smart App Control (or your organization's application control) blocked a component of "
        "the tool ({name}). Extract the ZIP of the package and run setup.bat to see the names of the blocked "
        "components."
    ),
    "Windows のスマート アプリ コントロール（または会社のアプリケーション制御）が、"
    "ツールの部品を止めました。配布用パッケージの ZIP を展開して setup.bat を実行すると、"
    "止められた部品の名前が出ます。": (
        "Windows Smart App Control (or your organization's application control) blocked a component of "
        "the tool. Extract the ZIP of the package and run setup.bat to see the names of the blocked components."
    ),
    "Windows ではないため、確かめません": "Not checked, because this is not Windows",
    "ツールの部品は、すべて読み込めました。": "All components of the tool could be loaded.",
}
