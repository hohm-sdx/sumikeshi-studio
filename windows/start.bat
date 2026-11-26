@echo off
rem Copyright (C) 2026 Hideki Ohmura
rem SPDX-License-Identifier: AGPL-3.0-only
rem Start the tool without a console window. Files to open are dragged onto the shortcut or the start screen, so no
rem arguments are passed. When a file is dragged onto this batch file, cmd reads the dropped name, and a name with an
rem ampersand would run the rest of the name as another command after this file ends. The exit (without /b) ends that
rem cmd here, so the rest does not run.
start "" "%~dp0python\pythonw.exe" -I -m sumikeshi_studio
exit
