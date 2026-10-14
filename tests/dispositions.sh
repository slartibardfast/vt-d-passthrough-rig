# dispositions.sh -- the gate-facing test runners for specs/szdd.obligations.
# host-lifecycle's test resolver understands Rust (`fn name(`) and shell
# (`name() {`) definitions; the suite is Python, so each pytest node gets a
# real shell runner that invokes it. These RUN the tests - they are the same
# assertions, not a mirror.

VENV_PYTHON="./venv/bin/python"

test_reference_pair_byte_exact() {
    # exercises szdd_expand - the decode must match expand.exe byte-for-byte
    "$VENV_PYTHON" -m pytest tests/test_szdd.py::test_reference_pair_byte_exact -q
}

test_rejects_foreign_stream() {
    # exercises szdd_expand - foreign streams are rejected loudly, no partial output
    "$VENV_PYTHON" -m pytest "tests/test_szdd.py::test_rejects_foreign_stream" -q
}

test_roundtrip_identity() {
    # exercises szdd_compress,szdd_expand - compress then expand is identity
    "$VENV_PYTHON" -m pytest tests/test_szdd.py::test_roundtrip_identity -q
}

test_termination_on_fuzzed_streams() {
    # exercises szdd_expand - any signed stream terminates the decode
    "$VENV_PYTHON" -m pytest tests/test_szdd.py::test_termination_on_fuzzed_streams -q
}

test_format_constants() {
    # exercises WINDOW_SIZE,WINDOW_START,MIN_REFERENCE_LENGTH,MAX_REFERENCE_LENGTH,GROUP_ITEMS
    "$VENV_PYTHON" -m pytest tests/test_szdd.py::test_format_constants -q
}

test_header_is_fourteen_bytes() {
    # exercises szdd_compress,szdd_expand - the 14-byte header law
    "$VENV_PYTHON" -m pytest tests/test_szdd.py::test_header_is_fourteen_bytes -q
}

# --- specs/patch.allium (the graft builder) ---

test_committed_oracle_byte_exact() {
    # exercises apply_manifest - the builder's output equals the committed oracle
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_committed_oracle_byte_exact -q
}

test_donor_fill_byte_exact() {
    # exercises apply_manifest - the SMC slot law: donor bytes land byte-exact
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_donor_fill_byte_exact -q
}

test_size_preserved() {
    # exercises apply_manifest - a graft never resizes the image
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_size_preserved -q
}

test_size_preserved_fuzzed() {
    # exercises apply_manifest - size preservation over random bases and patches
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_size_preserved_fuzzed -q
}

test_rejects_range_outside_base() {
    # exercises apply_manifest - an op past the base is a loud rejection
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_rejects_range_outside_base -q
}

test_rejects_donor_range_outside() {
    # exercises apply_manifest - a fill past its donor is a loud rejection
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_rejects_donor_range_outside -q
}

test_rejects_empty_manifest() {
    # exercises apply_manifest - an empty manifest is rejected
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_rejects_empty_manifest -q
}

test_patch_applied() {
    # exercises apply_manifest - a patch writes its bytes at the offset
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_patch_applied -q
}

test_order_respected() {
    # exercises apply_manifest - ops apply in manifest order
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_order_respected -q
}

test_digest_binds() {
    # exercises apply_manifest,digest_of - the digest is sha256 of the output
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_digest_binds -q
}

test_cli_roundtrip() {
    # exercises main - the CLI end-to-end over the committed fixture
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_cli_roundtrip -q
}

test_rejects_unknown_op() {
    # exercises apply_manifest - an unknown op kind never completes the apply
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_rejects_unknown_op -q
}

test_no_state_carryover() {
    # exercises apply_manifest - a rejected call leaves no state behind
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_no_state_carryover -q
}
