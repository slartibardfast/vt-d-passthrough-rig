#!/usr/bin/env python3
"""Grade one experiment run and say what it proved, in machine-checkable lines.

Why this file exists. The control load is graded on six things: the image path the loader used, the
absence of a bugcheck, the PowerPlay prints, the per-adapter key after the boot, the devnode's
ConfigManagerErrorCode, and what drove the display. Read by hand across a KD stream, an overlay
hive and a set of guest-written captures, that reading is where a campaign quietly talks itself
into a result, so each claim is one line with one verdict and a named remedy when it fails.

Three claims are reported and never demanded. PowerPlay prints are what a graft delta is supposed
to produce, so their absence is the point of an ungrafted control and a failure only when
`--expect-powerplay` is passed. The display path is required only with `--expect-display`, because
the control's answer is the VGA fallback by design. And a frame is only ever reported: this argv
attaches no display device at all (`-nodefaults`, `-display none`, VFIO `x-vga=on`), so QEMU has
no console surface to dump and says so, and the instrument that can see the card's own output is
the GLKVM feed, which needs an operator-held credential.

Two witnesses, different strengths. The KD stream carries ordering and timing, so it is the
authority for what loaded when. `ntbtlog.txt` carries the loader's own verdict per driver but no
timestamps, so it is the authority for what was attempted and refused. Neither replaces the other.

The offline hive is consulted for the registration only. `ConfigManagerErrorCode` lives in a
subkey the PnP manager creates with `REG_OPTION_VOLATILE`, so it exists only in memory and no
offline read of any overlay or backup can produce it: the running guest has to answer, and the
answer is accepted only when it carries the nonce that says this run wrote it.

Usage: load-verify.py --run <run-dir> [--hive <file>] [--expect-powerplay] [--expect-display <dll>]
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
DISPLAY_DRIVER = "ati2dvag"
FALLBACK = "vga.dll"
CLEAN_ENDINGS = ("clean-shutdown", "measured-shutdown")
BUGCHECK = re.compile(r"(KERNEL DEBUG ATTACH|BugCheck|SYSTEM_SERVICE_EXCEPTION|PAGE_FAULT_IN_"
                      r"NONPAGED_AREA|IRQL_NOT_LESS_OR_EQUAL|breakin sent)", re.I)
PP_LINE = re.compile(r"(PP_PowerPlay|PowerPlay)", re.I)


class Claim:
    """One graded fact. `unwitnessed` gates like a failure but blames the rig, not the device."""

    def __init__(self, name, required):
        self.name, self.required = name, required
        self.state = "MEASURED"
        self.detail = ""
        self.remedy = ""

    def ok(self, detail):
        self.state, self.detail = "PASS", detail
        return self

    def fail(self, detail, remedy=""):
        self.state, self.detail, self.remedy = "FAIL", detail, remedy
        return self

    def unwitnessed(self, detail, remedy):
        self.state, self.detail, self.remedy = "UNWITNESSED", detail, remedy
        return self

    def line(self):
        out = "%-11s %-18s %s" % (self.state, self.name, self.detail)
        if self.state in ("FAIL", "UNWITNESSED") and self.remedy:
            out += "\n            remedy: " + self.remedy
        return out


def read_kd_log(run):
    for cand in ("kdc-detached.log", "kdc.log"):
        p = run / cand
        if p.is_file():
            return p.read_text(errors="replace"), cand
    return "", "(no KD log in the run directory)"


def read_capture(path):
    """Decode a guest-written file as the bytes it is, since Windows writes two different ones.

    `wmic` and `/bootlog` redirect UTF-16LE with a BOM. A reader that decodes those as latin-1
    sees every ASCII character doubled by a NUL, so a regex for a driver name matches nothing and
    a non-empty file reports as empty of the thing it names. That bug produced "201 lines, 0
    naming the ATI driver" over a file that said "Loaded driver" seven times.
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", "replace")
    else:
        text = raw.decode("utf-8", "replace")
    return [l.strip() for l in text.replace("\r\n", "\n").splitlines() if l.strip()]


def evidence(run, rel):
    return run / "evidence" / rel


