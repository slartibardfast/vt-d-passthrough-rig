# Type an arbitrary string into the guest via QEMU's monitor sendkey.
import socket, sys, time
NAMED = {' ': 'spc', ':': 'shift-semicolon', '\\': 'backslash', '.': 'dot',
         '/': 'slash', '-': 'minus', '=': 'equal', ',': 'comma', '.': 'dot',
         ';': 'semicolon', "'": 'apostrophe', '[': 'bracket_left',
         ']': 'bracket_right', '`': 'grave_accent', '%': 'shift-5', '"': 'shift-apostrophe', '!': 'shift-1', '_': 'shift-minus', '~': 'shift-grave_accent', '+': 'shift-equal', '#': 'shift-3', '$': 'shift-4', '<': 'shift-comma',
         '>': 'shift-period', '?': 'shift-slash', '|': 'shift-backslash',
         '^': 'shift-6', '&': 'shift-7', '*': 'shift-8', '(': 'shift-9', ')': 'shift-0'}
def keys_for(ch):
    if ch in NAMED: return [NAMED[ch]]
    if ch.isdigit(): return [ch]
    if ch.isalpha():
        return [ch] if ch.islower() else [f'shift-{ch.lower()}']
    return None
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/home/dconnolly/xp64-rig/mon.sock'); s.settimeout(5)
for ch in sys.argv[1]:
    ks = keys_for(ch)
    if ks is None:
        raise SystemExit(f"  FATAL: no key mapping for {ch!r}; refusing to type a partial command")
    for k in ks:
        s.sendall(f'sendkey {k}\n'.encode()); time.sleep(0.14)
print(f"  typed {len(sys.argv[1])} chars")
