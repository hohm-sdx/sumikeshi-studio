Sumikeshi Studio (墨消しスタジオ) distribution package

A tool that creates PDFs in which the fields defined in forms are redacted, from scanned documents (PDF and image files).
Python and the required libraries are included in this folder, so you can use the tool without an Internet connection.
A Japanese version of this document is in README-ja.txt.

== Setup
1. If you downloaded the ZIP from the Internet (for example, from the Releases page of the public repository), unblock
   it as follows before extracting it. Windows marks a ZIP downloaded from the Internet as coming from the Internet. If
   you extract the ZIP with this mark, Smart App Control may block the setup (install.bat).
   (1) Right-click the downloaded ZIP and open "Properties".
   (2) On the "General" tab, select the "Unblock" check box near the bottom.
   (3) Click "OK".
   - The "Unblock" check box does not appear for a ZIP without the mark (for example, one copied from a file server or
     a USB drive). In that case, this step is not needed.
2. Extract all files from the ZIP. You can extract it anywhere, such as the Downloads folder.
   - The tool does not run directly from inside the ZIP. Be sure to extract it.
   - Use the extracted folder (sumikeshi-studio) without moving its contents or adding other files to it. Setup
     cannot be run if its contents are moved to the root of a drive or other files are added to it.
   - On Windows, extraction may fail if a path (the folder and file names joined together) exceeds 260 characters.
     The longest path in this ZIP is 136 characters. Keep the path of the destination folder to 121 characters or less.
3. Double-click install.bat in the extracted folder (sumikeshi-studio). It asks for the type of installation; type 1 or 2
   and press Enter (Enter alone chooses 1). If an earlier installation is found, it does not ask, and updates the tool
   with the same type (see "Updating to a new version").
   - 1 (Just for me): Administrator rights are not required. The extracted folder is moved to
     %LOCALAPPDATA%\Programs\SumikeshiStudio.
   - 2 (All users of this PC): Administrator rights are required. The files are copied to
     C:\Program Files\SumikeshiStudio. You can delete the extracted folder after the installation. Installation from a
     network folder cannot be done.
   After tens of seconds, "Sumikeshi Studio" appears on the Start menu and the desktop ("墨消しスタジオ" on Japanese
   Windows), and the tool is added to "Installed apps" in Windows. When "Press Enter to close." appears, press Enter.
   install.bat shows its messages in the language selected on the start screen of the tool (if none is selected, in the
   Windows display language).
4. If the forms folder has no forms, you are asked whether to add the sample National Tax Agency forms (扶養控除等申告書,
   保険料控除申告書, and 基・配・特・所の申告書). Type Y to add them or N not to add them, and then press Enter (Enter
   alone adds them). In an installation for all users, the tool asks each user the first time the user starts it.
If you used version 0.2.0 or earlier, you can delete the folder where you extracted that version after the
installation (install.bat shows the location of that folder at the end of its window). The forms, the work log, and the
settings are kept as they are.

== Starting the tool
Choose "Sumikeshi Studio" on the Start menu or the desktop.
If there is no shortcut, you can also start the tool by double-clicking start.bat in the installed folder (just for me:
%LOCALAPPDATA%\Programs\SumikeshiStudio; all users: C:\Program Files\SumikeshiStudio).
To open files, drag them onto the shortcut or the start screen. Do not drag files onto .bat files (with a file whose
name contains &, the part after it may be run as a separate command).
If the tool is already running, it does not start again; the window that is shown comes to the front.

== Display language
The tool is shown in Japanese on Japanese Windows and in English on other Windows.
Select "日本語" or "English" in "言語 / Language" at the right end of the top row of the start screen. The start screen
switches to that language at once, and the tool uses it the next time it starts. You cannot change the language while
the redaction screen or the forms screen is open.
Form names (such as 扶養控除等申告書) are shown in Japanese even on the English screens.

