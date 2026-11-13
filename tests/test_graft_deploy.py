"""Mounted deployment checks without mounting or hardware."""

import json
from pathlib import Path

import pytest

import graft_deploy


def fixture(tmp_path):
    mount = tmp_path / "mount"
    (mount / "drv").mkdir(parents=True)
    built = tmp_path / "built.sys"
    deployed = mount / "drv" / "ati2mtag.sys"
    rollback = tmp_path / "rollback.sys"
    built.write_bytes(b"candidate")
    deployed.write_bytes(b"candidate")
    rollback.write_bytes(b"previous")
    return built, mount, rollback, deployed


def test_hash_match_records_deployment_and_rollback(tmp_path):
    built, mount, rollback, _ = fixture(tmp_path)
    record = graft_deploy.verify_files(built, mount, rollback)
    assert record["built_sha256"] == record["deployed_sha256"]
    assert record["rollback_sha256"] != record["deployed_sha256"]
    assert record["destination"] == "drv/ati2mtag.sys"


def test_hash_mismatch_fails(tmp_path):
    built, mount, rollback, deployed = fixture(tmp_path)
    deployed.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="differs"):
        graft_deploy.verify_files(built, mount, rollback)


def test_missing_rollback_fails(tmp_path):
    built, mount, rollback, _ = fixture(tmp_path)
    rollback.unlink()
    with pytest.raises(ValueError, match="regular file"):
        graft_deploy.verify_files(built, mount, rollback)
