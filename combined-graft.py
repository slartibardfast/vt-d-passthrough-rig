#!/usr/bin/env python3
"""Build the combined graft from the three banked deltas and prove it adds nothing of its own.

Why this exists. The milestone's combined graft is the union of the descriptor redirect, the dispatch
routing and the SMC fill, and the claim that has to be checkable is a narrow one: the final diff
contains exactly the banked changes and no additional behavioural bytes. Reading three JSON manifests
and trusting that their union is what was applied is not that check. So this derives the union from
the banked records themselves, applies it, and refuses unless the resulting diff equals the union
position for position.

What is banked, and why the ranges are recomputed rather than copied. Each single delta records its
own `changed_ranges`. Two of them record `diff_mode: exact`, which is a statement about positions; the
third records `within`, because a 60574-byte donor replaces a region and only some of those positions
actually differ from the base. A union of the recorded ranges is therefore a superset of the truth,
and a superset is exactly what a proof of "no additional bytes" must not rest on. So each delta is
re-derived here from its own manifest and expectation, its behavioural positions are read off the bytes,
and those positions are what the combined expectation names.

The result is written as two records and one note, all three of which the milestone's own verify
command then reads back through `graft-check.py`: a manifest of ops, an expectation whose digest was
produced by the union rather than asserted over it, and a dated note explaining the route.

Usage: combined-graft.py [--base BLOB] [--out-prefix fixtures/graft/combined-deploy]
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

RIG = Path(__file__).resolve().parent
sys.path.insert(0, str(RIG))
import graft  # noqa: E402

# `checksum_offset` belongs to graft-check.py, the tool that already refuses a candidate whose stored
# checksum does not match its bytes. Reading it from there rather than re-deriving the PE offset here
# means one place in the rig knows where those four bytes sit. It is loaded by path because the file
# is named with a hyphen and so is not an importable module name.
_importer = importlib.util.spec_from_file_location("graft_check", RIG / "graft-check.py")
graft_check = importlib.util.module_from_spec(_importer)
_importer.loader.exec_module(graft_check)

BANKED = ("descriptor-redirect", "dispatch-routing", "smc-fill")
CHECKSUM_BYTES = 4


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def checksum_field(image):
    """The four bytes the PE header calls its checksum, which no delta is claiming to have chosen."""
    return set(range(graft_check.checksum_offset(image),
                     graft_check.checksum_offset(image) + CHECKSUM_BYTES))


def banked_positions(base, name, expectation):
    """The behavioural positions a banked delta actually moves, read from its own re-derived image."""
    if sha(base) != expectation["base_sha256"]:
        raise SystemExit("%s: the base blob is not the one %s was banked against" % (name, name))
    manifest_path = RIG / "fixtures" / "graft" / ("manifest-%s-deploy.json" % name)
    manifest = read(manifest_path)
    donors = {}
    for record, bound in (expectation.get("donors") or {}).items():
        data = (manifest_path.parent / record).read_bytes()
        if len(data) != bound["size"] or sha(data) != bound["sha256"]:
            raise SystemExit("%s: the donor %s is not the one the expectation binds" % (name, record))
        donors[record] = data
    out = graft.apply_manifest(base, manifest, donors)
    if sha(out) != expectation["output_sha256"]:
        raise SystemExit("%s: re-deriving the banked candidate gives %s, not %s"
                         % (name, sha(out)[:16], expectation["output_sha256"][:16]))
    field = checksum_field(base)
    return manifest, donors, {i for i, (a, b) in enumerate(zip(base, out)) if a != b and i not in field}


def ranges_of(positions):
    ordered = sorted(positions)
    spans = []
    for position in ordered:
        if spans and position == spans[-1][1]:
            spans[-1][1] = position + 1
        else:
            spans.append([position, position + 1])
    return [(start, end) for start, end in spans]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path,
                    default=RIG / "work" / "stageE" / "ati2mtag-s5_accessor_blob.sys")
    ap.add_argument("--manifest", type=Path,
                    default=RIG / "fixtures" / "graft" / "manifest-combined-deploy.json")
    ap.add_argument("--expect", type=Path,
                    default=RIG / "fixtures" / "graft" / "combined-deploy.expect.json")
    ap.add_argument("--notes", type=Path, default=RIG / "work" / "graft-manifest.md")
    a = ap.parse_args()
    base = a.base.read_bytes()

    ops, union, per_delta, donors = [], set(), {}, {}
    position_sets = {}
    for name in BANKED:
        expectation = read(RIG / "fixtures" / "graft" / ("%s-deploy.expect.json" % name))
        manifest, found_donors, positions = banked_positions(base, name, expectation)
        donors.update(found_donors)
        body = [op for op in manifest["ops"] if op.get("op") != "pe-checksum"]
        ops.extend(body)
        union |= positions
        position_sets[name] = positions
        spans = ranges_of(positions)
        per_delta[name] = {"positions": len(positions), "output_sha256": expectation["output_sha256"],
                           "ranges": [["0x%x" % s, "0x%x" % e] for s, e in spans],
                           # The range a delta is allowed to land in, which for a donor fill is the
                           # region it was given rather than the 7832 fragments it actually moves.
                           "allowed": [["0x%x" % min(positions), "0x%x" % (max(positions) + 1)]]
                           if len(spans) > 8 else [["0x%x" % s, "0x%x" % e] for s, e in spans]}
        print("%-20s %6d behavioural byte(s) in %d range(s)" % (name, len(positions), len(spans)))

    overlapping = []
    for name in BANKED:
        for other in BANKED:
            if name < other:
                shared = position_sets[name] & position_sets[other]
                if shared:
                    overlapping.append((name, other, len(shared)))
    if overlapping:
        print("the banked deltas share positions: %s" % overlapping)
    else:
        print("no two banked deltas share a single behavioural position")

    checksum_expectations = [read(RIG / "fixtures" / "graft" / ("%s-deploy.expect.json" % n))
                             for n in BANKED]
    if not all(e.get("deployable") for e in checksum_expectations):
        raise SystemExit("a banked delta is not marked deployable, so a checksum-last union is unjustified")
    combined_ops = ops + [{"op": "pe-checksum"}]
    combined_manifest = {"delta_id": "combined", "route": "union of the three banked single deltas",
                         "ops": combined_ops}
    out = graft.apply_manifest(base, combined_manifest, donors)
    field = checksum_field(base)
    moved = {i for i, (x, y) in enumerate(zip(base, out)) if x != y and i not in field}
    extra = sorted(moved - union)
    missing = sorted(union - moved)
    if extra or missing:
        raise SystemExit("the union is not the sum of its parts: %d byte(s) beyond the banked deltas, "
                         "%d banked byte(s) absent" % (len(extra), len(missing)))

    spans = ranges_of(moved)
    allowed = sorted({tuple(int(value, 16) for value in pair)
                      for name in BANKED for pair in per_delta[name]["allowed"]})
    positions_digest = hashlib.sha256(",".join(str(i) for i in sorted(moved)).encode()).hexdigest()
    expectation = {
        "delta_id": "combined",
        "route": ("the union of the descriptor redirect, the dispatch routing and the SMC fill, with "
                  "pe-checksum last and no operation that belongs to none of them"),
        "base_size": len(base),
        "base_sha256": sha(base),
        "output_sha256": sha(out),
        # `within` the banked regions, and pinned to exactly the banked positions by digest. The
        # fragments a donor fill actually moves run to thousands, so ranges alone would either be
        # unreadable or would have to be rounded, and a rounded range is a claim weaker than the one
        # the milestone asks for.
        "diff_mode": "within",
        "changed_ranges": [["0x%x" % s, "0x%x" % e] for s, e in allowed],
        "changed_positions_sha256": positions_digest,
        "changed_position_count": len(moved),
        "changed_fragments": len(spans),
        "changed_bytes": len(moved),
        "deployable": True,
        "components": {name: {"output_sha256": per_delta[name]["output_sha256"],
                              "changed_bytes": per_delta[name]["positions"]} for name in BANKED},
        "sum_of_components": sum(per_delta[name]["positions"] for name in BANKED),
        "union_positions": len(moved),
        "donors": {record: {"size": len(data), "sha256": sha(data)} for record, data in donors.items()},
    }

    # Written before the expectation, whose `notes_sha256` is the note's digest: the record binds the
    # prose, so the prose has to exist first and any later edit of it breaks the check.
    a.notes.parent.mkdir(parents=True, exist_ok=True)
    a.notes.write_text(notes(base, out, expectation, per_delta, overlapping, spans))
    expectation["notes_sha256"] = sha(a.notes.read_bytes())

    for path, payload in ((a.manifest, combined_manifest), (a.expect, expectation)):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    print("\ncombined: %d behavioural bytes over %d range(s), sha256 %s"
          % (len(moved), len(spans), sha(out)[:20]))
    print("wrote %s" % a.manifest)
    print("wrote %s" % a.expect)
    print("wrote %s" % a.notes)
    return 0


def notes(base, out, expectation, per_delta, overlapping, spans):
    lines = ["# The combined graft: what it unions, and what was proved before it was claimed",
             "",
             "Generated by `combined-graft.py`. Regenerate rather than edit: every digest below is a",
             "measurement of the bytes on disk, and a hand-edited note is a note nothing re-checks.",
             "",
             "The note carries no timestamp on purpose. Its digest is bound in the expectation file,",
             "so a wall-clock line would make every regeneration a mismatch, and a record that cannot",
             "be re-derived byte for byte is a record a fresh clone cannot check. When it was written is",
             "in the git history and in the task receipt; what it says is a function of the bytes.",
             "",
             "## The route",
             "",
             "The milestone asks for the intended union of three banked deltas, with `pe-checksum`",
             "last, and for a proof that the final diff contains exactly the banked changes and no",
             "additional behavioural bytes. This note is that proof, and the expectation file beside it",
             "is the machine-readable form of the same statement.",
             "",
             "## What each delta moves",
             ""]
    for name, detail in per_delta.items():
        lines.append("- `%s`: %d behavioural byte(s), candidate `%s`, ranges %s"
                     % (name, detail["positions"], detail["output_sha256"][:16],
                        ", ".join("%s..%s" % (s, e) for s, e in detail["ranges"])))
    lines += ["",
              "Sum and union:",
              "",
              "The sum of the three is %d positions and the union is %d, so the deltas are disjoint:"
              % (expectation["sum_of_components"], expectation["union_positions"]),
              "no byte is claimed twice and no byte arrives from nowhere.",
              ""]
    if overlapping:
        lines += ["Shared positions were found and are listed here, which is the case the plan asks to",
                  "be recorded rather than hidden:", ""]
        lines += ["- `%s` and `%s` share %d position(s)" % trio for trio in overlapping]
        lines.append("")
    else:
        lines += ["No two banked deltas share a single behavioural position, so an isolated verdict",
                  "was possible for each of them and a combination of the three adds exactly three",
                  "changes rather than one new region of uncertainty.", ""]
    lines += ["## Why the ranges are recomputed rather than copied",
              "",
              "Two of the banked expectations record `diff_mode: exact` and one records `within`,",
              "because a 60574-byte donor replaces a region and only some of those positions differ",
              "from the base. A union of recorded ranges is therefore a superset of the truth, and a",
              "superset is the wrong basis for a claim about additional bytes. Each delta was",
              "re-derived from its own manifest and expectation here, its behavioural positions read",
              "off the bytes, and the combined expectation names those positions.",
              "",
              "## The result",
              "",
              "- base `%s` (%d bytes)" % (expectation["base_sha256"][:16], len(base)),
              "- combined candidate `%s` (%d bytes, same size as the base)"
              % (expectation["output_sha256"][:16], len(out)),
              "- `%d` behavioural byte(s) across `%d` range(s), the PE checksum field excluded"
              % (expectation["changed_bytes"], len(spans)),
              "- ranges: %s" % ", ".join("`0x%x`..`0x%x`" % (s, e) for s, e in spans),
              "",
              "Grade it with the milestone's own command:",
              "",
              "```",
              "./graft-check.py --base work/stageE/ati2mtag-s5_accessor_blob.sys \\",
              "  --manifest fixtures/graft/manifest-combined-deploy.json \\",
              "  --expect fixtures/graft/combined-deploy.expect.json \\",
              "  --notes work/graft-manifest.md --dry-run --require-deployable",
              "```",
              ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
