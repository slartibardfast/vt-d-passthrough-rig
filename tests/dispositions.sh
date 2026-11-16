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

test_pe_checksum_recomputed() {
    # exercises apply_manifest,recompute_pe_checksum - the op writes the amd-form checksum
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_pe_checksum_recomputed -q
}

test_pe_checksum_changes_on_corruption() {
    # exercises pe_checksum - the checksum binds the image content
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_pe_checksum_changes_on_corruption -q
}

test_pe_checksum_idempotent() {
    # exercises recompute_pe_checksum - the op is idempotent
    "$VENV_PYTHON" -m pytest tests/test_graft.py::test_pe_checksum_idempotent -q
}

# --- specs/kdwire.allium and specs/walk.allium (the wire and the walk) ---
# Generated from the two manifests: one runner per discharging test, each running
# the pytest node named in its body.

test_a_block_walk_advances_by_the_bytes_actually_returned() {
    # exercises read_block - a block walk advances by the bytes actually returned
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_block_walk_advances_by_the_bytes_actually_returned -q
}

test_a_captor_catch_is_written_even_when_the_module_is_unknown() {
    # exercises captor_catch - a captor catch is written even when the module is unknown
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_captor_catch_is_written_even_when_the_module_is_unknown -q
}

test_a_closed_link_is_reported_as_lost_and_redialled() {
    # exercises connect_wait,link - A run's collector is the only witness of its boot, so the link is rebuilt, not restarted
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_closed_link_is_reported_as_lost_and_redialled -q
}

test_a_control_packet_carries_no_checksum_and_no_payload() {
    # exercises CTRL_LEADER,send_control - a control packet carries no checksum and no payload
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_control_packet_carries_no_checksum_and_no_payload -q
}

test_a_data_packet_carries_the_measured_header_and_trailer() {
    # exercises DATA_LEADER,send_data - a data packet carries the measured header and trailer
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_data_packet_carries_the_measured_header_and_trailer -q
}

test_a_file_open_is_declined_with_the_documented_status() {
    # exercises FILE_OPEN_DECLINE - The collector has no debugger-file role, so the guest keeps its own bytes
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_file_open_is_declined_with_the_documented_status -q
}

test_a_frame_is_a_sixteen_byte_header_then_payload_then_trailer() {
    # exercises calcsize,read_packet - The size is not a detail: a collector that reads nine bytes loses the session
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_frame_is_a_sixteen_byte_header_then_payload_then_trailer -q
}

test_a_module_load_is_answered_with_a_resume_not_a_version_request() {
    # exercises T_STATE_CHANGE64,API_CONTINUE2,DBG_CONTINUE - The engine's fallback depends on this reply
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_module_load_is_answered_with_a_resume_not_a_version_request -q
}

test_a_module_load_is_recorded_with_its_base_and_name() {
    # exercises STATE_LOAD_SYMBOLS,parse_module_name - a module load is recorded with its base and name
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_module_load_is_recorded_with_its_base_and_name -q
}

test_a_read_is_clamped_to_the_length_the_engine_answers() {
    # exercises MAXRD,kd_read - Measured: the engine refuses a request longer than sixteen bytes, so the client never asks
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_read_is_clamped_to_the_length_the_engine_answers -q
}

test_a_read_reply_reports_what_arrived_and_clears_what_was_asked() {
    # exercises API_READ_VM,read-dump - The reply's own length field is the answer; the reader must not trust the request
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_read_reply_reports_what_arrived_and_clears_what_was_asked -q
}

test_a_read_that_arrives_is_not_retried_at_all() {
    # exercises kd_read - a read that arrives is not retried at all
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_read_that_arrives_is_not_retried_at_all -q
}

test_a_refusal_retransmits_the_same_packet_under_the_same_id() {
    # exercises T_RESEND,last_tx - A refusal is not a new request
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_refusal_retransmits_the_same_packet_under_the_same_id -q
}

test_a_reply_carries_the_id_of_the_packet_it_answers() {
    # exercises T_ACK,send_control - a reply carries the id of the packet it answers
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_reply_carries_the_id_of_the_packet_it_answers -q
}

test_a_reply_longer_than_its_count_is_discarded_not_trusted() {
    # exercises read_dump - a reply longer than its count is discarded not trusted
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_reply_longer_than_its_count_is_discarded_not_trusted -q
}

test_a_reset_restarts_both_id_counters() {
    # exercises INITIAL_PID,POST_RESET_PID,T_RESET - a reset restarts both id counters
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_a_reset_restarts_both_id_counters -q
}

test_a_stalled_walk_reports_where_it_stopped() {
    # exercises read_block - a stalled walk reports where it stopped
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_stalled_walk_reports_where_it_stopped -q
}

