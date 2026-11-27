#!/usr/bin/env python3
"""Build the XP INF candidate that points the 290X at a Hawaii section.

The XP 14.4 INF maps `PCI\\VEN_1002&DEV_67B0` to `ati2mtag_Kaveri_Desktop` and has no Hawaii section. The
same-era Win7 INF maps the same device to `ati2mtag_Hawaii` and carries two Hawaii power settings.
This script clones the Kaveri install, services and software-settings sections under Hawaii names, adds
the Hawaii settings, and repoints the device line. Nothing else changes.
"""
import sys

SRC = "work/candidates/CA178340.inf"
DST = "work/candidates/CA178340-hawaii.inf"


def read(path):
    with open(path, encoding="latin-1") as f:
        return f.readlines()


def section(lines, name):
    start = next(i for i, l in enumerate(lines) if l.strip().lower() == ("[%s]" % name).lower())
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("[")), len(lines))
    return start, end


def clone(lines, old, new):
    s, e = section(lines, old)
    body = list(lines[s:e])
    body[0] = "[%s]\n" % new
    return body


def main():
    out = read(SRC)
    for i, line in enumerate(out):
        if "DEV_67B0" in line and "ati2mtag_Kaveri_Desktop" in line:
            out[i] = line.replace("ati2mtag_Kaveri_Desktop", "ati2mtag_Hawaii")
            break
    else:
        print("no 67B0 line pointing at the Kaveri section", file=sys.stderr)
        return 1

    hawaii = clone(out, "ati2mtag_Kaveri_Desktop", "ati2mtag_Hawaii")
    services = clone(out, "ati2mtag_Kaveri_Desktop.Services", "ati2mtag_Hawaii.Services")
    settings = clone(out, "ati2mtag_Kaveri_Desktop.SoftwareSettings", "ati2mtag_Hawaii.SoftwareSettings")
    settings.append("AddReg = ati2mtag_Hawaii_SoftwareDeviceSettings\n")
    block = ["\n"] + hawaii + services + settings + [
        "\n[ati2mtag_Hawaii_SoftwareDeviceSettings]\n",
        "HKR,, PP_EnableBAPM, %REG_DWORD%, 0\n",
        "HKR,, DalAllowDPrefSwitchingForGLSync, %REG_DWORD%, 1\n",
    ]
    strings = next((i for i, l in enumerate(out) if l.strip().lower() == "[strings]"), len(out))
    out = out[:strings] + block + out[strings:]
    with open(DST, "w", encoding="latin-1") as f:
        f.writelines(out)
    print("wrote", DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
