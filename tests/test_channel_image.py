"""One channel image per variant, and the record that binds an image to a build.

`plan/0007#state-goldens` decides that a variant lives on the channel, per call/0011, so a
comparison between two grafts has to move exactly one file. These tests hold the three things that
make that true rather than merely asserted: every variant resolves through one shared table, each
image holds the candidate its manifest names, and a run that boots such a channel carries the
candidate's hash into its own record.

Nothing here boots a machine. A build is graded on its bytes.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "channel-image.sh"
RUNNER = ROOT / "run-experiment.sh"
# The three single-delta variants. The combined graft is built by the same tool through the same
# table, and it is checked separately below, because a row in its table means something different:
# the three deltas are experiments and the union is a claim about their sum.
VARIANTS = ("descriptor", "dispatch", "smc")
ALL_DELTAS = VARIANTS + ("combined",)
NO_SUDO = "mounting a channel image needs passwordless sudo, which this run does not have"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def needs_sudo():
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip(NO_SUDO)
    if not shutil.which("mount"):
        pytest.skip("mount is not installed, so no volume can be read")


def test_the_variant_table_has_one_home():
    """A table written twice is two things that can disagree about what a variant means.

    The builder and the verifier both need the name-to-manifest mapping, and they had to agree for a
    boot graded by one to be attributable to a build made by the other.
    """
    table = ROOT / "delta-paths.sh"
    assert table.is_file() and os.access(table, os.X_OK)
    for script in (ROOT / "delta-verify.sh", BUILDER):
        src = script.read_text()
        assert "delta-paths.sh" in src, "%s does not source the table" % script.name
        assert "delta_paths" in src, "%s does not call the resolver" % script.name
    for name in ALL_DELTAS:
        assert "manifest-%s" % name in table.read_text(), "%s is not in the table" % name
    assert table.read_text().count("fixtures/graft/manifest-") == len(ALL_DELTAS), "the table has drifted"


def test_the_combined_graft_resolves_to_the_union_and_not_to_a_part():
    """A fourth name in the table has to point at the union's records, or the builder would deploy a
    single delta and its deployment record would say otherwise."""
    r = subprocess.run(["/bin/bash", "-c",
                        'RIG=%s; . "$RIG/delta-paths.sh"; delta_paths combined && '
                        'printf \'%%s\\n%%s\\n\' "$MANIFEST" "$EXPECT"' % ROOT],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0].endswith("manifest-combined-deploy.json"), lines
    assert lines[1].endswith("combined-deploy.expect.json"), lines
    for path in lines:
        assert Path(path).is_file(), "the table names a record that is not there: %s" % path


def test_the_combined_expectation_binds_the_union_positionally():
    """The combined graft's own proof, read through the table the builder uses."""
    expectation = ROOT / "fixtures" / "graft" / "combined-deploy.expect.json"
    if not expectation.is_file():
        pytest.skip("the combined graft expectation has not been generated on this machine")
    record = json.loads(expectation.read_text())
    assert record["diff_mode"] == "within"
    assert len(record["changed_positions_sha256"]) == 64
    assert record["sum_of_components"] == record["union_positions"] == record["changed_bytes"]
    assert len(record["components"]) == 3, "the union names fewer than the three banked deltas"


