#!/usr/bin/env bash
# Build the signed catalog for the patched INF and stage it onto the guest's driver volume.
#
# The volume is a plain FAT32 image so it is the channel in both directions: this script
# writes the package and the batch file before the boot, and the guest writes its verdict
# to B:\result.txt for the host to read after. Nothing long is typed through sendkey, which
# plan/PLAN.md records as the thing that fails silently.
#
# It refuses to run unless the catalog it is about to write actually verifies, because a
# catalog that is subtly malformed produces a driver-signing failure in the guest that is
# indistinguishable from every other signing failure.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
PKG=${PKG:-/home/dconnolly/xp64-corpus/extracted-inf/icafe-100.50-xp64-9.00.300.3005/Packages/Drivers/Display/XP6A_INF}
WORK=/tmp/opencode/driver-stage
MNT=/mnt/gpudrv
OFFSET=1048576

rm -rf "$WORK"; mkdir -p "$WORK"

# The INF the guest will actually use is the one on the image, so hash that, not a copy.
if mountpoint -q "$MNT"; then sudo umount "$MNT"; fi
sudo mkdir -p "$MNT"
sudo mount -o loop,offset=$OFFSET "$RIG/gpudrv.img" "$MNT"
sudo cp "$MNT/B175198/CA178340.inf" "$WORK/CA178340.inf"
sudo cp "$WORK/CA178340.inf" "$WORK/CA178340.inf.seen" 2>/dev/null || true
sudo chmod a+r "$WORK/CA178340.inf"
sudo umount "$MNT"

echo "  patched INF taken from the image: $(sha1sum "$WORK/CA178340.inf" | cut -c1-40)"

python3 "$RIG/catalog-sign.py" --package "$PKG" --inf "$WORK/CA178340.inf" --out "$WORK"

# This guest has no certutil.exe - it is on neither the XP x64 media nor the installed
# system32 - so the signer cannot be trusted with `certutil -addstore`. The offline route
# is the registry one: CryptSvc validates against the machine store under
# HKLM\SOFTWARE\Microsoft\SystemCertificates, so an imported .reg entry is equivalent to
# the GUI import and needs no MMC navigation.
python3 - "$WORK/CA178340-290x.cer" "$WORK/hawaii-trust.reg" <<'PY'
import hashlib, sys
der = open(sys.argv[1], "rb").read()
thumb = hashlib.sha1(der).hexdigest().upper()
body = ",".join(f"{b:02X}" for b in der)
# Root alone is not enough on XP: a driver node whose signer is absent from Trusted
# Publishers is demoted in ranking (#I087 "Driver node not trusted") even when the chain
# builds. Both stores get the same self-signed certificate.
blocks = []
for store in ("Root", "TrustedPublisher"):
    key = (r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\SystemCertificates"
           rf"\{store}\Certificates\{thumb}")
    blocks.append(f"[{key}]\r\n"
                  f'"EncodedCertificate"=hex:{body}\r\n'
                  '"Properties"=hex:00\r\n')
reg = "Windows Registry Editor Version 5.00\r\n\r\n" + "\r\n".join(blocks)
open(sys.argv[2], "w", newline="").write(reg)
print(f"  trust entries for thumbprint {thumb} into Root and TrustedPublisher "
      f"({len(der)} bytes of DER)")
PY

# Stage the batch file. %~d0 is this script's own drive, because the letter the volume gets
# is not ours to assume - the first attempt hard-coded B: and the guest had D:.
cat > "$WORK/install.bat" <<'BAT'
@echo off
set VOL=%~d0
echo === trusting the package signer (%VOL%) ===> %VOL%\result.txt
regedit /s %VOL%\hawaii-root.reg
if errorlevel 1 echo regedit FAILED >> %VOL%\result.txt
echo. >> %VOL%\result.txt
echo === pnputil capabilities === >> %VOL%\result.txt
pnputil /? >> %VOL%\result.txt 2>&1
echo. >> %VOL%\result.txt
echo === pnputil -a (stage the package into the driver store) === >> %VOL%\result.txt
pnputil -a %VOL%\B175198\CA178340.inf >> %VOL%\result.txt 2>&1
echo. >> %VOL%\result.txt
echo === InstallHinfSection 132 (run DDInstall on matching devices) === >> %VOL%\result.txt
rundll32 setupapi.dll,InstallHinfSection,132,%VOL%\B175198\CA178340.inf >> %VOL%\result.txt 2>&1
echo. >> %VOL%\result.txt
echo === video controllers === >> %VOL%\result.txt
wmic path win32_videocontroller get name,driverversion,adapterram,status,configmanagererrorcode >> %VOL%\result.txt 2>&1
echo. >> %VOL%\result.txt
echo === the driver store === >> %VOL%\result.txt
dir C:\WINDOWS\System32\DriverStore\FileRepository >> %VOL%\result.txt 2>&1
echo === install run finished >> %VOL%\result.txt
BAT
python3 - "$WORK/install.bat" <<'PY'
import sys
p = sys.argv[1]
d = open(p, "rb").read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
open(p, "wb").write(d)
PY

sudo mkdir -p "$MNT"
sudo mount -o loop,offset=$OFFSET "$RIG/gpudrv.img" "$MNT"
sudo cp "$WORK/CA178340.cat" "$MNT/B175198/CA178340.cat"
sudo cp "$WORK/CA178340-290x.cer" "$MNT/B175198/CA178340-290x.cer"
sudo cp "$WORK/hawaii-trust.reg" "$MNT/hawaii-trust.reg"
sudo cp "$WORK/install.bat" "$MNT/install.bat"
sudo rm -f "$MNT/result.txt"
sudo umount "$MNT"

echo
echo "  staged onto the volume:"
sudo python3 - <<'PY'
import subprocess
subprocess.run("sudo mount -o loop,offset=1048576 /home/dconnolly/xp64-rig/gpudrv.img /mnt/gpudrv", shell=True)
print(subprocess.run(
    "ls -l /mnt/gpudrv/B175198/CA178340.cat /mnt/gpudrv/B175198/CA178340-290x.cer "
    "/mnt/gpudrv/hawaii-root.reg /mnt/gpudrv/install.bat /mnt/gpudrv/B175198/CA178340.inf",
    shell=True, capture_output=True, text=True).stdout)
subprocess.run("sudo umount /mnt/gpudrv", shell=True)
PY