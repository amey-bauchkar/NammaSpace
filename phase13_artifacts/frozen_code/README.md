# Frozen code for the held-out runs (pipeline 13.3.0)

The held-out runs (2026-10-11 19:14 – 23:41 IST) used the code frozen in `datasets/FROZEN_CONFIG.json`
(code SHA-256 `03816b3c…`). After the batch exactly one frozen file changed: `src/namma/studio/profiles.py`
(D13-14: Standard face cap 4 M, pipeline 13.3.1, docstring). `profiles_frozen_13.3.0.py` is that file as frozen;
substituting it reproduces the frozen code digest exactly (checked 2026-10-12).

To repeat a held-out run with the frozen configuration:

1. Keep a copy of the current `src/namma/studio/profiles.py`.
2. Copy `profiles_frozen_13.3.0.py` over `src/namma/studio/profiles.py`.
3. `python scripts/eval/freeze_config.py --verify` must print "frozen configuration matches".
4. `python scripts/eval/run_heldout.py --only <scene_id> --profiles <preview|standard>` (creates a NEW run; earlier runs stay).
5. Restore the copy from step 1.

Never run `freeze_config.py` without `--verify`: it would overwrite the freeze record.
