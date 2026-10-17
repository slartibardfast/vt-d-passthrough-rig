# Donor provenance: the Win7 14.4 desktop Hawaii SMC blob

## The blob

fixtures/graft/donor-smc-win7-14.4.bin — 60,574 bytes (0xEC9E), the Hawaii
SMC firmware image, sha256
100355efec51d175afc3cfe2e4f0e03a3e6862f659773564c082a9a736cf167e.

## Where it came from

The desktop package, operator-acquired 2026-10-17 and placed at
~/14-4-win7-win8-win8.1-64-dd-ccc-whql.exe:

- Package: 14-4-win7-win8-win8.1-64-dd-ccc-whql.exe (295,347,968 bytes,
  NSIS-wrapped), sha256
  e99b480c9f84db095e43c48c8ca307b16aa9244118e1ee4c222f8414ec0ba6b2.
  Durable: ~/xp64-corpus/drivers/14-4-win7-win8-win8.1-64-dd-ccc-whql.exe
  (MANIFEST.tsv row, source=operator; the x32 sibling is banked beside
  it). Working copy: work/donors/14-4-desktop-win7-64.exe.
- Component: Packages/Drivers/Display/WB_INF/B171094/atikmdag.sy_
  (8,526,281 bytes, SZDD; expanded with the rig's szdd.py to 13,515,264
  bytes, PE-valid), sha256
  afdff78d61d1495bd51197cf26eb34f77871da0a13e9056de3776c9364fbc9a9.
- Extraction: bytes [0xBFFA13, 0xBFFA13 + 60574) — a unique slot (one
  occurrence, verified by full-slot and 64-byte-head counts).
- Driver version: 14.100.0.0000, DriverVer 04/17/2014 (the package's INFs:
  DriverVer=04/17/2014, 14.100.0.0000). This is the peer's literal
  component (host plan/0004/PLAN.md, "The SMC firmware's origin":
  Downloads\WB_INF\B171094\atikmdag.sy_), and every primary contract
  matches: path, offset, length, version, build date.

## The digest note, resolved

The campaign summary's recollection (sha256 100355ef...) was exact: it is
this desktop WB_INF blob. The earlier committed donor (25b10f61...) was
carved at the same offset from a *different binary* — WB6A_INF/B171094/
atikmdag.sy_ — first taken from the archived mobility sibling (271,932,192
bytes, Wayback) and later found byte-identical (sha256 119c8549...) in this
desktop package's own WB6A_INF tree. The mobility-vs-desktop mismatch was
never package-level: it was component-level, WB6A_INF (the Win8.1-unified
binary, shared across both packages) vs WB_INF (the peer's binary). The
recollection was sound; the component choice was wrong. The operator
directed the desktop re-source (NAK on the mobility donor, 2026-10-17),
which resolved the doubt outright.

## Re-acquisition recipe (if ever needed)

Durable package: ~/xp64-corpus/drivers/14-4-win7-win8-win8.1-64-dd-ccc-whql.exe
(hash-verified by the corpus manifest; the only copy with a living
provenance chain — the operator delivered it 2026-10-17). 7z x the
package; extract WB_INF/B171094/atikmdag.sy_; `python3 szdd.py <sy_>
<sys>`; take the 60,574 bytes at 0xBFFA13. The web is no longer a source
for this package: www2.ati.com 301s to a dead FAQ with no Wayback body
(verified by CDX — every capture is a redirect or tombstone), TechSpot's
file server 404s, Guru3D's only mirror (ftp.nluug.nl) deleted it, and the
Microsoft Update Catalog purged 14.100-era entries.