def grade_capture_witness(run, manifest, claim):
    """Do the in-guest captures belong to this run?

    A file left on the volume by an earlier boot, or baked into the golden by a staging mistake,
    is otherwise indistinguishable from one this run wrote, and it grades as evidence. The hook
    stamps the run's nonce into `capture.txt` at both ends, so the grader can ask.
    """
    nonce = manifest.get("nonce")
    capture = evidence(run, "WINDOWS/capture.txt")
    if not capture.is_file():
        return claim.unwitnessed(
            "no capture.txt, so nothing proves the in-guest captures belong to this run",
            "the Startup trigger writes it; check that install-channel/startup-hook.bat reached "
            "the overlay (./golden-settings.py --image <overlay> --startup-hook "
            "install-channel/startup-hook.bat --runid <nonce> --check)")
    lines = read_capture(capture)
    start = [l for l in lines if l.lower().startswith("capture-start")]
    end = [l for l in lines if l.lower().startswith("capture-end")]
    if not start or not end:
        return claim.unwitnessed("capture.txt carries no start and end sentinel: %s" % lines[:3],
                                 "the hook began but did not finish, so the captures are partial")
    if not nonce:
        return claim.unwitnessed("run.json carries no nonce to compare against",
                                 "run-experiment.sh records the nonce it handed the overlay")
    seen = re.search(r"capture-(?:start|end)\s+(\S+)", start[0] + " " + end[0])
    stamp = seen.group(1) if seen else ""
    if stamp != nonce:
        return claim.fail("the captures name run %s, not this run %s" % (stamp, nonce),
                          "a file inherited from the golden or an earlier boot cannot witness "
                          "this run: re-stage the overlay with the current hook and nonce")
    rc = re.findall(r"(?m)^(\S+ rc=(\d+))", "\n".join(lines))
    counts = ", ".join("%s=%s" % (name.split()[0], code) for name, code in rc)
    return claim.ok("nonce %s, sentinels present, %s%s"
                    % (nonce, counts or "no rc lines", clock_skew_note(run)))


def clock_skew_note(run):
    """How far the guest's clock sits from the host's, measured rather than assumed.

    The guest boots with `-rtc base=localtime` and believes it is UTC-8, so every NTFS mtime the
    guest writes reads eight hours ahead of true UTC. `runid.txt` is the one artifact in the same
    evidence set whose mtime the host wrote, so the pair gives the offset without asking anyone to
    remember a flag. Reading a guest mtime as a UTC time is how a run looks like it happened in the
    future, and eight hours is inside the window where that reads as a plausible ordering.
    """
    host = evidence(run, "WINDOWS/runid.txt")
    guest = evidence(run, "WINDOWS/capture.txt")
    if not (host.is_file() and guest.is_file()):
        return ""
    delta = (guest.stat().st_mtime - host.stat().st_mtime) / 3600.0
    if abs(delta) < 0.05:
        return ", guest clock agrees with the host"
    return ", guest mtimes read %+.1f h from the host's (the guest runs localtime)" % delta


def grade_powerplay(kd, claim, expect):
    lines = [l for l in kd.splitlines() if PP_LINE.search(l)]
    fingerprints = {}
    for line in lines:
        m = re.search(r"([a-z_]+\.c)\[(\d+)\].*?:\s*([^,]{0,60})", line, re.I)
        key = re.sub(r"\s+", " ", (m.group(0) if m else line)).strip()[:90]
        fingerprints[key] = fingerprints.get(key, 0) + 1
    top = sorted(fingerprints.items(), key=lambda kv: -kv[1])[:3]
    detail = ("%d lines carry a PowerPlay assertion, over %d distinct sites: %s"
              % (len(lines), len(fingerprints),
                 "; ".join("%s x%d" % (k.strip(), n) for k, n in top) or "none"))
    if expect and not lines:
        return claim.fail("no PowerPlay prints at all",
                          "the delta whose whole claim is these prints produced none")
    claim.state = "PASS" if (expect and lines) else "MEASURED"
    claim.detail = detail + ("" if expect else " (reported, not required: a control carries no "
                                               "graft delta, and these are its baseline "
                                               "fingerprints)")
    return claim


def grade_bootlog(run, claim):
    ntbt = evidence(run, "WINDOWS/ntbtlog.txt")
    if not ntbt.is_file() or ntbt.stat().st_size == 0:
        return claim.fail("no ntbtlog.txt in the run's evidence",
                          "the booted entry must carry /bootlog: ./golden-settings.py --image "
                          "<overlay> --bootlog, and the run must boot that overlay")
    lines = read_capture(ntbt)
    loaded = [l for l in lines if l.lower().startswith("loaded driver") and SERVICE_IMAGE.lower()
              in l.lower()]
    refused = [l for l in lines if l.lower().startswith("did not load driver")
               and SERVICE_IMAGE.lower() in l.lower()]
    tail = [l for l in lines if re.search(r"(hidusb|kbdhid|srv\.sys|HTTP\.sys|mrxsmb)", l, re.I)]
    detail = ("%d lines; %d loaded, %d refused for %s; log reaches %s"
              % (len(lines), len(loaded), len(refused), SERVICE_IMAGE,
                 "the PnP phase" if tail else "only the loader phase"))
    if not loaded:
        return claim.fail(detail,
                          "the loader never recorded a successful load of the registered path: "
                          "compare the FILE_IO opens in the KD stream and the service's Start and "
                          "ImagePath with ./registration_graft.py check <hive>")
    return claim.ok(detail + ("; the retries are the service loader reaching for a volume letter "
                              "that does not exist yet, a known fragility of loading off "
                              "\\??\\D:" if refused else ""))


