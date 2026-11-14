@echo off
rem The October phantom-head probe, kept so a layered run can be compared against
rem it. Sources and outputs are named from this file's own directory for the same
rem reason drv.bat is: the volume's letter is decided at boot and is not ours.
set OUT=%~dp0..
echo === probe %date% %time% > %OUT%\o-phantom.txt
ping -n 46 127.0.0.1 >nul
echo after-45s >> %OUT%\o-phantom.txt
%~dp0cap.exe %OUT%\o-screen.bmp > %OUT%\o-cap.txt 2>&1
echo --- CAPTURE >> %OUT%\o-phantom.txt
type %OUT%\o-cap.txt >> %OUT%\o-phantom.txt
echo --- VIDEO >> %OUT%\o-phantom.txt
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Video" /s > %OUT%\o-video.txt 2>&1
type %OUT%\o-video.txt >> %OUT%\o-phantom.txt
echo --- CLASSKEY >> %OUT%\o-phantom.txt
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4D36E968-E325-11CE-BFC1-08002BE10318}\0000" /s > %OUT%\o-class.txt 2>&1
type %OUT%\o-class.txt >> %OUT%\o-phantom.txt
echo --- HWPROFILES >> %OUT%\o-phantom.txt
reg query "HKLM\SYSTEM\CurrentControlSet\Hardware Profiles" /s > %OUT%\o-hw.txt 2>&1
type %OUT%\o-hw.txt >> %OUT%\o-phantom.txt
echo probe complete >> %OUT%\o-phantom.txt
