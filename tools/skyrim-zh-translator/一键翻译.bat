@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 天际整合包一键汉化

set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python --version >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo.
  echo  [错误] 没有检测到 Python 3。
  echo  请先安装 Python 3.9+： https://www.python.org/downloads/
  echo  安装时务必勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

%PYEXE% run.py %*
echo.
pause
