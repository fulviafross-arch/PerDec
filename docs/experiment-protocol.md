# Experiment protocol and acceptance

Short integration acceptance uses warmup 1 s, measurement 5 s, sample interval
16 ms, and one repetition. Timeout is 30 s startup allowance + warmup +
measurement + 30 s exit allowance. The runner passes `--pg-quit-on-complete true`
and does not use `-batchmode` or `-nographics` by default.

Eligibility requires self-exit before timeout with exit code 0, all four non-empty
artifacts, a completed manifest without failure, a final `run_completed` event,
valid frames covering at least 80% of the configured measurement duration, and no
unhandled exception, crash, or collector-startup failure evidence in `player.log`.

CPU, GPU, and GC may be entirely null in the MVP but reports must flag that fact.
Test001 is a cancelled manual-stop example only, never a baseline or auto-exit proof.

## Single-run diagnostics

One eligible run is sufficient for a Phase 3 diagnostic report; repetition is
not a prerequisite. `--run-id` selects one exact eligible run, while `--latest`
selects the eligible run with the greatest valid UTC `completed_at` value from
its Runner report (ties use lexicographically greatest `run_id`). The selection
rule is recorded in `analysis.json`.

Incident detection is experimental. It combines the run median and MAD, the
configured or display-derived frame budget, a 50 ms absolute long-frame ceiling,
and merges candidates separated by at most one normal frame. CPU/GPU attribution
requires the corresponding timing to cover at least 80% of the worst frame;
otherwise the result is conservatively `unattributed`. Marker values can overlap
and must not be summed as CPU total time.

Memory trends shorter than 30 seconds are `insufficient_duration`. Longer-run
`growth_candidate` is a prompt for controlled follow-up, never a leak claim.
Allocation evidence includes Collector overhead where declared by the capture.
