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
