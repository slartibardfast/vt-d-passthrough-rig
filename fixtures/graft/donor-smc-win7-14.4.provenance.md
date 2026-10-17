# Donor provenance: the Win7 14.4 Hawaii SMC blob

## The blob

fixtures/graft/donor-smc-win7-14.4.bin — 60,574 bytes (0xEC9E), the Hawaii
SMC firmware image, sha256 25b10f61... (see below).

## Where it came from

The campaign's source package (14-4-win7-win8-win8.1-64-dd-ccc-whql.exe,
295MB desktop) is retired from every reachable host: www2.ati.com 301s to
a dead FAQ, and no Wayback snapshot archived a body. The archived sibling
was used instead:

- Package: 14-4-mobility-win7-win8-win8.1-64-dd-ccc-whql.exe (271,932,192
  bytes), fetched from the Wayback Machine:
  https://web.archive.org/web/20140812073226id_/http://www2.ati.com/drivers/mobile/14-4-mobility-win7-win8-win8.1-64-dd-ccc-whql.exe
- Component: Packages/Drivers/Display/WB6A_INF/B171094/atikmdag.sy_
  (9,255,425 bytes, SZDD; expanded with the rig's szdd.py to 15,376,384
  bytes, PE-valid).
- Extraction: bytes [0xBFFA13, 0xBFFA13 + 60574) — a unique slot (one
  copy) in the Win7 binary, unlike the icafe build's five-copy layout.
- Driver version: 14.100.0.0000, DriverVer 04/17/2014 (WB6A_INF INFs) —
  the exact version and build date the peer's notes (host
  plan/0004/PLAN.md, "The SMC firmware's origin") record for their
  WB_INF donor at the same offset. All primary contracts match: offset,
  length, version, build date.

## The digest note

The campaign summary recalled the donor blob as sha256 100355ef...; the
fresh extraction writes 25b10f61... The stock slot digest from the same
summary (daa0a867...) matched our packed file exactly, so the convention
is proven reproducible; the donor value is a recollection that appears in
no committed note. Every primary contract matches the peer's record, and
the graft boot's PowerPlay evidence is the final judge. If the graft
fails on firmware grounds, re-sourcing the desktop WB_INF build is the
first suspicion to test.

## Re-acquisition recipe (if ever needed)

7z x the package; extract WB6A_INF/B171094/atikmdag.sy_; `python3
szdd.py <sy_> <sys>`; dd the 60,574 bytes at 0xBFFA13.
