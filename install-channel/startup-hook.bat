@echo off
rem Boot-time trigger for the letter witness, installed into the All Users
rem Startup folder so it runs the moment a session exists.
rem
rem It names no volume letter, and that is deliberate. The first version checked
rem for the witness on one assumed letter, so a drifted letter made the evidence
rem simply never appear - the same silent failure as a hook that never ran.
rem Instead it finds the driver volume by what the volume holds, the rule
rem verify-letters.py uses too, and records where it found it. The witness
rem surveys every letter anyway, so the question "is it the letter the scripts
rem assume" is answered by the evidence, not by this file.
set HOOK=C:\letters-hook.txt
set FOUND=
echo letters-hook-start %date% %time% > %HOOK%
rem The video device map is a volatile key: it is built during boot and exists on no
rem disk, so no offline read of an overlay can produce it later. The guest has to write it
rem while it is running. It goes on the system volume, because that is the volume inside
rem the run's overlay that the host reads back after the guest ends the run. It names no
rem volume letter for the same reason the rest of this file names none.
reg query "HKLM\HARDWARE\DEVICEMAP\VIDEO" /s > %SystemRoot%\devicemap.txt 2>&1
rem The devnode's answer to "did PnP load it" is no one's file: measured on both banked SYSTEM
rem hives, the card's instance key carries no ConfigManagerErrorCode, neither before a real boot
rem nor after one, because SetupAPI owns that number and keeps it in the running device node. So
rem the guest asks it while it is running. The percent signs are doubled: cmd would otherwise
rem expand what sits between them as a variable and the query would come back empty.
wmic path Win32_PnPEntity where "PNPDeviceID like '%%VEN_1002&DEV_67B0%%'" get Name,Status,ConfigManagerErrorCode /format:list > %SystemRoot%\devnode.txt 2>&1
for %%d in (c d e f g h i) do @if exist %%d:\drv\ati2mtag.sys @(set FOUND=%%d& echo found-driver-volume-on %%d >> %HOOK% & start "" /min %%d:\letters.bat)
if defined FOUND goto end
echo letters-hook-failed no volume held drv\ati2mtag.sys >> %HOOK%
:end
echo letters-hook-end %date% %time% >> %HOOK%
