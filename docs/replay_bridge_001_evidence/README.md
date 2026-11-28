# Replay Bridge 001 evidence (SYNTHETIC, paper only, decision support only)

Regenerated on 2026-11-28 (Europe/Berlin), after Doodle's revised price-freshness proposal (sha256 `805f2681...e246`, still PROPOSED), from the frozen Lesson 001 v3 archive (sha256 `159de19c...47a3`) with
the bridge source committed alongside it (its sha256 is `code_sha256` in `output/bridge_receipt.json`).

- `output/`: the bridge's own output folder, unchanged. Command (run offline under `unshare -rn`, from a folder
  holding the archive): `python -m optpipe.research.replay_bridge export Optpipe-Lesson-001-Portability-v3.zip output`.
  `output/SHA256SUMS` is the bridge's own checksum file (five files: the three CSVs, the accept log and the receipt).
- `export.log`: that export plus `verify output` (both exit 0).
- `exists.log`: the same export repeated into the existing `output` folder (refused, exit 2, nothing written).
- `tests.log`: `python -m pytest -p no:cacheprovider -o addopts="" -v tests/test_replay_bridge.py` with
  `LESSON001_BUNDLE` set to the archive, so the real-archive test ran too. Two machine paths in the header were
  replaced with `<venv>` and `<repo>` (repo hygiene forbids absolute paths); nothing else was edited.
- `EVIDENCE.SHA256SUMS`: sha256 of every other file in this folder.

Python 3.13.5. Results: 391 rows; final and upstream counts ABSTAIN 375, BLOCKED 15, SIGNAL_BULL 1, SIGNAL_BEAR 0;
`decision_tape.csv` sha256 `039ee588...8de24` (byte-identical to Sol's evidence tape); price block log 1 record
(row 0, 09:30, `PF-STATUS`, label BAR); price accept log 390 records, all `label=BAR`, age 0; all labeled rows and
log records carry PAPER_ONLY and DECISION_SUPPORT_ONLY_NOT_AN_ENTRY_OR_FILL. The frozen tape has no DELAYED prices;
the DELAYED class is covered by the tests in `tests.log` (PF-39 to PF-46 and extras), not by this run.
