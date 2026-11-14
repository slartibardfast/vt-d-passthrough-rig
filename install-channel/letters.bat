@echo off
rem The letter witness: is the driver volume on the letter the rig's scripts name,
rem and which letters does this boot have at all. The host reads these files after
rem the boot, so "D: is the driver volume" becomes a recorded fact of the run
rem rather than a reading of pixels.
rem
rem fsutil emits UTF-16 when redirected, so the census gets its own file and the
rem survey gets another: mixing them produced a file that decoded as neither.
rem
rem The volume may be named to this script as its first argument. The first version
rem took it only from %~d0, and when it was launched through a path the parser had
rem mangled, %VOL% came out empty, the redirect failed, and no witness appeared at
rem all - silence again, which is the one outcome this file exists to remove. So
rem the failure is written where the host always looks.
rem
rem Only two volumes are opened: the system volume and this one. Five designs were
rem tried against the KVM's empty removable and all of them lost the witness on
rem it - `dir` of every letter, `vol` of every letter, an `if exist` guard (which
rem opens the volume too), a `wmic logicaldisk` (WMI is still starting at logon),
rem and `vol` again after suppressing the drive-with-no-media dialog in the
rem registry, which did not stop the block. The census of letters comes from
rem `fsutil fsinfo drives`, which is metadata and returns at once; opening media
rem is what stalls, so nothing here opens a volume it does not need.
set VOL=%~1
if "%VOL%"=="" set VOL=%~d0
if "%VOL%"=="" goto nowrite
echo letters-start %date% %time% > %VOL%\letters.txt
if not exist %VOL%\letters.txt goto nowrite
fsutil fsinfo drives > %VOL%\drives.txt 2>&1
echo surveyed-volumes c: and %VOL% >> %VOL%\letters.txt
call :survey c
call :survey %VOL:~0,1%
echo letters-done >> %VOL%\letters.txt
goto :eof

:survey
echo == %1: == >> %VOL%\letters.txt
vol %1: >> %VOL%\letters.txt 2>&1
if exist %1:\drv\ati2mtag.sys echo driver-marker drv\ati2mtag.sys found on %1: >> %VOL%\letters.txt
if exist %1:\B175198\CA178340.inf echo driver-marker B175198\CA178340.inf found on %1: >> %VOL%\letters.txt
goto :eof

:nowrite
echo letters-failed cannot write a witness to the driver volume > C:\letters-hook.txt
goto :eof