== The start screen and the forms folder
When you start the tool, the start screen appears first. Click "Open files" and select PDF or image files to open them
on the redaction screen (you can also open a whole folder with "Open folder", or drag PDF and image files or folders
onto the start screen). "Edit forms" opens the forms screen. Closing either screen returns you to the start screen.
Closing the start screen ends the tool.
The start screen shows the list of forms and, for each form, whether it is used for redaction on this PC (the
"Use" check box). Documents of forms that are unchecked are not redacted automatically on this PC; they are
treated as "Unknown form" and marked "⚠ Review" (a confirmation window appears when you uncheck a form). The more forms
you use, the longer the analysis takes.
Forms that users register or edit are saved in the forms folder. Select the forms folder with "Change" next to
"Folder:" on the start screen.
If you do not select one, the default location (%LOCALAPPDATA%\SumikeshiStudio\forms) is used.
The default location remains when you update to a new version.
If you select a shared folder, everyone uses the same forms.
If the forms folder cannot be read (for example, when you are not connected to the shared folder), the redaction screen
cannot be opened.
When you change the forms folder, if the new folder has no forms and the previous folder has forms, the tool asks
whether to copy the forms in the previous folder (only the forms are copied; the history folder and changes.csv are
not copied).
If the forms folder has no forms, the start screen tells you so. If you open files as they are, every page becomes
"Unknown form". You can add the sample forms with "Add sample forms" on the start screen.
In "Register new form" on the forms screen, select a PDF or image file of a document without entries, or copy an image
of the document and paste it (Ctrl+V), then draw the redaction areas and save.
When saving, the tool fills the redaction areas in the sample image with the paper color and keeps the frame lines
around the fields. Everything outside the redaction areas remains in the sample image as is. If you try to register
from a sheet with entries, a distorted image such as a photo, or a low-resolution image, the tool shows a warning and
asks whether to continue. Scan a sheet without entries with a scanner and register it. A form with little text and
few lines has few cues for alignment, and its pages may become "Unknown form" depending on the condition of the scan;
when you save such a form, the tool also shows a warning and asks whether to continue. A form without enough cues
(such as a form with lines only) cannot be saved. For forms registered from a photo or a sheet with entries, use
"Replace sample image" with a scan of a sheet without entries to replace the sample image. In the sample images of
forms saved with an earlier version, the frame lines along the areas were filled too, so when you draw or resize an
area while editing, its frame lines may not be found. To restore the frame lines, use "Replace sample image" with a
sheet without entries.
Forms whose sample images are similar (for example, the 2026 and 2027 versions of the same document) are told apart by
their titles. In "Identifying by title" on the area editor, enter one line with the year and the document name
separated by a space.
Pages that are not similar to the sample image of any form (such as deduction certificates) become "Unknown form" and
are marked "⚠ Review".
Redacted PDFs are exported with "_redacted" added to the end of the original name (example: form_redacted.pdf).

== When a form is revised every year
When the form of a document is revised every year, prepare a sheet of the new form (a PDF or image without entries)
and do the following.
1. Open the new sheet on the redaction screen, and check that the orange frames (the fields defined in the form) are on
   the fields of the sheet and that the page is not marked "⚠ Review". If so, the positions of the fields have not
   changed, and you do not need to do anything.
2. If you no longer handle sheets of the previous form, open the forms screen with "Edit forms" on the start screen,
   select the form, click "Replace sample image", and select the new sheet. The current areas are moved onto the new
   sheet; check their positions and save.
3. If you handle sheets of both the previous and the new form, register the new sheet with "Register new form". In
   "Identifying by title" on the area editor, enter words that tell the two forms apart (such as the year, for
   example: 令和9年分　扶養控除等), and enter the words of the previous year for the previous form as well. Without
   them, pages similar to both forms are marked "⚠ Review".
If the forms folder is a shared folder, updating the form on one PC makes all PCs use the new form.