def grade_display(kd, run, claim, expect):
    """What drove the display, from the module stream and the guest's own mode report."""
    loaded = set(re.findall(r"MODULE LOAD.*?([A-Za-z0-9_]+\.dll)", kd))
    opened = set(n.lower() for n in re.findall(r"FILE_IO.*?\\((?i:[a-z0-9_]+\.dll))", kd))
    ati_mapped = any(n.lower() == DISPLAY_DRIVER + ".dll" for n in loaded)
    vga_mapped = any(n.lower() == FALLBACK for n in loaded)
    video = evidence(run, "WINDOWS/videoctl.txt")
    mode = ""
    if video.is_file():
        vals = {}
        for line in read_capture(video):
            k, _, v = line.partition("=")
            if k.strip():
                vals[k.strip().lower()] = v.strip().strip('"')
        mode = " mode %sx%sx%s AdapterRAM=%s InstalledDisplayDrivers=%s" % (
            vals.get("currenthorizontalresolution", "?"), vals.get("currentverticalresolution", "?"),
            vals.get("currentbitsperpixel", "?"), vals.get("adapterram", "?"),
            vals.get("installeddisplaydrivers", "?"))
    what = ("ati2dvag.dll mapped" if ati_mapped else
            "%s.dll opened, never mapped, and %s mapped instead" % (DISPLAY_DRIVER, FALLBACK)
            if vga_mapped else "neither %s.dll nor %s mapped" % (DISPLAY_DRIVER + ".dll", FALLBACK))
    detail = "%s%s" % (what, mode)
    if expect:
        want = expect.lower()
        want = want if want.endswith(".dll") else want + ".dll"
        if want in {n.lower() for n in loaded}:
            return claim.ok(detail)
        return claim.fail("expected %s to be mapped; %s" % (want, detail),
                          "the display driver the delta is claimed to install never took the "
                          "session; see the per-adapter key line and the loader's own ntbtlog")
    claim.detail = detail + " (reported: the control's expected answer is the fallback)"
    return claim


def grade_devnode(run, claim, enum_path=None):
    """Whether PnP accepted the device, answered by the machine that knows.

    The hive cannot answer: the PnP manager creates the instance's `Control` subkey with
    `REG_OPTION_VOLATILE`, so it lives only in memory and is never written to the hive file. Two
    banked SYSTEM hives, one taken from a booted system, agree with that. So the guest is asked,
    and only an answer carrying this run's nonce counts.
    """
    capture = evidence(run, "WINDOWS/devnode.txt")
    if not capture.is_file() or not capture.stat().st_size:
        return claim.unwitnessed(
            "no live capture",
            "the guest has to answer while it runs: install-channel/startup-hook.bat queries "
            "Win32_PnPEntity into WINDOWS/devnode.txt. Absence here says the capture did not "
            "happen, and says nothing about whether PnP accepted the device")
    # An answer is only worth what the witness is worth: the nonce check in the capture claim above
    # is what makes this line mean "this run asked" rather than "some boot answered".
    lines = read_capture(capture)
    text = "\n".join(lines)
    codes = re.findall(r"(?mi)^\s*ConfigManagerErrorCode\s*=\s*(\d+)", text)
    names = re.findall(r"(?mi)^\s*Name\s*=\s*(.+)", text)
    status = re.findall(r"(?mi)^\s*Status\s*=\s*(.+)", text)
    if not codes:
        return claim.fail("the capture answers no error code: %s" % (lines[:3] or "empty"),
                          "wmic wrote a header and nothing else: check the PNPDeviceID pattern "
                          "against the hardware in the host, and the rc line in capture.txt")
    codes = [int(c) for c in codes]
    who = (names[0] if names else "unnamed") + ("/" + status[0] if status else "")
    where = " for %s" % enum_path if enum_path else ""
    if codes == [0]:
        return claim.ok("ConfigManagerErrorCode=0 from the running guest for %s%s" % (who, where))
    return claim.fail("ConfigManagerErrorCode=%s from the running guest (%s)" % (codes, who),
                      "PnP refused the device, so no display path can be claimed from this run; "
                      "the code is the answer, look it up in the device-manager error list")


