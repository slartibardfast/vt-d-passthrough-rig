#!/usr/bin/env python3
"""Grade one experiment run and say what it proved, in machine-checkable lines.

Why this file exists. The plan's control load is graded on six things: the image path the
loader used, the absence of a bugcheck, the PowerPlay prints, the per-adapter key after the
boot, the devnode's ConfigManagerErrorCode, and the feed. Read by hand across a KD log, an
overlay hive and a device-map dump, that reading is where a campaign quietly talks itself into
a result, so each claim is one line with one verdict and a named remedy when it fails.

Two of the six are never required, only reported. The PowerPlay prints are what a graft delta
is supposed to produce, so their absence is the point of the ungrafted control and a failure
only when `--expect-powerplay` is passed by a variant run. The feed is the emulated standard
VGA surface: it witnesses how far the boot came and nothing about what the 290X put on the
physical monitor, and saying so is better than dressing a black frame up as a verdict.

The hive is read with the same reader that wrote the registration, so a grader and a graft
cannot disagree about what a key holds. That reader needs hivex, which lives in the system
Python and not in the rig's venv; a run without it reports the hive claims as unread rather
than passing them.

Usage: load-verify.py --run <run-dir> [--hive <file>] [--expect-powerplay]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registration_graft as rg

SERVICE_IMAGE = r"\??\D:\drv\ati2mtag.sys"
MINIPORT = "ati2mtag"
BUGCHECK = re.compile(r"(KERNEL DEBUG ATTACH|BugCheck|SYSTEM_SERVICE_EXCEPTION|PAGE_FAULT_IN_"
                      r"NONPAGED_AREA|IRQL_NOT_LESS_OR_EQUAL|breakin sent)", re.I)
POWERPLAY = re.compile(r"(powerplay|PP_|AtiPcib2|DIG|TransmitterTable)", re.I)
CLEAN_ENDINGS = ("clean-shutdown", "measured-shutdown")


class Claim:
    def __init__(self, name, required):
        self.name, self.required = name, required
        self.state = "MEASURED"
        self.detail = ""
        self.remedy = ""

    def fail(self, detail, remedy):
        self.state, self.detail, self.remedy = "FAIL", detail, remedy
        return self

    def ok(self, detail):
        self.state, self.detail = "PASS", detail
        return self

    def line(self):
        out = "%-6s %-18s %s" % (self.state, self.name, self.detail)
        if self.state == "FAIL" and self.remedy:
            out += "\n       remedy: " + self.remedy
        return out


def read_kd_log(run):
    for cand in ("kdc-detached.log", "kdc.log"):
        p = run / cand
        if p.is_file():
            return p.read_text(errors="replace"), cand
    return "", "(no KD log in the run directory)"


def ppm_near_black(path):
    """The percentage of near-black pixels on a QEMU screendump, P6 only."""
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        return None
    # The magic is two bytes, "P6": a reader that starts inside it takes the "6" for the width,
    # which is what the first version of this function did and it counted three pixels in a
    # two-pixel frame.
    parts, pos = [], 2
    while len(parts) < 3:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        end = pos
        while end < len(data) and not data[end:end + 1].isspace():
            end += 1
        parts.append(int(data[pos:end]))
        pos = end
    pos += 1
    w, h, _max = parts
    body = data[pos:pos + w * h * 3]
    pixels = len(body) // 3
    if not pixels:
        return None
    dark = sum(1 for i in range(0, pixels * 3, 3)
               if body[i] < 32 and body[i + 1] < 32 and body[i + 2] < 32)
    return dark * 100 / pixels


def grade_devnode(run, h, facts, claim):
    """Whether PnP accepted the device, read from the machine that knows.

    The offline hive is not that source. Both banked SYSTEM hives, one taken from a booted
    system, carry no ConfigManagerErrorCode on the card's instance key, and no `Control` subkey
    either: SetupAPI answers that from the running device node, so `install-channel/
    startup-hook.bat` asks `Win32_PnPEntity` during the run and the host reads the capture back
    out of the overlay. The hive is still consulted, because a Windows that did write it is a
    Windows worth reporting.
    """
    capture = run / "evidence" / "WINDOWS" / "devnode.txt"
    if capture.is_file() and capture.stat().st_size:
        raw = capture.read_bytes()
        text = raw.decode("utf-16-le", "replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
            else raw.decode("latin-1", "replace")
        codes = re.findall(r"(?m)^\s*ConfigManagerErrorCode\s*=\s*(\d+)", text, re.I)
        names = re.findall(r"(?m)^\s*Name\s*=\s*(.+)", text, re.I)
        if not codes:
            return claim.fail("the live capture names no error code: %s" %
                          (" | ".join(n.strip() for n in names[:2]) or "empty of values"),
                          "wmic wrote a header and nothing else: check the PNPDeviceID pattern in "
                          "install-channel/startup-hook.bat against the hardware in the host")
        codes = [int(c) for c in codes]
        if codes == [0]:
            return claim.ok("ConfigManagerErrorCode=0 from the running guest (%s) for %s"
                      % (", ".join(n.strip() for n in names[:1]) or "unnamed device",
                         facts["enum_path"]))
        return claim.fail("ConfigManagerErrorCode=%s from the running guest" % codes,
                      "PnP refused the device, so no display path can be claimed from this run; "
                      "the code is the answer, look it up in the device-manager error list")
    cm = rg.value_map(h, rg.descend(h, [facts["control_set"], "Enum", "PCI"] +
                                    facts["enum_path"].split("\\")[3:] + ["Control"]))
    if "configmanagererrorcode" in cm:
        code = rg.decode(h, cm["configmanagererrorcode"])
        if code == 0:
            return claim.ok("ConfigManagerErrorCode=0 read from the offline hive on %s"
                      % facts["enum_path"])
        return claim.fail("ConfigManagerErrorCode=%d on %s" % (code, facts["enum_path"]),
                      "PnP refused the device, so no display path can be claimed from this run")
    return claim.fail("no live capture and the hive carries no such value",
                  "the guest has to answer this while it runs: install-channel/startup-hook.bat "
                  "queries Win32_PnPEntity into %s and run-experiment.sh collects it" % capture)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--hive", type=Path, default=None)
    ap.add_argument("--expect-powerplay", action="store_true")
    a = ap.parse_args()
    run = a.run
    if not run.is_dir():
        print("load-verify: no such run directory: %s" % run, file=sys.stderr)
        print("       remedy: run ./run-experiment.sh --golden <qcow2> --channel <img> --name <name>",
              file=sys.stderr)
        return 2
    manifest_path = run / "run.json"
    if not manifest_path.is_file():
        print("load-verify: %s has no run.json, so nothing about it is attributable" % run,
              file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())
    kd, kd_name = read_kd_log(run)

    claims = [Claim("image-path", True), Claim("no-bugcheck", True), Claim("powerplay", False),
              Claim("per-adapter-key", True), Claim("devnode-code", True), Claim("bootlog", True),
              Claim("feed", False)]
    by = {c.name: c for c in claims}

    # 1. The image path the loader used, from the guest's own file opens.
    opened = SERVICE_IMAGE in kd
    mapped = re.search(r"MODULE LOAD.*\b%s\.sys\b" % MINIPORT, kd) is not None
    if opened and mapped:
        by["image-path"].ok("%s opened by the guest and %s.sys mapped, in %s"
                            % (SERVICE_IMAGE, MINIPORT, kd_name))
    else:
        by["image-path"].fail(
            "opened=%s mapped=%s (%s)" % (opened, mapped, kd_name),
            "the miniport did not load from the registered path: check Services\\%s ImagePath "
            "with ./registration_graft.py check <hive> and the channel copy's drv\\%s.sys hash"
            % (MINIPORT, MINIPORT))

    # 2. No bugcheck, and the run ended the way a live guest ends a run.
    hits = sorted(set(m.group(0) for m in BUGCHECK.finditer(kd)))
    outcome = str(manifest.get("outcome", ""))
    if hits:
        by["no-bugcheck"].fail("the KD log carries %s" % ", ".join(hits),
                               "the guest did not survive to answer: grade the crash, not the driver")
    elif manifest.get("crash_suspected") is True or outcome not in CLEAN_ENDINGS:
        by["no-bugcheck"].fail("outcome=%s" % (outcome or "unset"),
                               "the run did not end on its own, so absence of a bugcheck is not evidence "
                               "of absence of a crash")
    else:
        by["no-bugcheck"].ok("no bugcheck marker in %s, and the guest ended its own run (%s)"
                             % (kd_name, outcome))

    # 3. PowerPlay: reported always, required only of a variant that claims to produce it.
    pp = len(POWERPLAY.findall(kd))
    pp_claim = by["powerplay"]
    if a.expect_powerplay and pp == 0:
        pp_claim.fail("no PowerPlay prints in %s" % kd_name,
                      "the delta whose whole claim is these prints produced none")
    else:
        pp_claim.state = "PASS" if (pp and a.expect_powerplay) else "MEASURED"
        pp_claim.detail = ("%d PowerPlay-shaped lines in %s%s"
                           % (pp, kd_name, "" if a.expect_powerplay else
                              " (reported, not required: the control carries no graft delta)"))

    # 4 and 5. The disk's own account, after the boot.
    hive = a.hive or (run / "evidence" / "WINDOWS" / "system32" / "config" / "system")
    if not Path(hive).is_file():
        detail = "no collected hive at %s" % hive
        remedy = ("collect it in the run: run-experiment.sh copies WINDOWS/system32/config/system "
                  "out of the overlay after the guest ends the run")
        by["per-adapter-key"].fail(detail, remedy)
        by["devnode-code"].fail(detail, remedy)
    elif rg.hivex is None:
        by["per-adapter-key"].fail("this interpreter has no hivex", rg.HIVEX_REMEDY)
        by["devnode-code"].fail("this interpreter has no hivex", rg.HIVEX_REMEDY)
    else:
        try:
            h = rg.open_hive(str(hive))
            f = rg.facts(h)
            drivers, vgacomp = f.get("dev_drivers"), f.get("dev_vgacompat")
            service = f.get("dev_service")
            if drivers == ["ati2dvag"] and vgacomp == 0 and service == MINIPORT:
                by["per-adapter-key"].ok(
                    "Control\\Video\\{%s}\\0000 after the boot: InstalledDisplayDrivers=%s "
                    "VgaCompatible=%s Video Service=%s; services ImagePath=%s Start=%s"
                    % (f.get("videoid"), drivers, vgacomp, service, f.get("svc_imagepath"),
                       f.get("svc_start")))
            else:
                by["per-adapter-key"].fail(
                    "drivers=%s VgaCompatible=%s service=%s of control set %s"
                    % (drivers, vgacomp, service, f.get("control_set")),
                    "the per-adapter key is not the one the runtime reads for the ATI path: "
                    "re-run ./registration_graft.py check %s and compare with the DEVICEMAP in "
                    "evidence/WINDOWS/devicemap.txt" % hive)
            grade_devnode(run, h, f, by["devnode-code"])
        except rg.Bad as exc:
            by["per-adapter-key"].fail(str(exc), "read the hive by hand with hivexget to see what it holds")
            by["devnode-code"].fail("the hive read failed", "read the hive by hand with hivexget")

    # 6. The loader's own record, which is why /bootlog is on the entry.
    ntbt = run / "evidence" / "WINDOWS" / "ntbtlog.txt"
    if not ntbt.is_file() or ntbt.stat().st_size == 0:
        by["bootlog"].fail("no ntbtlog.txt in the run's evidence",
                           "the booted entry must carry /bootlog: ./golden-settings.py --image "
                           "<overlay> --bootlog, and the run must boot that overlay")
    else:
        lines = [l.strip() for l in ntbt.read_text(errors="replace").splitlines() if l.strip()]
        mine = [l for l in lines if re.search(r"ati2mtag|ati2dvag", l, re.I)]
        by["bootlog"].ok("%d lines, %d naming the ATI driver: %s"
                         % (len(lines), len(mine), " | ".join(mine[:3]) or "none"))

    # 7. The feed, reported with its limit.
    frames = sorted(run.glob("feed-*.ppm"))
    if not frames:
        by["feed"].state, by["feed"].detail = "MEASURED", "no feed frame was captured"
    else:
        seen = []
        for fr in frames:
            pct = ppm_near_black(fr)
            seen.append("%s=%s" % (fr.stem, "unreadable" if pct is None else "%.1f%% near-black" % pct))
        by["feed"].detail = ("%s, on the emulated standard VGA surface: it witnesses how far the boot "
                             "came, not what the 290X drove on the monitor" % ", ".join(seen))

    for c in claims:
        print(c.line())
    bad = [c.name for c in claims if c.required and c.state == "FAIL"]
    print("run: %s golden=%s channel=%s" % (manifest.get("name", "?"),
                                            str(manifest.get("golden_sha256", ""))[:12],
                                            str(manifest.get("channel_sha256", ""))[:12]))
    if bad:
        print("verdict: FAIL (%s)" % ", ".join(bad))
        return 1
    print("verdict: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
