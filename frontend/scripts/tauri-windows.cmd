@echo off
call "%NOTESLYT_VSDEVCMD%" -arch=x64 -host_arch=x64
if errorlevel 1 exit /b %errorlevel%
set "PATH=%VCToolsInstallDir%bin\HostX64\x64;%PATH%"
call npm.cmd run tauri -- %1
exit /b %errorlevel%