def grade_frame(run, claim):
    """The only view of the card's own output is the GLKVM feed, and the agent cannot open it."""
    if (run / "frame.png").is_file():
        return claim.ok("frame.png collected from the GLKVM feed")
    claim.detail = ("unattempted: this argv attaches no display device (-nodefaults -display none, "
                    "VFIO x-vga=on), so QEMU has no console to dump, and the GLKVM feed that can "
                    "see the monitor needs the operator's credential")
    return claim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--hive", type=Path, default=None)
    ap.add_argument("--expect-powerplay", action="store_true")
    ap.add_argument("--expect-display", default=None)
    a = ap.parse_args()
    run = a.run
    if not run.is_dir():
        print("load-verify: no such run directory: %s" % run, file=sys.stderr)
        print("       remedy: ./run-experiment.sh --golden <qcow2> --channel <img> --name <name>",
              file=sys.stderr)
        return 2
    manifest_path = run / "run.json"
    if not manifest_path.is_file():
        print("load-verify: %s has no run.json, so nothing about it is attributable" % run,
              file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())
    kd, kd_name = read_kd_log(run)

    claims = [Claim("image-path", True), Claim("no-bugcheck", True), Claim("capture", True),
              Claim("bootlog", True), Claim("per-adapter-key", True), Claim("devnode-code", True),
              Claim("display-path", bool(a.expect_display)), Claim("powerplay", a.expect_powerplay),
              Claim("frame", False)]
    by = {c.name: c for c in claims}

    opened = SERVICE_IMAGE in kd
    mapped = re.search(r"MODULE LOAD.*\b%s\.sys\b" % MINIPORT, kd) is not None
    if opened and mapped:
        by["image-path"].ok("%s opened by the guest and %s.sys mapped, in %s"
                            % (SERVICE_IMAGE, MINIPORT, kd_name))
    else:
        by["image-path"].fail("opened=%s mapped=%s (%s)" % (opened, mapped, kd_name),
                              "the miniport did not load from the registered path: check "
                              "Services\\%s ImagePath with ./registration_graft.py check <hive> "
                              "and the channel copy's drv\\%s.sys hash" % (MINIPORT, MINIPORT))

    hits = sorted(set(m.group(0) for m in BUGCHECK.finditer(kd)))
    outcome = str(manifest.get("outcome", ""))
    if hits:
        by["no-bugcheck"].fail("the KD log carries %s" % ", ".join(hits),
                               "the guest did not survive to answer: grade the crash, not the driver")
    elif manifest.get("crash_suspected") is True or outcome not in CLEAN_ENDINGS:
        by["no-bugcheck"].fail("outcome=%s" % (outcome or "unset"),
                               "the run did not end on its own, so absence of a bugcheck is not "
                               "evidence of absence of a crash")
    else:
        by["no-bugcheck"].ok("no bugcheck marker in %s, and the guest ended its own run (%s)"
                             % (kd_name, outcome))

    grade_capture_witness(run, manifest, by["capture"])
    grade_bootlog(run, by["bootlog"])
    grade_powerplay(kd, by["powerplay"], a.expect_powerplay)
    grade_display(kd, run, by["display-path"], a.expect_display)
    grade_frame(run, by["frame"])

    hive = a.hive or (run / "evidence" / "WINDOWS" / "system32" / "config" / "system")
    grade_devnode(run, by["devnode-code"])
    if not Path(hive).is_file():
        by["per-adapter-key"].fail(
            "no collected hive at %s" % hive,
            "run-experiment.sh copies WINDOWS/system32/config/system out of the overlay after the "
            "guest ends the run")
    elif rg.hivex is None:
        by["per-adapter-key"].fail("this interpreter has no hivex", rg.HIVEX_REMEDY)
    else:
        try:
            h = rg.open_hive(str(hive))
            f = rg.facts(h)
            drivers, vgacomp, service = f.get("dev_drivers"), f.get("dev_vgacompat"), f.get("dev_service")
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
                    "re-run ./registration_graft.py check %s and compare with the volatile map in "
                    "evidence/WINDOWS/devicemap.txt" % hive)
            if by["devnode-code"].state == "PASS":
                by["devnode-code"].detail += " for %s" % f["enum_path"]
        except rg.Bad as exc:
            by["per-adapter-key"].fail(str(exc), "read the hive by hand with hivexget")

    for c in claims:
        print(c.line())
    bad = [c.name for c in claims if c.required and c.state in ("FAIL", "UNWITNESSED")]
    print("run: %s nonce=%s golden=%s channel=%s"
          % (manifest.get("name", "?"), manifest.get("nonce", "?"),
             str(manifest.get("golden_sha256", ""))[:12], str(manifest.get("channel_sha256", ""))[:12]))
    if bad:
        print("verdict: FAIL (%s)" % ", ".join(bad))
        return 1
    print("verdict: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
