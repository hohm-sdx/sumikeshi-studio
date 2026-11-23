@echo off
rem Copyright (C) 2026 Hideki Ohmura
rem SPDX-License-Identifier: AGPL-3.0-only
rem Start the tool without a console window
start "" "%~dp0python\pythonw.exe" -I -m sumikeshi_studio %*
