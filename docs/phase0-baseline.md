# Phase 0 Baseline Notes (2026-04-20)

## Command
- `python -m unittest discover -s tests -q`

## Result
- Existing suite is not fully green before refactor.
- Failures observed in `tests/test_quality_service.py`:
  - `test_accepts_clear_well_exposed_face`
  - `test_debug_summary_contains_primary_issue_and_component_scores`
  - `test_missing_landmarks_are_not_treated_as_ideal`
  - `test_uses_landmarks_for_pose_and_truncation`

## Interpretation
- Baseline quality-threshold expectations are currently stricter than actual scorer outputs for synthetic fixtures.
- These are pre-existing behavioral mismatches and should be handled as a dedicated quality-service calibration task.