test_a_walk_of_exactly_the_limit_is_one_step() {
    # exercises read_block,BlockWalk - a walk of exactly the limit is one step
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_walk_of_exactly_the_limit_is_one_step -q
}

test_a_zero_length_read_is_never_issued() {
    # exercises read_block - a zero length read is never issued
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_a_zero_length_read_is_never_issued -q
}

test_an_answered_refusal_is_not_retried() {
    # exercises kd_read,_last_ret - The law that was dead: a refusal is an answer, so asking twice only burns the window
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_an_answered_refusal_is_not_retried -q
}

test_an_endpoint_too_long_to_bind_is_refused_before_the_boot() {
    # exercises MAX_ENDPOINT_PATH - Naming the limit is the point: the transport's own failure says nothing about length
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_an_endpoint_too_long_to_bind_is_refused_before_the_boot -q
}

test_an_exchange_this_spec_does_not_model_is_still_answered() {
    # exercises T_ACK - Refusing to answer an unknown exchange costs the run its only witness
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_an_exchange_this_spec_does_not_model_is_still_answered -q
}

test_an_unmodelled_state_change_holds_the_machine_and_still_acks() {
    # exercises HOLD_RELEASE,captor_catch,T_ACK,stale - The freeze is the feature, and the DRAIN-ACK LAW keeps running inside it
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_an_unmodelled_state_change_holds_the_machine_and_still_acks -q
}

test_an_unrequested_reply_is_still_answered() {
    # exercises T_MANIPULATE - A reply that arrives with nothing pending is still a packet the engine awaits
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_an_unrequested_reply_is_still_answered -q
}

test_bytes_before_a_leader_are_stepped_over() {
    # exercises read_packet - bytes before a leader are stepped over
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_bytes_before_a_leader_are_stepped_over -q
}

test_every_frame_on_the_wire_ends_with_the_trailer() {
    # exercises TRAILER - every frame on the wire ends with the trailer
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_every_frame_on_the_wire_ends_with_the_trailer -q
}

test_issuing_requests_never_advances_the_id() {
    # exercises kd_expected - The sender's half of the law: the id comes from the engine's expectation and stays there
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_issuing_requests_never_advances_the_id -q
}

test_no_request_asks_for_more_than_the_limit_across_a_whole_walk() {
    # exercises MAXRD - The clamp is read off the command file, so the real `kd_read` is what is under test here
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_no_request_asks_for_more_than_the_limit_across_a_whole_walk -q
}

test_only_an_ack_of_the_id_last_issued_flips_the_pair() {
    # exercises kd_expected,last_sent_id - only an ack of the id last issued flips the pair
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_only_an_ack_of_the_id_last_issued_flips_the_pair -q
}

test_printed_driver_text_is_captured_and_answered() {
    # exercises PRINT - The only route by which the driver's own assertion text reaches the host
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_printed_driver_text_is_captured_and_answered -q
}

test_silence_retriggers_the_read_exactly_once() {
    # exercises kd_read,Waiting - silence retriggers the read exactly once
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_silence_retriggers_the_read_exactly_once -q
}

test_the_checksum_is_the_full_sum_and_not_a_sixteen_bit_field() {
    # exercises send_data - The measured law: a 16-bit checksum passed for small payloads and broke every larger one
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_the_checksum_is_the_full_sum_and_not_a_sixteen_bit_field -q
}

test_the_collector_forwards_a_read_it_was_asked_for_and_the_reader_owns_the_clamp() {
    # exercises API_READ_PHYS,MAXRD - Where the read limit is enforced, stated once
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_the_collector_forwards_a_read_it_was_asked_for_and_the_reader_owns_the_clamp -q
}

test_the_first_kernel_load_sets_the_base_later_breaks_are_measured_against() {
    # exercises ntbase - the first kernel load sets the base later breaks are measured against
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_the_first_kernel_load_sets_the_base_later_breaks_are_measured_against -q
}

test_the_owner_of_an_address_is_the_highest_base_not_above_it() {
    # exercises resolve_addr - A first match over the ascending list lets a gap swallow a later module's range
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_the_owner_of_an_address_is_the_highest_base_not_above_it -q
}

test_the_reply_is_read_from_the_log_and_cut_to_what_arrived() {
    # exercises _wait_read - The collector writes the bytes and an `actual` count; the count is what bounds the read
    "$VENV_PYTHON" -m pytest tests/test_walk.py::test_the_reply_is_read_from_the_log_and_cut_to_what_arrived -q
}

test_two_requests_across_an_ack_carry_the_other_id() {
    # exercises kd_expected - THE ALTERNATION LAW, from the sender's side, which is what makes a retrigger worth sending
    "$VENV_PYTHON" -m pytest tests/test_kdwire.py::test_two_requests_across_an_ack_carry_the_other_id -q
}