== Variations of a form
Sheets whose fields are in the same order as the sample image of a form but whose row heights, column widths, or
typeface differ (variations of a form) cannot be identified by aligning them with the sample image. For such pages,
the tool finds the fields from the order of the ruled lines of the sample image, places the areas, and marks the page
"⚠ Review" with a message that the sheet is a variation of the form. Check that the areas match the fields, and
correct the areas if they are off.
The condition for identifying a variation (the minimum line match) can be changed for each form. On the forms screen,
select the form, click "Edit", move the slider under "Variations of this form" in the right column of the area
editor, and save. Raise it if other documents are identified as that form, and lower it if sheets of that form become
"Unknown form". If other documents are still identified after you raise it, uncheck "Use the order of ruled lines to
identify this form".
The "Line match below limit" column of the work log shows how far a page that became "Unknown form" was below the
minimum (for example, "4: 0.41/0.55 < 0.70 (nta-fuyou-r9 r1)" means that the line match of page 4 was 0.41 and 0.55,
below the minimum 0.70 of the form nta-fuyou-r9; when both values are at or above the minimum, the page is identified
as a variation of that form).
Only forms that have the ruled lines of their sample image are used to find such sheets. The ruled lines are read from
the sheet without entries and saved in the form when a form is registered with "Register new form" and when a form is
saved after "Replace sample image". The sample forms added by the installation or by "Add sample forms" have the
ruled lines from the start. To use
a form without the ruled lines (a form added or registered with an earlier version), select the form on the forms
screen, click "Replace sample image", select a sheet without entries (a PDF, or a scanned image of a sheet without
entries), and save. For the three sample forms, you can use page 1 of the National Tax Agency PDFs
listed in "Source of the forms". The current areas are moved onto the new sheet; check their positions before saving.
If the forms folder is a shared folder, doing this on one PC makes the form available on all PCs.

== Exporting from the command line (without the screen)
With sumikeshi-cli.bat in the Command Prompt or another console, you can analyze files and export redacted PDFs without
the screen.
Example (just-for-me installation): %LOCALAPPDATA%\Programs\SumikeshiStudio\sumikeshi-cli.bat C:\scan --out C:\out
In an installation for all users, use "C:\Program Files\SumikeshiStudio\sumikeshi-cli.bat" (enclose it in quotes because
the path contains a space).
- Specify one or more PDF or image files, or folders (the PDF and image files directly in them), as input.
- Specify an existing output folder with --out. If a file with the same name already exists in the output folder, that
  file is not exported (to overwrite it, add --overwrite).
- The forms used are the forms checked in "Use" in the forms folder selected on the start screen (you can
  change the folder with --forms). If the forms folder cannot be read, if any form in use cannot be read, or if there
  are no forms in use, no files are processed.
- Because nobody checks the pages on the screen, files with pages to check (⚠ Review, ⚠ Orientation, Unknown form,
  No redaction, ✕ Failed) are also exported, and those pages are shown in the console and written to the work log.
  Check those pages after the export. Pages that could not be analyzed (✕ Failed) are exported without redaction.
- The exit code (%ERRORLEVEL%) is 0 if all files were exported, 1 if some files were not exported, 2 if processing
  could not start, and 3 if you stopped it partway with Ctrl+C.
- For the list of options, run sumikeshi-cli.bat --help.
- When calling from another program or script, pass folders instead of individual file names, or call
  python\python.exe in the installed folder directly with -I -X utf8 -m sumikeshi_studio.cli.
  Example: %LOCALAPPDATA%\Programs\SumikeshiStudio\python\python.exe -I -X utf8 -m sumikeshi_studio.cli C:\scan --out C:\out
  Arguments passed to sumikeshi-cli.bat are read by the Command Prompt (cmd.exe), so if a file name containing & or a
  similar character is passed without quotes, the part after it may be run as a separate command. The same applies
  when files are dragged onto sumikeshi-cli.bat.

