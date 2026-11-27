# Fidelity programme execution

Base 79cca81 on codex-handoff. Programme specification in NAMMASPACE_CODEX_MASTER_HANDOFF.md, amended by the user's binding corrections.

| Phase | Dependency and exit | State |
|---|---|---|
| P0 | Camera rows, private PLY, honest fit, unchanged SPZ | In progress on photo route |
| P1 | P0; manifests, splits, evaluator, private publishing, three complete scenes | Pending |
| P2 | P1; S1 readiness, S2 baselines, S3 seed spread | Intel graphics; training runtime absent |
| P2a | Independent; pinned licences, genuine AI inference and resource measurements | Pending |
| P3 | Independent; saved-still QA, coverage, native depth ingest, device A/B | Pending |
| X2/X3/X5/X6 and P4 | S3; recipe, BA, export, depth experiments and parity | Pending |
| P5 | P1/S3; two-store, context equivalence, controls, rejection and one-shot evaluation | Pending |
| P6 | P5; localisation, observability, rollback, multi-region | Pending |
| P7 | P5/P6; Studio, export, viewer benchmarks, large spaces | Pending |

Node baseline: node --test web/tests/*.test.js on v25.6.1: 42 passed, 8 skipped, 0 failed.
Known local input: Downloads/21_11_2026.zip exists. No transfer authorised.

Implementation checkpoint: photo and Polycam evidence routes, trainer sidecars and
stable IDs, manifest verification, publication guard, raw regional PSNR/SSIM,
time-block preparation for raw ARKit/LiDAR, corridor QA and the same-frame native
depth correction are implemented. The split-aware preparation currently requires a
fresh work directory, explicit reserved_splits path, and holdout_every=0. It does
not establish independent BA or final-test registration. Legacy pose solutions
are ineligible for fidelity evaluation. See docs/HANDOFF_DEVIATIONS.md.

Tests: initial Python collection failed in 5 modules. After collection repair
496 passed, 7 skipped, one failed in a mock rasterizer missing radii. Fixture
corrected. Golden SPZ comparison passed independently. Raw metric test passed.
QA execution results and command in docs/experiments/corridor-capture-qa.md.
Apple toolchain and CUDA runtime unavailable. Native depth change awaits device
verification. No pretrained inference, performance or restoration claims.

Next executable work: integrate split reservation before BA, register held-out
observations with a frozen model, finish input provenance for photo captures,
and verify full capture-to-checkpoint integration. P3 saved-still gating,
native archive ingestion and observability are pending. P2a licences and weight
hashes unverified. P5–P7 feature work and hardware experiments remain open.

Latest regression snapshot: 510 Python passed, 7 skipped (304.22 s).
Three subsequently added tests independently passed (native training, raw
metrics and the SPZ golden). Built the synthetic site locally; all 50 Node
tests then passed with zero skips. On Windows set OMP_NUM_THREADS=2 and
MKL_NUM_THREADS=2 for the mocked CPU trainer. See P3 report for the new native
CLI and saved-still switch. Photo checkpoints now inventory original and
training files, and record the trainer hash and git revision.

P1 progress on codex/fidelity-p1: train-only Polycam BA, optional native
training BA, fixed-reference development registration, private Gaussian
baseline runner, raw development rendering, exclusive final-registration claim.
See docs/phase_reports/P1.md and docs/experiments/corridor-training-ba.md.
Real CPU COLMAP verified a small reference and the synthetic held-out pose
path. Corridor development coverage was 0/2 on the small reference. The
verified synthetic pipeline uses mock Gaussian rendering and real COLMAP.
Next: add final scoring against the frozen protocol, expand development
overlap diagnostics, and execute the exact pinned GPU baseline on suitable
captures.
