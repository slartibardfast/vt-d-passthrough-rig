# Verification contracts

This rig separates source-level tests, real image transformations, hardware runs,
and operator acceptance. A green synthetic test run does not prove a real driver
boot.

## Local setup and tests

Create an isolated Python environment in the rig and run the complete suite:

```sh
python3 -m venv venv
./venv/bin/pip install hypothesis pytest
PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python -m pytest -p no:cacheprovider tests/ -q
```

Tests with the real proprietary base are marked with their own skip reason when
that input is absent. Their skip is not a real-image pass.

## Importing real evidence

`fixtures/graft/real-inputs.tsv` records the relative path, byte length, and
SHA-256 of every required real input. Copy the source tree layout to an
operator-controlled evidence directory, then run:

```sh
./evidence-import.sh --source /path/to/evidence-root
```

The source must contain `work/dumps/ati2mtag-live.sys`,
`work/stageE/ati2mtag-s5_accessor_blob.sys`, and `work/descriptor-read.md`.
The importer measures every source before replacing the corresponding ignored
file under this rig's `work/`. A missing or mismatched input exits 30 without a
verdict. No script searches the host for these files.

## Exact image checks

The historical descriptor dry-run is:

```sh
./graft-check.py \
  --base work/stageE/ati2mtag-s5_accessor_blob.sys \
  --manifest fixtures/graft/manifest-descriptor-redirect.json \
  --expect fixtures/graft/descriptor-redirect.expect.json \
  --notes work/descriptor-read.md \
  --dry-run
```

The deployable descriptor and SMC-only candidates are checked by:

```sh
./graft-check.py \
  --base work/stageE/ati2mtag-s5_accessor_blob.sys \
  --manifest fixtures/graft/manifest-descriptor-redirect-deploy.json \
  --expect fixtures/graft/descriptor-redirect-deploy.expect.json \
  --dry-run --require-deployable

./graft-check.py \
  --base work/stageE/ati2mtag-s5_accessor_blob.sys \
  --manifest fixtures/graft/manifest-smc-fill-deploy.json \
  --expect fixtures/graft/smc-fill-deploy.expect.json \
  --dry-run --require-deployable
```

An expectation binds the base and output hashes, image size, and behavioral diff.
`diff_mode: exact` requires the exact changed ranges. `diff_mode: within` confines
changes to the named ranges while the output hash binds every byte. A deployable
manifest has exactly one `pe-checksum`, in final position.

Dispatch and combined manifests are outputs of their research tasks. They do not
have placeholder expectations that could pass without the reverse engineering.

## Deployment evidence

With QEMU stopped, the previous destination saved as `rollback-ati2mtag.sys` in
the run directory, and the FAT volume mounted, run:

```sh
./graft-deploy.sh \
  --verify-mounted \
  --built work/runs/combined/ati2mtag.sys \
  --mount /mnt/gpudrv \
  --record work/runs/combined/deployment.json
```

The verifier requires the actual `drv/ati2mtag.sys` destination to equal the
built candidate. The rollback file must exist and is hashed into the record.
The script verifies only; it does not mount, copy, or repair a failed deployment.

## PowerPlay run record

Each instrument run directory contains `run.json`, `kdc-live.log`, and
`captor-catches.log`. `run.json` has this shape:

```json
{
  "candidate_sha256": "64 lowercase hexadecimal characters",
  "boot_id": "unique identifier for this boot",
  "session_ready": true,
  "abandoned": false,
  "desktop_reached": true,
  "manual_resume": false,
  "expected_captor": [],
  "expected_assert": null,
  "artifacts": {
    "kdc-live.log": "sha256 of that file",
    "captor-catches.log": "sha256 of that file"
  }
}
```

The hashes must be replaced with the actual artifact hashes. `expected_captor`
contains exact substrings for known non-PP catches. Unknown catches make the run
inconclusive.

A predeclared experimental catch sets `expected_assert` to its exact site or
fingerprint and `manual_resume` to true. `pp-events.json` then contains exactly
one event with `fingerprint`, `first_chance: true`, `second_chance: false`,
`resumed: true`, `context`, and `dump`. The context and dump names also need
entries in `artifacts`. Their files must be nonempty and hash-bound.

Run the final classifier with:

```sh
./graft-verify.sh --run work/runs/combined
```

Statuses and exit codes are:

| Status | Exit | Meaning |
|---|---:|---|
| `CLEAN` | 0 | Positive PP completion, no assert or manual resume, and complete evidence |
| `EXPECTED_EXPERIMENTAL` | 10 | One approved catch with complete evidence; not final acceptance |
| `ASSERT_FAIL` | 20 | Unexpected, repeated, or failed PP behavior |
| `INCONCLUSIVE` | 30 | Missing, stale, contradictory, or unbound evidence |

`fixtures/graft/pp-markers.json` deliberately has an empty `init_completed` list
until a positive marker is measured and reviewed. Consequently, a real boot with
no assert remains `INCONCLUSIVE` rather than becoming clean by absence of a bad
string.

`delta-verify.sh` accepts `EXPECTED_EXPERIMENTAL` for a single-delta research
record. It also checks the candidate and deployment hashes. Final
`graft-verify.sh` does not accept that status.

## Desktop and user evidence

A user run contains `evidence.json` and a hash-bound `desktop.png`. The desktop
check requires the ATI miniport and display-driver identity, a loaded and rendered
ATI desktop, physical-monitor agreement, and at least 1024x768:

```sh
./acceptance.sh desktop --run work/runs/desktop
```

The full user record additionally contains:

- `config_manager_error_code: 0`.
- Equal positive `vram_expected` and `vram_observed`, with `vram_source`.
- `keyboard_response: true` and `pointer_response: true`.
- `resolution_change` with distinct `before` and `after` pairs and
  `returned_to_known_good: true`.
- `acceleration` with a nonempty `probe` and `passed: true`.
- `soak_seconds` of at least 1800 and `developer_rollback_tested: true`.
- Hash-bound `proofs` named `input`, `resolution`, `acceleration`, `soak`, and
  `rollback`.

The warm-reboot run carries the same candidate, desktop, Code 0, VRAM, and basic
input evidence, plus `warm_reboot: true` and a hash-bound `warm_reboot` proof.
The tests in `tests/test_acceptance.py` contain a complete synthetic example.

Collect acceptance only after those records exist:

```sh
./acceptance.sh collect \
  --instrument work/runs/combined \
  --user work/runs/desktop \
  --reboot work/runs/reboot \
  --out work/acceptance
```

The collector reclassifies the raw instrument logs. Only `CLEAN` passes. It
requires a new output directory and writes `acceptance.json` plus `SHA256SUMS`.
The final research acceptance remains `verify: attested operator` in the governing
host. Packaging and end-user release acceptance belong to the successor milestone.
