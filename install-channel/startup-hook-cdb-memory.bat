@echo off
rem Boot-time trigger for the run's in-guest captures, installed into the All Users Startup
rem folder so it runs the moment a session exists.
rem
rem It names no volume letter, and that is deliberate. The first version checked for the witness
rem on one assumed letter, so a drifted letter made the evidence simply never appear - the same
rem silent failure as a hook that never ran. Instead it finds the driver volume by what the volume
rem holds, the rule verify-letters.py uses too, and records where it found it.
rem
rem Four of these facts exist only while the guest is running. HARDWARE\DEVICEMAP\VIDEO is
rem volatile and built during boot. The devnode's ConfigManagerErrorCode lives in a subkey the PnP
rem manager creates with REG_OPTION_VOLATILE, so it is never written to the hive file and no
rem offline read of any overlay can produce it. The session's mode and the service's state are
rem likewise live. So the guest writes them and the host only ever collects them.
rem
rem Every capture carries the run's nonce, read from %SystemRoot%\runid.txt, which the runner puts
rem in the overlay it boots. The grader refuses a capture whose nonce is not the run it claims.
rem Without that, a file inherited from the golden or left by an earlier boot grades as evidence of
rem this run, and that is exactly the failure that cost a control run its two captures: the golden
rem carried an older copy of this very file.
set HOOK=C:\letters-hook.txt
set CAP=%SystemRoot%\capture.txt
set RUNID=no-runid-file
set /p RUNID=<%SystemRoot%\runid.txt
echo letters-hook-start %date% %time% > %HOOK%
echo capture-start %RUNID% %date% %time% > %CAP%
rem The volatile video map: which per-adapter key the running session calls \Device\Video0.
reg query "HKLM\HARDWARE\DEVICEMAP\VIDEO" /s > %SystemRoot%\devicemap.txt 2>&1
echo devicemap rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem A cold WMI repository makes the first query fail or hang, so start the service first and keep
rem its answer. Each rc line is what lets the grader tell "the hook never ran" from "the hook ran
rem and the query failed", and that distinction is the difference between blaming the staging and
rem blaming the device.
net start winmgmt >> %CAP% 2>&1
wmic path Win32_PnPEntity where "PNPDeviceID like '%%VEN_1002&DEV_67B0%%'" get Name,PNPDeviceID,Status,ConfigManagerErrorCode /format:textvaluelist > %SystemRoot%\devnode.txt 2>&1
echo pnpentity rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem Win32_VideoController carries the same error code plus the mode and the VRAM the run-driver.sh
rem header names, so it discriminates a 640x480x8 VGA session from a native ATI mode.
wmic path Win32_VideoController get Name,VideoProcessor,AdapterRAM,InstalledDisplayDrivers,DriverVersion,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentBitsPerPixel,VideoModeDescription,ConfigManagerErrorCode,Status /format:textvaluelist > %SystemRoot%\videoctl.txt 2>&1
echo videocontroller rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem The live Enum tree shows the volatile Control subkey and its ActiveService, which exists only
rem when PnP actually started the devnode. It needs no WMI, so it survives a broken repository.
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\PCI" /s > %SystemRoot%\enum-pci.txt 2>&1
echo enum-pci rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
sc query ati2mtag > %SystemRoot%\svc-ati2mtag.txt 2>&1
echo service-query rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
set FOUND=
for %%d in (c d e f g h i) do @if exist %%d:\drv\ati2mtag.sys @(set FOUND=%%d& echo found-driver-volume-on %%d >> %HOOK% & start "" /min %%d:\letters.bat)
if defined FOUND goto end
echo letters-hook-failed no volume held drv\ati2mtag.sys >> %HOOK%
:end
rem The user-mode display files actually present, with sizes. A display driver that loads and
rem then declines is often missing part of its own package, and only the DLL the host installs
rem was ever visible from outside the guest.
dir %SystemRoot%\system32\ati*.* > %SystemRoot%\ati-files.txt 2>&1
echo dir-ati rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem The monitor devices and their EDIDs. The modes a driver may offer are bounded by what the
rem monitor advertises, and a Default_Monitor carrying a bad EDID advertises nothing.
reg query "HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY" /s > %SystemRoot%\enum-display.txt 2>&1
echo enum-display rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem The mode the session is asked for, in the hardware profile, which is where that request
rem lives rather than in the per-adapter key.
reg query "HKLM\SYSTEM\CurrentControlSet\Hardware Profiles\0001\System\CurrentControlSet\Control\VIDEO" /s > %SystemRoot%\profile-video.txt 2>&1
echo profile-video rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
rem The system event log, copied while the session that wrote it is still up: a display driver
rem that fails to enable records why here, and no run of this campaign has ever captured it.
copy /y %SystemRoot%\system32\config\SysEvent.Evt %SystemRoot%\SysEvent.Evt > nul 2>&1
echo eventlog rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%

rem Analyse the kernel dump with the guest's own debugger. The debugger and the dump are placed at
rem C:\DBG by the run's settings step; cdb needs no symbols to report the bugcheck, the thread
rem contexts and the stacks, which is what the register question needs.
rem Prefer the copy smss makes at boot from the pagefile dump: the pagefile layout is not the
rem dump-file layout, which is why a carve of it reads as corrupt however far it is cut.
set DUMPFILE=
if exist %SystemRoot%\MEMORY.DMP set DUMPFILE=%SystemRoot%\MEMORY.DMP
if not defined DUMPFILE if exist %SystemDrive%\DBG\MEMORY.DMP set DUMPFILE=%SystemDrive%\DBG\MEMORY.DMP
if defined DUMPFILE if exist %SystemDrive%\DBG\cdb.exe (
  cd /d %SystemDrive%\DBG
  cdb.exe -z "%DUMPFILE%" -c "!analyze -v; ~*kb; q" > %SystemDrive%\cdb-out.txt 2>&1
  echo cdb rc=%ERRORLEVEL% nonce=%RUNID% >> %CAP%
)
echo capture-end %RUNID% %date% %time% >> %CAP%
echo letters-hook-end %date% %time% >> %HOOK%
