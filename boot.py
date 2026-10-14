#!/usr/bin/env python3
"""boot.py - the clean-boot orchestrator.

The proven recipe, automated: kill the old session, relaunch, watch the
first-load storm (self-heals in ~14 retries on a healthy boot; a reset
escape if it does not), wait for the session era, report readiness.

Usage: boot.py [--hold]     # --hold leaves hold-all armed for the walk
"""
import os, subprocess, sys, time

RIG = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(RIG, "kdc-live.log")


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def log_lines(pattern):
    try:
        with open(LOG, errors="replace") as f:
            return [l for l in f if pattern in l]
    except OSError:
        return []


def main():
    hold = "--hold" in sys.argv
    # the clean slate
    sh("pkill", "-xf", "python3 kdc.py")
    time.sleep(1)
    sh("pkill", "-x", "qemu-system-x86")
    # the VFIO group teardown lags the process exit - wait until it frees,
    # or the new QEMU fails with "Device or resource busy" (measured)
    for _ in range(30):
        r = sh("sudo", "fuser", "-v", "/dev/vfio/17")
        if not (r.stdout.strip() or r.stderr.strip()):
            break
        time.sleep(1)
    time.sleep(1)
    for f in ("kdc-live.log", "hold-release", "send-reset"):
        p = os.path.join(RIG, f)
        if os.path.exists(p):
            os.remove(p)
    if hold:
        open(os.path.join(RIG, "hold-all"), "w").close()

    subprocess.Popen("nohup python3 kdc.py >> /tmp/opencode/kdc-dbg.log 2>&1 &",
                     shell=True, cwd=RIG)
    subprocess.Popen("nohup ./run-driver.sh > /tmp/opencode/run-driver.log 2>&1 &",
                     shell=True, cwd=RIG)
    print("boot launched; watching...")

    t0 = time.time()
    storm_resets = 0
    session_ready = False
    boot_bp_released = False
    while time.time() - t0 < 420:
        time.sleep(10)
        loads = [l for l in log_lines("ntoskrnl")]
        modules = [l for l in log_lines("MODULE LOAD")]
        holds = [l for l in log_lines("HOLDING")]
        names = []
        for l in modules:
            parts = l.split("name=")
            if len(parts) > 1:
                nm = parts[1].strip()
                if nm not in names:
                    names.append(nm)
        n_loads = len(loads)
        print(f"  t+{time.time()-t0:3.0f}s: storm={n_loads} unique_modules={len(names)} holds={len(holds)}")
        # The boot era: ANY hold before the session era is nt's boot
        # breakpoint (reported twice by kdcom - measured) and must be
        # released promptly; a long hold poisons the storage init
        # (measured: the crcdisk hang). After the session era, holds
        # stick (--hold: the walk waits there).
        if holds and len(names) < 40:
            # a hold in the boot era is an int3: resuming without RIP+1
            # re-executes it forever (measured). Step over instead.
            with open(os.path.join(RIG, "cmd"), "w") as f:
                f.write("step\n")
            boot_bp_released = True
            print("  boot breakpoint stepped over")
        # the storm self-heals (measured: ~14 retries then the fallback);
        # a reset here would also kill any intentional hold - no escape.
        if len(names) >= 40:
            session_ready = True
            break
        if time.time() - t0 > 240 and len(names) < 10:
            print("  boot stalled early - aborting watch")
            break

    # 0x80800800 is kdcom's STATIC not-present id: every boot announces with
    # it first, then the daemon's acks flip the pair ~10s in (measured
    # 2026-10-14). "Ever seen" is therefore not the abandon test - it fires
    # on every healthy boot. The session is alive iff the guest ever acked
    # from the present pair.
    alive = False
    with open(LOG, errors="replace") as f:
        alive = "RX ack pid=0x8080000" in f.read()
    abandoned = not alive
    print(f"  session_ready={session_ready} abandoned={abandoned} "
          f"after {time.time()-t0:.0f}s ({len(names)} unique modules)")
    if abandoned:
        return 2
    return 0 if session_ready else 1


if __name__ == "__main__":
    sys.exit(main())