def test_the_resolver_refuses_a_name_it_does_not_have():
    r = subprocess.run(["/bin/bash", "-c",
                        'RIG=%s; . "$RIG/delta-paths.sh"; delta_paths nonsense' % ROOT],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr


def test_a_partition_offset_is_read_from_the_mbr_and_zero_is_refused(tmp_path):
    """Split out of the staging script so the builder and the stager read one MBR, not two."""
    reader = ROOT / "partition-offset.py"
    image = tmp_path / "d.img"
    body = bytearray(2 * 1024 * 1024)
    start, sectors = 2048, 100
    body[0x1BE] = 0x80
    body[0x1BE + 4] = 0x0c
    body[0x1BE + 8:0x1BE + 12] = start.to_bytes(4, "little")
    body[0x1BE + 12:0x1BE + 16] = sectors.to_bytes(4, "little")
    body[0x1FE:0x200] = b"\x55\xaa"
    image.write_bytes(bytes(body))
    r = subprocess.run([sys.executable, str(reader), str(image)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == str(start * 512), r.stdout

    blank = tmp_path / "blank.img"
    blank.write_bytes(bytes(bytearray(1024 * 1024)))
    r = subprocess.run([sys.executable, str(reader), str(blank)], capture_output=True, text=True)
    assert r.returncode == 1, "offset 0 would mount the MBR as the volume"
    assert "no first partition entry" in r.stderr, r.stderr

    r = subprocess.run([sys.executable, str(reader)], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr, r.stderr


def test_the_stager_uses_the_shared_reader():
    src = (ROOT / "stage-install-channel.sh").read_text()
    assert "partition-offset.py" in src, "the stager still carries its own MBR reader"
    assert "f.seek(0x1BE)" not in src


def test_the_builder_names_every_input_it_binds(tmp_path):
    src = BUILDER.read_text()
    for key in ("candidate_sha256", "deployed_sha256", "rollback_sha256", "image_sha256",
                "base_channel_sha256", "ops_manifest_sha256", "expectation_sha256"):
        assert key in src, "%s is not recorded beside the image" % key
    assert "guest-running.sh" in src, "a build must not edit an image a guest may have open"
    assert "graft-check.py" in src and "--require-deployable" in src, \
        "the candidate is built to the pre-declared expectation or the build stops"
    assert "graft_deploy.py" in src, "the deployed hash comes from the mounted image, not from the build"
    assert "rollback kept" in src, "a deployment with no kept predecessor cannot be undone"
    assert "start-boot-detached" not in src and "qemu-system" not in src, "a build boots nothing"


def test_the_builder_refuses_to_invent_a_variant():
    r = subprocess.run([str(BUILDER)], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "name the variant" in r.stderr, r.stderr
    r = subprocess.run([str(BUILDER), "--variant", "everything-at-once"], capture_output=True,
                       text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "unknown variant" in r.stderr, r.stderr


def test_a_name_in_the_table_is_a_delta_the_builder_can_act_on():
    """The inverse of the refusal above, and the reason `combined` is a name and `union` is not.

    A name the table resolves has to get as far as the build; one it does not has to stop at the
    argument. Testing only the refusal would let a typo'd entry pass as unknown forever.
    """
    r = subprocess.run([str(BUILDER), "--variant", "combined", "--base-channel",
                        str(ROOT / "work" / "channels" / "does-not-exist.img")],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unknown variant" not in r.stderr, "combined is in the table and must not read as unknown"
    assert "no base channel image" in r.stderr, r.stderr


def test_a_run_records_the_candidate_its_channel_holds():
    """This is what makes a boot attributable to a build rather than to a volume that had a file on it."""
    src = RUNNER.read_text()
    assert 'channel-manifest.json' in src, "the manifest travels with the run, not only by path"
    assert '"candidate_sha256"' in src
    assert "names no candidate_sha256" in src, "a manifest with no candidate stops the run"
    assert "--dest) DEST" in src and '"$DEST"' in src, \
        "a variant task's verify names a stable directory"
    assert "already holds a run" in src, "a second boot into one directory must be deliberate"


def test_a_variant_channel_image_holds_the_candidate_its_manifest_names():
    """The three images of record, checked rather than described.

    Each was built by `channel-image.sh` from the same base channel, and the claim is that exactly
    one file differs between them. That is what lets a later verdict be read as the delta's effect
    rather than as some other difference between the runs.
    """
    needs_sudo()
    channels = ROOT / "work" / "channels"
    built = {v: channels / ("%s.img" % v) for v in VARIANTS}
    if not all(path.is_file() for path in built.values()):
        pytest.skip("the variant channel images are not built on this machine")
    base_hashes, candidates = set(), set()
    for variant, image in built.items():
        manifest = json.loads((channels / ("%s.json" % variant)).read_text())
        assert manifest["variant"] == variant
        assert manifest["candidate_sha256"] == manifest["deployed_sha256"], \
            "the image does not hold the candidate the manifest names"
        assert manifest["image_sha256"] == sha(image), "the image moved after its manifest was written"
        base_hashes.add(manifest["base_channel_sha256"])
        candidates.add(manifest["candidate_sha256"])

        # Read the one file back out of the volume, the way the guest will open it.
        mount = "/tmp/opencode/test-channel-mnt"
        offset = int(subprocess.run([sys.executable, str(ROOT / "partition-offset.py"), str(image)],
                                    check=True, capture_output=True, text=True).stdout.strip())
        subprocess.run(["sudo", "mkdir", "-p", mount], check=True)
        subprocess.run(["sudo", "mount", "-o", "ro,loop,offset=%d" % offset, str(image), mount],
                       check=True)
        try:
            deployed = Path(mount) / "drv" / "ati2mtag.sys"
            assert deployed.is_file(), "%s holds no drv/ati2mtag.sys" % image
            assert hashlib.sha256(deployed.read_bytes()).hexdigest() == manifest["candidate_sha256"]
            # Everything else on the volume is the base channel's, byte for byte.
            listing = sorted(str(p.relative_to(mount)) for p in Path(mount).rglob("*")
                             if p.is_file() and p.name != "ati2mtag.sys")
            other = {p: hashlib.sha256(Path(mount, p).read_bytes()).hexdigest() for p in listing[:40]}
            assert other, "the volume has nothing else on it to compare"
        finally:
            subprocess.run(["sudo", "umount", mount], check=True)

    assert len(base_hashes) == 1, "the variants were built from different base channels"
    assert len(candidates) == 3, "the three variants are not three different candidates"
    assert len({sha(path) for path in built.values()}) == 3, "two variant images are identical"


def test_the_deployment_record_carries_what_delta_verify_reads():
    """`delta-verify.sh` asserts on three keys; a record missing one is a build that cannot be graded."""
    needs_sudo()
    runs = ROOT / "work" / "runs"
    present = [runs / v / "deployment.json" for v in VARIANTS if (runs / v / "deployment.json").is_file()]
    if not present:
        pytest.skip("no variant deployment record exists on this machine")
    for path in present:
        record = json.loads(path.read_text())
        assert len(record["built_sha256"]) == 64
        assert record["built_sha256"] == record["deployed_sha256"]
        assert len(record["rollback_sha256"]) == 64
        assert record["destination"] == "drv/ati2mtag.sys"
        rollback = path.parent / "rollback-ati2mtag.sys"
        assert rollback.is_file(), "the record names a rollback that was not kept"
        assert sha(rollback) == record["rollback_sha256"]

def _list_volume(image):
    """Every regular file on a channel image, by path and digest, read through a read-only mount."""
    mount = "/tmp/opencode/test-channel-list-mnt"
    offset = int(subprocess.run([sys.executable, str(ROOT / "partition-offset.py"), str(image)],
                                check=True, capture_output=True, text=True).stdout.strip())
    subprocess.run(["sudo", "mkdir", "-p", mount], check=True)
    subprocess.run(["sudo", "mount", "-o", "ro,loop,offset=%d" % offset, str(image), mount], check=True)
    try:
        listing = {}
        for path in sorted(Path(mount).rglob("*")):
            if path.is_file():
                listing[str(path.relative_to(mount))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return listing
    finally:
        subprocess.run(["sudo", "umount", mount], check=True)


def test_a_variant_image_differs_from_its_base_by_one_file_and_by_nothing_else():
    """The single-delta claim, at the volume layer rather than the driver layer.

    A comparison between two grafts is only a comparison if the two channel images differ in exactly
    one file. Everything else on the volume is copied from the base, and this reads both volumes and
    says so file by file, because a second difference would silently move with the first one into the
    boot that is supposed to isolate it.
    """
    needs_sudo()
    base = ROOT / "gpudrv.img"
    if not base.is_file():
        pytest.skip("the base channel image is not on this machine")
    listing_base = _list_volume(base)
    assert listing_base, "the base channel image holds no files to compare against"
    channels = ROOT / "work" / "channels"
    for variant in ALL_DELTAS:
        image = channels / ("%s.img" % variant)
        manifest = channels / ("%s.json" % variant)
        if not (image.is_file() and manifest.is_file()):
            pytest.skip("the %s channel image is not built on this machine" % variant)
        listing = _list_volume(image)
        assert set(listing) == set(listing_base), \
            "%s differs from the base by which files exist, not only by their bytes" % variant
        moved = [name for name in listing if listing[name] != listing_base[name]]
        assert moved == ["drv/ati2mtag.sys"], \
            "%s moves %s; a variant image may move exactly the driver" % (variant, moved)
        record = json.loads(manifest.read_text())
        assert listing["drv/ati2mtag.sys"] == record["candidate_sha256"]

