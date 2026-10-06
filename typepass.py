# Type a string into the guest through the QEMU monitor, one key at a time.
import socket, subprocess, sys
pw = subprocess.run(['grep','-iE','^AdminPassword=','/home/dconnolly/xp64-rig/winnt.sif'],
                    capture_output=True, text=True).stdout.split('=',1)[1].strip().replace('\r','')
if len(sys.argv) > 1:
    pw = sys.argv[1]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/home/dconnolly/xp64-rig/mon.sock'); s.settimeout(4)
import time
for ch in pw:
    name = 'spc' if ch == ' ' else ch
    s.sendall(f'sendkey {name}\n'.encode()); time.sleep(0.25)
print(f"  typed {len(pw)} characters into the guest")
