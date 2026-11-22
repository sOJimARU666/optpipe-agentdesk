# claude_code/: Claude Code's contributions, kept apart until accepted

Paper only. **closure_claim: none.** Everything Claude Code adds lives in this folder, one subfolder per task, so
the existing system stays exactly as it is until the owners decide otherwise:

* Nothing under `src/`, `spec/`, `scripts/`, `tests/`, `config/`, `.github/`, `pyproject.toml` or the root `README.md` changes.
* Nothing in `optpipe` imports this folder. The main suite (`pytest`, `testpaths = ["tests"]`) and CI do not collect it, so
  their test counts and steps stay as on `main`.
* Still covered: `tests/test_repo_hygiene.py` scans every tracked file, including these. `test_folder_rules.py` runs the
  shared paper-only scanner from `tests/test_paper_only.py` over every `.py` here, because that scanner covers only
  `src/` and `scripts/`.
* `conftest.py` applies the main suite's hermetic rules here: pinned `spec/`, the runtime network guard (this process and
  every subprocess), and temp state and cache dirs.
* Adopting a tool means moving it into `scripts/` and `tests/` in a normal PR, with Moodle's and Doodle's say on placement.

```bash
.venv/bin/python -m pytest -p no:cacheprovider claude_code        # this folder's tests only
```

| Task | What | Status |
|---|---|---|
| [CC-001](CC-001/RECORD.md) | `CC-001/check_fixture_repro.py`: the README's offline demo (`run`, `validate`, `watch --replay`) in a TZ x PYTHONHASHSEED matrix, with hash-chain and UTF-8/LF checks per cell and a cross-machine fingerprint (`--expect`) | Implemented, PR #38, awaiting Zoodle and Noodle |

## CC-001 quick start

```bash
.venv/bin/python claude_code/CC-001/check_fixture_repro.py --receipt receipt.json   # exit 0 PASS, 1 FAIL, 2 cannot run
.venv/bin/python claude_code/CC-001/check_fixture_repro.py \
    --expect 42d76be8ab8bd78e814956dcdd867ddcdf3d72ed846965406cead6874a96557e      # reproduce the Linux reference
```

Each cell runs `optpipe run --fixtures`, `optpipe validate` and `optpipe watch --replay` as separate processes, with its
own temp `out/`, state and cache dirs. It reads the pinned `spec/`, or `$OPTIONS_SPEC_DIR` if set, and refuses (exit 2) a
spec folder that fails the pin. The checks:

* C0: the requested TZ and seed were really applied.
* C1: exit codes.
* C2-C6: the hash chain, with optpipe's own functions.
* C7: UTF-8 and LF line endings.
* R1: outputs identical across cells once only the wall-clock fields are masked. Their digest is the fingerprint.

The JSON receipt has no absolute paths. On a platform without `time.tzset` (Windows), the TZ axis is not exercised and
the receipt says so. See [CC-001/RECORD.md](CC-001/RECORD.md) for the evidence.
