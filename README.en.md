# Sumikeshi Studio (墨消しスタジオ)

![Sumikeshi Studio opening a form with fictional entries, checking the fields to redact, and exporting a redacted PDF (the screens in the video are in Japanese)](assets/demo.webp)

(The screens and setup messages are in Japanese and English. The Japanese README is [README.md](README.md).)

Sumikeshi Studio is a tool that, using the settings of a form registered in advance, automatically redacts (blacks out) fields on sheets filled in on that form. From scanned PDFs or photographs of sheets, it automatically detects the fields that the form specifies for redaction, and exports PDFs in which those fields are redacted. It works without an Internet connection.

## Features

- **Fields are redacted automatically once a form is registered**: Register a document without entries as a form and define the fields to redact, and when you open a sheet of that form, the tool finds the fields automatically and redacts them. The contents of the fields are not read.
- **Sample forms of National Tax Agency declarations included**: Three declarations, 扶養控除等申告書, 保険料控除申告書, and 基・配・特・所の申告書, can be added as forms that redact the My Number (Individual Number) fields.
- **Scanned PDFs and photographs alike**: PDF and image files (JPEG, PNG, TIFF, and BMP) can be opened. Skewed photographs and sheets whose fields are in slightly different positions, such as declarations printed by payroll software, can also be handled.
- **Check on the screen before exporting**: Check the automatically determined areas on the screen, and switch redaction on or off for each field or add areas. Pages that the tool cannot fully decide automatically are marked "⚠ Review".
- **PDFs that keep no text data**: The output is an image-only PDF. The text data of the original file (such as invisible text added by the scanner's OCR) is not carried over.
- **No external communication**: Everything runs only on the PC.
- **Forms can be shared**: If the forms folder is a shared folder, everyone can use the same forms.

## Requirements

| Item | Details |
|---|---|
| OS | Windows (64-bit). The distribution package is built for 64-bit Windows. Operation on each version of Windows has not been checked |
| Administrator rights | Not required for just-for-me installation or for starting the tool. Required for installation for all users |
| Internet | Not required when setting up from the distribution package or when using the tool. Required when creating the development environment and when building the distribution package |
| Python | The distribution package includes Python 3.14. In the development environment, uv installs Python 3.14. The tool does not run on 3.15, because onnxruntime has no distribution for Python 3.15 (the latest version as of 2026-10-29, 1.31.1, supports up to 3.14) |
| CPU and memory | If there are 4 or more logical CPUs and 4 GB or more of free memory, two analysis processes are used to analyze two pages at the same time. Otherwise, one process is used |
| Development environment | Development and automated tests are done on Linux (see "[Development environment](#development-environment)"). The PCs that use the tool are assumed to run Windows |

## Setup

### Setting up from the distribution package (no Internet connection required)

The distribution package is a ZIP that contains Python, the libraries, and the tool itself (for how to build it, see "[Building the distribution package](#building-the-distribution-package)"). The latest package is on the GitHub Releases page of the public repository (https://github.com/hohm-sdx/sumikeshi-studio). There is no commitment to when a new package is released. The changes in each version are in the release notes of that version on the Releases page.

1. If you downloaded the ZIP from the Internet (for example, from the Releases page of the public repository), before extracting it, right-click the ZIP → "Properties" → select the "Unblock" check box on the "General" tab → "OK". Windows marks a downloaded ZIP as coming from the Internet, and if you extract the ZIP with this mark, Smart App Control may block `setup.bat`. For a ZIP without the mark (for example, one received by copying it from a file server or a USB drive), the "Unblock" check box does not appear, so this step is not needed. If `setup.bat` is blocked, delete the extracted folder, select "Unblock" for the ZIP, and then extract it again.
2. Extract all files from the ZIP (anywhere is fine, but installation for all users cannot be done from a network folder).
3. Double-click `setup.bat` in the extracted `sumikeshi-studio` folder, and choose the type of installation (pressing Enter alone chooses 1). 1 (Just for me) moves the extracted folder to `%LOCALAPPDATA%\Programs\SumikeshiStudio` without administrator rights (on another drive, it copies the folder and deletes the original). 2 (All users of this PC) copies it to `C:\Program Files\SumikeshiStudio` with administrator rights. Both add the tool to "Installed apps" in Windows and create shortcuts on the Start menu and the desktop. They are named "墨消しスタジオ" on Japanese Windows and "Sumikeshi Studio" on other Windows. The setup messages are shown in the language selected in the tool (if none is selected, in the Windows display language). No network communication takes place. If the forms folder has no forms, you are asked whether to add the National Tax Agency forms (the three declarations) as samples (just for me: during the installation; all users: the first time each user starts the tool). If the folder has forms, nothing is added.
4. Start the tool from the shortcut on the Start menu or the desktop.

To update to a new version, extract the new ZIP and choose the same type with `setup.bat`. To uninstall, use "Installed apps" or `uninstall.bat` in the installed folder. Instructions for the people who use the tool, such as the length of the destination path, what uninstalling deletes, and the forms folder when several users use the tool, are in the README in the ZIP (Japanese version [windows/README-ja.txt](windows/README-ja.txt), English version [windows/README-en.txt](windows/README-en.txt)).

### Running from source (for development and testing; requires an Internet connection)

Create the environment with uv as described in "[Development environment](#development-environment)" (`uv sync --locked`), and run `uv run python -I -m sumikeshi_studio` in the root of the repository to start the tool. To add the sample forms, run `uv run python -I -m sumikeshi_studio.forminstall` (if the forms folder has no forms, it asks whether to add them).

## Usage

1. When you start the tool, the start screen appears. At the top are the version and the status of the preparation; below them are the list of forms, whether each form is used for redaction on this PC (the "Use" check box), and the forms folder. Select the forms folder with "Change" next to "Folder:". If you do not select one, the default location (`%LOCALAPPDATA%\SumikeshiStudio\forms`) is used. When you change the forms folder, if the new folder has no forms and the previous folder has forms, the tool asks whether to copy the forms in the previous folder. If there are no forms at all, the start screen says so (you can open files, but every page becomes "Unknown form").
2. Click "Open files" and select PDF or image files (you can also open a whole folder with "Open folder", or drag PDF and image files or folders onto the start screen). Multiple files and folders can be opened at once. The redaction screen opens, analyzes all pages in the background, and shows the fields defined in the forms as fields to redact (orange frames). On the redaction screen, too, you can open files with "Open files" (Ctrl+O), "Open folder", or by dragging.
3. Check the pages while moving through them in the list on the left. Clicking a field switches between redacting it and not redacting it, and dragging on an empty spot adds an area. For pages with the "⚠ Review" or "⚠ Orientation" label, read the reason shown in the banner above the page, and check them with particular care. The "How to use" button shows the list of operations.
4. Click "Export redacted PDF" (at the right end of the toolbar; Ctrl+S) and select the output folder. One "original name_redacted.pdf" is created for each input file.

To add or edit forms, open the forms screen with "Edit forms" on the start screen (forms cannot be edited from the redaction screen). When registering forms whose sample images are similar, such as the forms for 2026 (令和8年分) and 2027 (令和9年分), enter one line with the year (年分) and the document name separated by a space (example: 令和8年分　扶養控除等) in "Identifying by title" on the area editor. How to handle documents whose form is revised every year is described in "When a form is revised every year" in the distribution README.

- Only one instance of the tool runs at a time. If you start the tool again while it is running, the current window comes to the front.
- Work log: `%LOCALAPPDATA%\SumikeshiStudio\worklog.csv` (one row for each exported file; it can be opened in Excel)
- Crash log: `%LOCALAPPDATA%\SumikeshiStudio\crash.log` (used to investigate the cause when the window suddenly disappears)

### Exporting from the command line (without the screen)

You can also analyze files and export redacted PDFs without showing the screen. In the distribution package, use `sumikeshi.bat` (example: `sumikeshi.bat C:\scan --out C:\out`). In the development environment, use `uv run python -m sumikeshi_studio.cli <input files or folders> --out <output folder>`.

- Because nobody checks the pages on the screen, files that have pages to check ("⚠ Review", "⚠ Orientation", "Unknown form", "No redaction", and "✕ Failed") are also exported; those pages are printed to standard output by type and are also written to the work log. Check those pages after the export. Pages that could not be analyzed are exported without redaction.
- The forms used are the forms checked in "Use" in the forms folder selected on the start screen (you can change the folder with `--forms`). If the forms folder cannot be read, if any form in use cannot be read, or if there are no forms in use, no files are processed.
- If a file with the same name exists in the output folder (`--out`; an existing folder), that file is not exported (`--overwrite` overwrites it).
- The exit code is 0 if all files were exported, 1 if some files were not exported, 2 if processing could not start, and 3 if it was stopped with Ctrl+C. `--help` shows the list of arguments.
- When calling from another program or script, pass folders instead of passing file names one by one, or call `python\python.exe` in the package directly with `-I -X utf8 -m sumikeshi_studio.cli`. Arguments passed to a `.bat` file are read by cmd.exe, so if a file name containing `&` or a similar character is passed without quotes, the part after it may be run as a separate command.

## How it works

Processing flow for one page:

1. The page is rendered as an image and aligned with the sample images (images of the documents without entries) of the forms (the forms in the forms folder that are used on this PC). The form that aligns determines the document type, and the orientation (in 90-degree steps) is also determined. The skew is estimated from the straight lines on the page. If two or more forms align with the same part of the sheet (forms whose sample images are similar, such as the forms for 2026 (令和8年分) and 2027 (令和9年分)), the part of the page that corresponds to the band at the top of the sample image is read with OCR (RapidOCR, bundled with the tool), and the form is determined by the title words of each form (example: the title words of the 2026 form are set to the single line "令和8年分　扶養控除等"; if the form cannot be determined, the redaction areas are taken from the more similar form, and the page is marked "⚠ Review").
2. If the page cannot be aligned with any form, the orientation is determined with OCR, and the tool checks, by the order of the ruled lines of the sample images, whether the page is a variation of a form (the title is not read). A variation of a form is a sheet whose fields are in the same order as the sample image but whose row heights, column widths, or typeface differ (such as declarations printed by payroll software). The ruled lines of the sample image and the page are matched in order to determine the type and place the fields. If a form is determined, the areas are marked "⚠ Review". If no form is determined, the page is treated as "Unknown form", is not redacted, and is marked "⚠ Review".
3. Near the positions defined in the form, the printed frame lines of each field are searched for, the edges of the redaction are aligned to the centers of the lines (so that the redaction does not extend beyond the frame), and the field is shown on the screen as a field to redact. The contents of the fields are not read, and all fields in the form are redacted. For a field whose frame lines are not found (for example, in a black-and-white binary image in which thin lines have disappeared), the position in the form is used as the redaction area as it is, and the page is marked "⚠ Review", as it is for a field with entries written beyond the frame lines (the redaction is not extended automatically). Check the page on the screen, and correct it if needed (you can switch redaction on or off for each field, add areas manually, and delete unneeded pages). For a page with fields whose frame lines are not found (when one sheet was aligned and it does not appear distorted), the areas are also placed by the order of the ruled lines of the sample image as in step 2, and only when fewer fields are left without frame lines, all the areas are replaced with them and the page is marked "⚠ Review" (a sheet that differs from the sample image only in row heights and column widths).
4. On export, the page is loaded at its original resolution, the pixels in the confirmed areas are set to black, and then an image-only PDF is rebuilt (OCR is not used). The file is saved in the output folder under a temporary name and then renamed to its final name. If the input file has changed after it was opened, it is not exported. The output is not inspected at each export; the same inspections (that the redacted areas are black, that no text data remains, and so on) are performed by tests when the code or the libraries are changed.

Identifying variations of a form:

- The condition for identifying a variation (the minimum line match) is kept for each form, and you can change it with the slider under "Variations of this form" in the area editor, opened by editing the form (raise it if other documents are identified as that form, and lower it if sheets of that form become "Unknown form"; if other documents are still identified after you raise it, uncheck the box so that the form is not used for identifying by the order of ruled lines).
- How far a page that became "Unknown form" was below the minimum is recorded in the "Line match below limit" column of the work log.
- Only forms that have the ruled lines of their sample image are used for searching by the order of ruled lines (the ruled lines are read from the sheet without entries and saved in the form when a form is registered and when a form is saved after "Replace sample image"; the sample forms have the ruled lines from the start; forms added or registered with an earlier version can be used after "Replace sample image" is done and the form is saved).

Sample forms:

The distribution package contains sample forms of the following three declarations, made from forms published by the National Tax Agency (the fields to redact are the My Number (Individual Number) fields), and if the forms folder has no forms, you are asked whether to add them (for just-for-me installation, during setup; for installation for all users, when each user starts the tool for the first time).

- 扶養控除等（異動）申告書
- 保険料控除申告書
- 基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書

Rules the tool follows:

- It does not communicate over the network after it starts.
- It does not write images before redaction to disk (intermediate images are handled only in memory; the same applies to clipboard images pasted on the forms screen).
- Windows Error Reporting (WER) for a forced termination does not include the heap of the tool's processes (`WerSetFlags`; this affects only the tool's processes and does not change Windows settings).
- Analysis runs in a process separate from the screen (the analysis process), so that the screen does not freeze during analysis.

## Development

### Development environment

Development and automated tests are done on Linux (Debian 13). The environment is created with uv.

1. Install uv (0.13.6) (for how to install it, see the uv documentation https://docs.astral.sh/uv/getting-started/installation/ ).
2. Run `uv sync --locked` in the root of the repository. Python 3.14 and the libraries whose versions are pinned in `uv.lock` are installed in `.venv`. pypdfium2 is installed from the distribution in `wheels/`, not from PyPI (see the "[License](#license)" section). `uv.lock` is resolved only for Windows and Linux, so the environment cannot be created on other operating systems.
3. The GUI tests need the following libraries that Qt loads (Debian and Ubuntu package names; they can be installed with `apt install`): libgl1, libglx0, libglvnd0, libegl1, libopengl0, libxkbcommon0, libdbus-1-3, libfreetype6, libfontconfig1. The screens run without being displayed (`QT_QPA_PLATFORM=offscreen`). Without these libraries, the GUI tests are skipped instead of failing, so add `-rs` to check the skipped tests and the reasons.
4. To build the distribution package, osslsigncode is also needed (see "[Building the distribution package](#building-the-distribution-package)").
5. The tests that parse the PowerShell scripts (the .ps1 files in `windows/scripts/`) with the PowerShell parser need PowerShell 7 (`pwsh`). Without it, those tests are skipped instead of failing (check with `-rs`).

### Running, testing, and formatting the code

```bash
uv run python -m sumikeshi_studio                     # Start the tool (the start screen appears)
uv run pytest                                    # Regular tests
uv run pytest -m regression                      # Regression tests (whether the identification and the redaction areas are the same as the records)
uv run pytest -m "regression or not regression"  # Both
uv run pytest -n 4 -m "regression or not regression"  # Both, run in parallel in 4 processes
uv run ruff format src tests tools && uv run ruff check src tests tools
```

### Building the distribution package

```bash
uv run python tools/make_dist.py   # Run with an Internet connection. Requires osslsigncode
```

This creates `dist/sumikeshi-studio-<version>-<date>-win64.zip`. It contains Python 3.14 for Windows (the embeddable package from python.org), the Windows distributions of the libraries whose versions are pinned in `uv.lock`, the tool itself, and the setup files (`windows/`). Python is checked against the SHA-256 written in `tools/make_dist.py` (the value from the python.org list of downloads), and the libraries against the SHA-256 in `uv.lock`, before they are added.

- Each time the package is built, the signatures of all .exe, .dll, and .pyd files in the package are checked with osslsigncode, and if any file departs from the rules (the publisher and the list of 38 unsigned files), the build stops and shows the file name and the reason (because Windows Smart App Control may block unsigned components). Whether the signing certificates have been revoked is also checked, with the CRLs obtained from the CRL distribution points of the certificates in the signatures (so the hosts contacted during the build also include the CRL distribution points (www.microsoft.com and crl3.digicert.com)). On Debian and Ubuntu, osslsigncode can be installed with `apt install osslsigncode`. If it is not on the PATH, pass its location in the environment variable `OSSLSIGNCODE`.
- The Python in the package loads modules only from the locations written in `python314._pth`, and is started with `-I` (it does not use the PC's environment variables such as PYTHONPATH).
- Setup (`setup.bat`) checks that the bundled Python can be started and that each component of the tool can be loaded, and if Smart App Control or similar blocks them, it shows the blocked components and indicates that a screenshot of that window may help investigate the cause. After that, you choose the type of installation, and for just-for-me installation, if the forms folder has no forms, it asks whether to add the sample forms (for installation for all users, it asks when each user starts the tool for the first time).

- On Windows, extraction may fail if a path exceeds 260 characters, so the parts that the tool does not load (such as the QML components of the GUI library) are excluded, and the top folder in the ZIP is given the short name `sumikeshi-studio`. Without the exclusion, 3 paths exceed 260 characters when the ZIP is extracted with "Extract All" in the Downloads folder. The longest path in the ZIP and the maximum length of the destination folder path are measured when the package is built, and are written into the Japanese and English versions of the README.
- The tool itself is inside the package, so when the program is changed, rebuild the package and distribute it again.
- The forms folder is selected on the start screen of each PC, so the package contains no settings file.
- The package is built in a Linux environment, and its contents (including the signatures) are checked there.

### Building the sample forms

```bash
uv run python tools/make_templates.py
```

From the first page of each of the National Tax Agency's published forms (`samples/20xxbun_xx.pdf`), this creates, in `src/sumikeshi_studio/bundled_forms/`, the sample images and the form information (`.json`) of the three sample forms included in the distribution package (identifiers `nta-fuyou-r9`, `nta-hoken-r8`, and `nta-kihai-r8`). The positions of the My Number (Individual Number) fields are the centers of the frame lines measured on the published forms (`PUBLIC_FIELDS` in `tools/make_templates.py`). Each build produces the same files, and `tests/test_make_templates.py` checks that the rebuilt files are the same as the committed ones. If this test fails after you upgrade a library, check with the regression tests that the identification and the areas do not change, and then commit the rebuilt files. If the forms folder has no forms, setup (`setup.bat`) asks whether to add these forms.

## Folder structure

| Path | Contents |
|---|---|
| `src/sumikeshi_studio/` | The tool itself |
| `src/sumikeshi_studio/bundled_forms/` | The three sample forms included in the distribution package (the sample images made from the National Tax Agency's published forms, and the form information `.json`). If the forms folder has no forms, you are asked whether to add them during setup (for installation for all users, when each user starts the tool for the first time) |
| `src/sumikeshi_studio/icons/` | The original drawings (SVG) of the tool icon, and the icon file (`.ico`, used by the shortcuts and the app list) and the PNG images for the windows that are made from them |
| `tools/make_templates.py` | Script that builds the sample forms |
| `tools/make_icons.py` | Script that builds the icon file and the PNG images from the original SVG drawings (`tests/test_icons.py` checks that the built files match the drawings) |
| `tools/make_dist.py` | Script that builds the distribution package |
| `tools/fetch_sources.py` | Script that downloads the source archives of the LGPL components in the package (Qt and GEOS) and checks their SHA-256 (the archives are attached to the Releases page) |
| `tools/make_qt_licenses.py` | Script that creates, from the Qt source (qtbase), the license texts of the third-party components contained in the Qt DLLs kept in the package (`licenses/qt/`) and the corresponding rows of the mapping table |
| `tools/make_test_pdfs.py` | Script that creates PDFs used for checks in the tests (5 of the `tests/data/試験_*.pdf` files) |
| `tests/` | Automated tests |
| `tests/data/` | Test data (images rendered from the published forms, images with fictitious numbers and other entries written on them, and PDFs used for checks. All numbers, names, and company names are fictitious. `tests/data/README.md`) |
| `windows/` | Setup files and instructions included in the distribution package |
| `dist/` | Output location of the distribution package (not tracked by git) |
| `LICENSE` | License of this tool (the full text of the AGPL-3.0) |
| `LICENSE-EXCEPTION.txt` | Additional permission under section 7 of the AGPL-3.0 |
| `licenses/` | Full texts of the licenses of the dependencies, and the mapping table, included in the distribution package (see the "License" section) |
| `pyproject.toml`, `uv.lock`, `.python-version` | Dependent libraries and their versions |
| `samples/` | Forms published by the National Tax Agency (without entries). The sources of the PDFs directly in this folder are in the "Source of the National Tax Agency forms" section |
| `samples/2025bun_01.pdf` | 扶養控除等（異動）申告書, 令和7年分 (2025) |
| `samples/2026bun_01.pdf` | 扶養控除等（異動）申告書, 令和8年分 (2026) |
| `samples/2027bun_01.pdf` | 扶養控除等（異動）申告書, 令和9年分 (2027) |
| `samples/2026bun_04.pdf` | 保険料控除申告書, 令和8年分 (2026) |
| `samples/2026bun_06.pdf` | 基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書, 令和8年分 (2026) |
| `samples/2026bun_08.pdf` | 従たる給与についての扶養控除等（異動）申告書, 令和8年分 (2026) |

## Source of the National Tax Agency forms

The PDFs directly in `samples/` are forms published on the National Tax Agency website (without entries), as they are. The sample images of the three sample forms included in the distribution package (the PNG files in `src/sumikeshi_studio/bundled_forms/`) and the test images (`公開様式_*` and `架空記入_*` in `tests/data/`) were created by Hideki Ohmura by processing forms published on the National Tax Agency website. They were not created by the National Tax Agency.

These files (the forms published by the National Tax Agency and the files created by processing them) are not covered by the AGPL-3.0; they can be used under the terms of use of the National Tax Agency website (Public Data License (Version 1.0); https://www.nta.go.jp/chuijiko/copy.htm , full text at https://www.digital.go.jp/resources/open_data/public_data_license_v1.0 ).

Source: National Tax Agency website. The published forms (PDF) used are the following.

- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2025bun_01.pdf (扶養控除等（異動）申告書, 令和7年分 (2025))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_01.pdf (扶養控除等（異動）申告書, 令和8年分 (2026))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2027bun_01.pdf (扶養控除等（異動）申告書, 令和9年分 (2027))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_04.pdf (保険料控除申告書, 令和8年分 (2026))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_06.pdf (基礎控除申告書 兼 配偶者控除等申告書 兼 特定親族特別控除申告書 兼 所得金額調整控除申告書, 令和8年分 (2026))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_08.pdf (従たる給与についての扶養控除等（異動）申告書, 令和8年分 (2026))
- https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2027bun_09.pdf (公的年金等の受給者の扶養親族等申告書, 令和9年分 (2027); used only for the images in `tests/data/`)

## Contributions and contact

- External code contributions (pull requests) are not accepted.
- Support requests, usage questions, and bug reports are not accepted. Only private vulnerability reports through GitHub are accepted. How to report is described in [SECURITY.md](SECURITY.md).

## License

Copyright (C) 2026 Hideki Ohmura

This tool is distributed under the GNU Affero General Public License 3.0 only (AGPL-3.0-only). The full text is in [LICENSE](LICENSE). There is an additional permission under section 7 of the AGPL-3.0, which permits distributing this tool combined with the Microsoft Visual C++ Runtime or with OpenCV including Intel IPP ICV ([LICENSE-EXCEPTION.txt](LICENSE-EXCEPTION.txt)). In the distribution package, the same full text and the additional permission are in `licenses\sumikeshi-studio\`.

This tool comes with no warranty. To the extent permitted by applicable law, the author provides no warranty for this tool and is not liable for any damages arising from the use of this tool (sections 15 and 16 of the AGPL-3.0).

The full texts of the licenses of the dependencies included in the distribution package, and the list of the version, the license, and the location of the full text for each dependency, are in `licenses\` of the package (the list is `licenses\index.txt`). When the package is built, `tools/make_dist.py` creates them by copying the license files in the distributions of the dependencies and the full texts in `licenses/` of the repository. `licenses/` in the repository holds the full texts that the dependencies do not include themselves (GPL-3.0, LGPL-3.0, Apache-2.0, and the license texts of the Qt components included in the package), and a mapping table that lists what each full text applies to (`licenses/targets.tsv`). The license texts of the Qt components and their rows in the mapping table are created by `tools/make_qt_licenses.py` from `qt_attribution.json` in the Qt source.

Notices required for distribution:

- Qt for Python (PySide6 and shiboken6 6.12.0) is used for the user interface components under the GNU LGPL 3.0. The LGPL 3.0 is the GPL 3.0 supplemented by additional permissions (at its beginning: "incorporates the terms and conditions of version 3 of the GNU General Public License, supplemented by the additional permissions listed below"), and section 4 b ("Accompany the Combined Work with a copy of the GNU GPL and this license document.") requires that the full texts of the GPL 3.0 and the LGPL 3.0 accompany a work combined with the library. Therefore, the full texts (the original texts from gnu.org) are included in `licenses\GPL-3.0.txt` and `licenses\LGPL-3.0.txt` of the package, and the distribution README (Japanese and English versions) states this and where the full texts are. `LicenseRef-Qt-Commercial.txt`, which is included in its distribution, is kept as it is, but Qt is not used under a commercial license. The license texts of the third-party components contained in the Qt DLLs are in `licenses\qt\` of the package. The Qt source (pyside-setup for PySide6 and shiboken6, and qtbase of Qt) is placed on the Releases page of the public repository for the same version as the package (downloaded with `tools/fetch_sources.py`, with the SHA-256 checked).
- GEOS: shapely, the geometry library used by RapidOCR, includes GEOS 3.14.1, which is used under the GNU LGPL 2.1. The license text is in the shapely entry in `licenses\` of the package (the list is `licenses\index.txt`). Like the Qt source, the GEOS source is placed on the Releases page of the public repository for the same version as the package.
- FreeType: PDFium (pypdfium2), Pillow (`PIL\_imagingft`), and Qt (Qt6Gui) each include FreeType 2.14.3. The FreeType license ("2. Redistribution" of The FreeType Project License) requires, for distribution in binary form, that the documentation of the distribution state that it is based in part on the work of the FreeType Team. Therefore, the distribution README (`README-ja.txt` and `README-en.txt`) and this README contain the following sentence, which the license gives as an example. Following the license's instruction "Please replace `<year>` with the value from the FreeType version you actually use.", `<year>` is set to the years in the copyright notice of the FreeType 2.14.3 source ("Copyright (C) 1996-2026").
  - Portions of this software are copyright © 1996-2026 The FreeType Project (www.freetype.org). All rights reserved.
- The pypdfium2 distribution is not the one from PyPI; it was built by this project from the source distribution of pypdfium2 5.14.0 and the PDFium of chromium/8138 from pdfium-binaries (`tools/build_pypdfium2.py`; the built files are placed in `wheels/`, and their SHA-256 is written in `uv.lock`). This is because the PDFium (156.0.8076) in pypdfium2 5.14.0 on PyPI lacks 5 vulnerability fixes. The Python part is the same as 5.14.0 on PyPI; the differences are the PDFium component, its function definitions (`pypdfium2_raw\bindings.py`), and the license texts.
- The OCR models bundled with RapidOCR are converted from PaddleOCR (Baidu) models and are distributed under Apache-2.0 (`licenses\rapidocr\MODEL_LICENSES.md` in the package).
- Visual C++ runtime: The package contains DLLs of the Microsoft Visual C++ Runtime. All of them come from the distributions of components of the package (Python and the libraries), and the files, their locations, and which distribution each came from (and, for the files copied by `tools/make_dist.py`, where they were copied from) are listed in `licenses\index.txt` of the package. Microsoft's terms are in `licenses\Microsoft-Visual-C++-Runtime.txt` of the package (the English text and a Japanese translation; the English text prevails). The package is arranged so that the people who use it are deemed to have agreed to those terms (setup does not show a screen asking for consent).
