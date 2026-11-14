@echo off
rem Install the patched driver set onto the system volume, naming the source by
rem where this file actually lives rather than by a drive letter.
rem
rem %~dp0 is this script's own drive and directory, with the trailing slash. The
rem letter this volume gets is not ours to assume: on 2026-11-14 the guest gave
rem D: to the CSMWrap partition of the system disk and the driver volume landed
rem on E:, so every D:\ source below pointed at the wrong volume and the install
rem died as a modal dialog nobody was watching. The system drive is spelled out
rem because this rig installs onto C: by definition; no other letter appears
rem here, and tests/test_install_channel.py is what keeps it that way.
copy /y %~dp0ati2mtag.sys C:\WINDOWS\system32\drivers\ati2mtag.sys
copy /y %~dp0ati2erec.dll C:\WINDOWS\system32\drivers\ati2erec.dll
copy /y %~dp0ati2dvag.dll C:\WINDOWS\system32\ati2dvag.dll
copy /y %~dp0ati2cqag.dll C:\WINDOWS\system32\ati2cqag.dll
copy /y %~dp0ati2mdxx.exe C:\WINDOWS\system32\Ati2mdxx.exe
copy /y %~dp0ati3duag.dll C:\WINDOWS\system32\ati3duag.dll
copy /y %~dp0ativvaxx.dll C:\WINDOWS\system32\ativvaxx.dll
copy /y %~dp0atiicdxx.dat C:\WINDOWS\system32\atiicdxx.dat
copy /y %~dp0atiadlxx.dll C:\WINDOWS\system32\atiadlxx.dll
copy /y %~dp0ativvaxx.cap C:\WINDOWS\system32\ativvaxx.cap
copy /y %~dp0atimpc64.dll C:\WINDOWS\system32\atimpc64.dll
copy /y %~dp0atiapfxx.exe C:\WINDOWS\system32\atiapfxx.exe
copy /y %~dp0atiapfxx.blb C:\WINDOWS\system32\atiapfxx.blb
copy /y %~dp0ati2evxx.dll C:\WINDOWS\system32\ati2evxx.dll
copy /y %~dp0ati2evxx.exe C:\WINDOWS\system32\ati2evxx.exe
copy /y %~dp0atipdl64.dll C:\WINDOWS\system32\atipdl64.dll
copy /y %~dp0ati2edxx.dll C:\WINDOWS\system32\ati2edxx.dll
copy /y %~dp0atikvmag.dll C:\WINDOWS\system32\atikvmag.dll
copy /y %~dp0atidemgx.dll C:\WINDOWS\system32\ATIDEMGX.dll
copy /y %~dp0atibtmon.exe C:\WINDOWS\system32\atibtmon.exe
copy /y %~dp0atio6axx.dll C:\WINDOWS\system32\atio6axx.dll
copy /y %~dp0atiokax2.dll C:\WINDOWS\system32\atiokax2.dll
copy /y %~dp0atiadlxy.dll C:\WINDOWS\SysWOW64\atiadlxy.dll
copy /y %~dp0atioglxx.dll C:\WINDOWS\SysWOW64\atioglxx.dll
copy /y %~dp0atimpc32.dll C:\WINDOWS\SysWOW64\atimpc32.dll
copy /y %~dp0atipdlxx.dll C:\WINDOWS\SysWOW64\atipdlxx.dll
copy /y %~dp0atiiiexx.dll C:\WINDOWS\system32\atiiiexx.dll
copy /y %~dp0atiogl.xml C:\WINDOWS\atiogl.xml
copy /y %~dp0atiodcli.exe C:\WINDOWS\system32\ATIODCLI.exe
copy /y %~dp0atiode.exe C:\WINDOWS\system32\ATIODE.exe
regedit /s %~dp0install.reg
shutdown -r -f -t 5