== Updating to a new version
The latest package is on the Releases page of the public repository
(https://github.com/hohm-sdx/sumikeshi-studio/releases).
1. Close the tool. In an installation for all users, ask the other users of this PC to close it too (if a user left it
   running while signed in, the update is stopped).
2. If you downloaded the new ZIP from the Internet, unblock it before extracting it (step 1 of "Setup").
3. Extract the new ZIP, and double-click install.bat in that folder. It does not ask for the type of installation, and
   updates the tool with the same type as before (when updating from version 0.2.0 or earlier, it asks for the type; see
   step 3 of "Setup"). The earlier version is replaced with the new version. The forms, the work log, and the settings
   are kept. To change the type of installation, uninstall the tool first (see "Uninstalling"), and then install it.

== Uninstalling
1. Close the tool.
2. In Windows "Settings", open "Apps" > "Installed apps" ("Apps & features" on Windows 10), select "Sumikeshi Studio"
   ("Sumikeshi Studio (all users)" for an installation for all users), and click "Uninstall". You can also uninstall
   by double-clicking uninstall.bat in the installed folder.
3. In the window that appears, type Y and press Enter to uninstall. The program, the shortcuts, and the entry in
   "Installed apps" are deleted. Uninstalling an installation for all users requires administrator rights.
4. You are asked whether to also delete the work log, the settings, and the forms in the default location of this
   account. Press Enter without typing anything to keep them. A forms folder selected on the start screen that is not
   the default location (such as a shared folder) is not deleted. However, if that folder is inside the folders of
   the work log and settings (%LOCALAPPDATA%\SumikeshiStudio, %APPDATA%\SumikeshiStudio), it is deleted with them.
The work logs, settings, and forms of other users are not deleted. If they are not needed, an administrator deletes
C:\Users\(user name)\AppData\Local\SumikeshiStudio and C:\Users\(user name)\AppData\Roaming\SumikeshiStudio of each user.

== Using the tool with several users
The forms, the work log, and the settings are kept for each user, whichever type of installation is used. To use the
same forms with several users, prepare a folder that everyone can read and write (for example, C:\tools\forms or a
shared folder), and have each user select it with "Change" next to "Folder" on the start screen.

== When Windows blocks the tool
If a message saying that Smart App Control blocked a file that may be unsafe appears when you double-click install.bat or
another file, the ZIP may have been extracted without being unblocked. Do the following.
1. Delete the extracted folder.
2. Unblock the ZIP (step 1 of "Setup").
3. Extract the ZIP again, and then double-click install.bat.
In addition, Windows Smart App Control (or your organization's application control) may block components of the tool.
When they are blocked, install.bat or the start screen of the tool shows a message such as
"Windows Smart App Control (or your organization's application control) blocked ...".
Taking a screenshot of that window may help investigate the cause. If the message appears on the start screen of the
tool, extract the ZIP of the package and run install.bat to see the names of the blocked components.
If the message says "... These components are not used for analysis or export, so the tool is expected to work
normally.", you can keep using the tool.

== Work log
A record for each exported file is in %LOCALAPPDATA%\SumikeshiStudio\worklog.csv.

== When the window suddenly disappears
If the window of the tool suddenly disappears, the following files keep a record. They may help investigate the cause.
- %LOCALAPPDATA%\SumikeshiStudio\crash.log
- crash_previous.log in the same folder, if it exists
Each time the tool starts, it writes the date and time, the version, where the processing was, and error messages (in
English). Images before redaction are not included, but the locations (paths) of the files you opened may be included
(the same as the work log).
If the window suddenly disappeared last time, the tool also tells you the location of this file the next time it starts.

== Events recorded in Windows diagnostic data
The component used to read text (onnxruntime) records usage events on Windows. Sumikeshi Studio stops this recording
right after loading the component and each time it reads text. Before it is stopped, however, the following three pieces
of information are recorded:
- The version of the component
- The CPU name, the number of logical processors, and the total memory
- Whether a debugger is attached, the name of the processing component, and whether its setup succeeded
These events do not contain PDFs or images, the text read from them, file names, folder locations, or the Windows user
name (checked by recording the events on a test PC with Sumikeshi Studio 0.4.0rc2, which uses onnxruntime 1.31.0).
These events may be sent to Microsoft as Windows diagnostic data. Whether they are sent depends on "Settings" >
"Privacy & security" > "Diagnostics & feedback" in Windows and on Microsoft's settings. Sumikeshi Studio itself does not
send them.

== Source of the forms
The sample images of the three sample forms included in this package (扶養控除等申告書, 保険料控除申告書, and
基・配・特・所の申告書) were created by Hideki Ohmura by processing the following forms on the National Tax Agency
website. They were not created by the National Tax Agency. These sample images are not covered by the AGPL-3.0; they
can be used under the terms of use of the National Tax Agency website (Public Data License (Version 1.0);
https://www.nta.go.jp/chuijiko/copy.htm).
Source: National Tax Agency website
https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2027bun_01.pdf
https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_04.pdf
https://www.nta.go.jp/taxes/tetsuzuki/shinsei/annai/gensen/pdf/2026bun_06.pdf

== Contents of this folder
python\            Python 3.14, the tool itself (Lib\site-packages\sumikeshi_studio), and the libraries
install.bat        Installs and updates the tool: puts it in the fixed location, and adds it to "Installed apps"
                   and the shortcuts (see "Setup" and "Updating to a new version")
uninstall.bat      Uninstalls the tool (see "Uninstalling")
start.bat          Starts the tool
sumikeshi-cli.bat  Exports from the command line without the screen (see "Exporting from the command line")
scripts\           PowerShell scripts that install.bat and the other .bat files run (do not run them directly)
README-ja.txt      The Japanese version of this document
README-en.txt      This document
version.txt        Records of this package, such as the version, build date, public source location (Source),
                   and the versions of Python and PDFium
licenses\          The full texts of the licenses, and the list of the licenses of the components (index.txt)
The list of Python and the libraries, with their versions, licenses, and the locations of the full texts, is in
licenses\index.txt.
Copyright (C) 2026 Hideki Ohmura
Sumikeshi Studio itself is distributed under the GNU Affero General Public License 3.0 only (AGPL-3.0-only), with an
additional permission under section 7 of the AGPL 3.0. The full text and the additional permission are in
licenses\sumikeshi-studio\.
This tool comes with no warranty. To the extent permitted by applicable law, the author provides no warranty for this
tool and is not liable for any damages arising from the use of this tool (sections 15 and 16 of the AGPL 3.0).
The source code of the tool for this package is in the public repository
(https://github.com/hohm-sdx/sumikeshi-studio), at the tag of the same version as this package ("v" followed by the
version; see the Source line of version.txt).
The tool uses Qt for Python (PySide6 and shiboken6 6.12.0) for its user interface under the GNU Lesser General Public
License 3.0 (LGPL 3.0). The LGPL 3.0 adds permissions to the GNU General Public License 3.0 (GPL 3.0) and requires that
the full text of the GPL 3.0 is also provided. The full text of the LGPL 3.0 is in licenses\LGPL-3.0.txt, and the full
text of the GPL 3.0 is in licenses\GPL-3.0.txt.
GEOS 3.14.1, included in shapely (a library used by the text recognition component), is used under the GNU Lesser
General Public License 2.1 (its license text is in the shapely entry under licenses\).
The source code of Qt (PySide6, shiboken6, and qtbase of Qt) and GEOS is in the item for the same version as this
package on the Releases page of the public repository (https://github.com/hohm-sdx/sumikeshi-studio/releases).
This package contains the DLLs of the Microsoft Visual C++ Runtime. Their terms are in
licenses\Microsoft-Visual-C++-Runtime.txt. By using this package, you agree to those terms.
Portions of this software are based in part on the work of the FreeType Team (FreeType 2.14.3, included in PDFium, the
component that renders PDFs as images, in Pillow, the library that handles images, and in Qt, the user interface
component).
Portions of this software are copyright © 1996-2026 The FreeType Project (www.freetype.org). All rights reserved.
