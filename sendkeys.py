import socket, sys, time
SOCK='/home/dconnolly/xp64-rig/mon.sock'
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(SOCK); s.settimeout(4)
for k in sys.argv[1:]:
    s.sendall(f'sendkey {k}\n'.encode()); time.sleep(0.6)
print("  sent:", " ".join(sys.argv[1:]))
