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
