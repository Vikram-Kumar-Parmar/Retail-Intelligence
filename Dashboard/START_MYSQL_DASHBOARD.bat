@echo off
setlocal
cd /d "%~dp0"
set /p DWH_HOST=MySQL host [127.0.0.1]: 
if "%DWH_HOST%"=="" set "DWH_HOST=127.0.0.1"
set /p DWH_PORT=MySQL port [3306]: 
if "%DWH_PORT%"=="" set "DWH_PORT=3306"
set /p DWH_DB=Database [retail_dw]: 
if "%DWH_DB%"=="" set "DWH_DB=retail_dw"
set /p DWH_USER=MySQL user [root]: 
if "%DWH_USER%"=="" set "DWH_USER=root"
set /p DWH_PASSWORD=MySQL password (visible while typing): 
call "%~dp0START_DASHBOARD.bat"
