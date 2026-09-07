# P1-A domain foundation verification

日付: 2026-09-07
対象: FEBio CAE Harness V2 の P1-A（canonical JSON、単位、EvidenceRef）
判定範囲: CT-01の共通domain基礎、local test、静的ゲート、wheel配布、installed import smoke

この記録は合成・ローカル証拠であり、実FEBio、公式FBS、FEBio Studio、Gmsh、LLM、実モデルの成功証拠ではない。P1全体（ケース登録、質問世代、版、状態、原子的保存）を完了扱いにせず、今回固定した共通domain基礎だけを対象にする。設計上、物理的意味は根拠から解決し、geometryや慣例から推測しない。

## 固定したGit境界

| 項目 | 値 |
|---|---|
| 作業ブランチ | `codex/p1-a-domain-foundation` |
| P1-A base | `47cc20d1e05aa9bb1a3e039a39490813c98d1bb7` |
| test-only SHA | `f48780e8d2399d23780271a184ad45bd5b39f94a` |
| production SHA | `27fe86b9e60c37e92b1773c97735696dfa25dcfb` |
| final code candidate SHA | `bf82ebc4a734ad179af8362fe8da4a7a560458e5` |
| remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote状態 | `REMOTE_CONFIGURED` |
| `origin/V2` observed SHA | `47cc20d1e05aa9bb1a3e039a39490813c98d1bb7` |
| V2へのpush | 未実施 |

`f48780e` は`tests/unit/contracts/{test_canonical.py,test_units.py,test_evidence.py}`だけを追加したtest-only commitで、parentは`47cc20d`である。`27fe86b` はそのテストを満たすdomain実装4ファイルだけを追加したproduction commitである。初回の静的ゲートで見つかったRuff/mypy上の不備を、`bf82ebc`でdomain 4ファイルと契約テスト2ファイルへ限定して修正した。最終candidateの作業treeはcleanである。

## RED / GREEN

実行インタープリターは `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`、Python `3.12.10` である。runnerは各実行について展開済みargv、cwd、Python、HEAD、dirty state、UTC start/end、exit code、stdout/stderrの絶対パスを保存した。

### test-only RED

test-only SHA `f48780e8d2399d23780271a184ad45bd5b39f94a`で、basetempの事前作成・書込み確認を`P1-A-red-basetemp-preflight-01`（exit 0）で行った後、次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-A-red-01
```

結果は `26 collected / 3 failed / 23 skipped / exit 1`。3件はcanonical、evidence、units各API availability assertionの`AssertionError`で、collection error、import setup error、`KeyError`、`AttributeError`はない。rawは `.local/coordination/runs/P1-A-red-01/{metadata.json,stdout.bin,stderr.bin}` にあり、HEADは前後ともtest-only SHA、dirtyは前後とも空である。

### focused GREEN

production SHA `27fe86b9e60c37e92b1773c97735696dfa25dcfb`で、basetemp事前確認を`P1-A-green-basetemp-preflight-01`（exit 0）で行った後、次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-A-green-01
```

結果は `26 passed / exit 0`。rawは `.local/coordination/runs/P1-A-green-01/{metadata.json,stdout.bin,stderr.bin}` にある。静的修正後にも`P1-A-fix-green-02`で `26 passed / exit 0`、format/lint/mypyを各exit 0で確認し、その結果を`bf82ebc`へcommitした。

初回candidateの`P1-A-lint-02`はexit 1（import/`__all__` ordering、Python 3.12 type alias）、`P1-A-fix-mypy-01`もexit 1（canonicalのtype narrowingとoptional module narrowing）だった。これらは合格証拠に数えず、rawを保持したまま修正後のfinal candidateで全ゲートを再実行した。

## 実装した契約

- `src/febio_cae/domain/canonical.py` は、辞書キー順、明示されたunordered collection、明示されたunique-id collectionを単一のcanonical serializerで処理する。UTF-8、空白なし、有限数値、negative zeroの正規化、文字列キー、重複id検査を固定し、順序を持つ配列は暗黙に並べ替えない。
- `src/febio_cae/domain/units.py` はimmutableな`Dimension`、`UnitDefinition`、`Quantity`と明示的なregistryを提供する。`m/mm`、面積・体積、`s/ms`、`N`、`Pa/MPa`、dimensionlessを登録し、SI変換、dimension mismatch、未知unit、bool・非有限値を検査する。負値は物理的に必要なsigned valueとして許容し、未指定をゼロへ変換しない。
- `src/febio_cae/domain/evidence.py` はimmutableなschema `1` の`EvidenceRef`を提供する。source kindは`user_instruction`、`registered_document`、`registered_material`、`registered_test_condition`に限定し、非空参照、対象field形式、lowercase SHA-256 digestを検査する。LLMの確信度はsource kindに含めない。
- `src/febio_cae/domain/__init__.py` は上記共通契約を公開する。ケース世代、質問、revision、原子的保存、競合CASは今回の範囲外であり、後続P1の単一所有者契約で実装する。

## 必須ローカルゲート

全gateはfinal code candidate `bf82ebc4a734ad179af8362fe8da4a7a560458e5`で実行した。pytest basetempの事前作成・書込み確認は`P1-A-gate-pytest-basetemp-preflight-03`（exit 0）である。各recordの`head_before`/`head_after`はfinal candidate、dirtyは前後とも空である。

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P1-A-gate-pytest-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-A-gate-pytest-03` | `60 passed / exit 0` | `.local/coordination/runs/P1-A-gate-pytest-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-format-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | `24 files already formatted / exit 0` | `.local/coordination/runs/P1-A-format-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-lint-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | `All checks passed / exit 0` | `.local/coordination/runs/P1-A-lint-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-mypy-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | `Success, no issues, 15 source files / exit 0` | `.local/coordination/runs/P1-A-mypy-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-scanner-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | `PASS`, 26 filesystem/index files, 0 issues / exit 0 | `.local/coordination/runs/P1-A-scanner-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-build-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built / exit 0 | `.local/coordination/runs/P1-A-build-03/{metadata.json,stdout.bin,stderr.bin}` |

scanner raw JSONは`git_tracking.available=true`、`tracked_files=26`、`index_content_checked=26`、`excluded_tracked_files=0`、`issues=[]`、`status=PASS`を記録している。除外領域は`.git`、`.local`、cache、`__pycache__`、`dist`、`febio_cae.egg-info`である。

## wheel と installed smoke

buildで得たwheelは次のとおりである。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 46de6ee26aa76201a9091638ea7effbf9ea80269013d7a4c45d8d1ef23fdda5c
size: 15344 bytes
```

新規 `.local/venvs/P1-A-installed-02` にPython 3.12のvenvを作り、repo rootではない新規cwd `.local/verification/P1-A-installed-cwd-02`から、`--no-index --no-deps --force-reinstall`でwheelをnon-editable installした。preflightは`P1-A-installed-preflight-02`（exit 0）、venv作成は`P1-A-installed-venv-02`（exit 0）である。pip/CLI/importの全metadataはHEAD前後ともfinal candidate、dirty前後とも空である。

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P1-A-installed-wheel-hash-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -c "import hashlib; from pathlib import Path; p=Path(r'C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl'); print(p); print(hashlib.sha256(p.read_bytes()).hexdigest()); print(p.stat().st_size)"` | SHA/size above / exit 0 | `.local/coordination/runs/P1-A-installed-wheel-hash-02/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-installed-pip-02` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\venvs\P1-A-installed-02\Scripts\python.exe -m pip install --no-index --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl` | `Successfully installed febio-cae-0.1.0 / exit 0` | `.local/coordination/runs/P1-A-installed-pip-02/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-installed-cli-02` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\venvs\P1-A-installed-02\Scripts\febio-cae.exe --version` | `febio-cae 0.1.0 / exit 0` | `.local/coordination/runs/P1-A-installed-cli-02/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-A-installed-import-02` | venv Python `-I -c` import/version check from installed cwd | `isolated=1`, `PYTHONPATH=None`, `PYTHONHOME=None`, site-packages import, metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-A-installed-import-02/{metadata.json,stdout.bin,stderr.bin}` |

installed smokeはCLIの配布・import境界を確認するものであり、solver、FBS、Studio、解析入力生成、実モデル成功を意味しない。

## 未検証事項と次タスク

- P1のCaseDraft、IssuedQuestion、CaseRevision、状態遷移、atomic persistence、競合/CASは未実装・未検証である。P1-Aはdomain基礎だけである。
- 実FEBio、公式FBS、FEBio Studio、Gmsh、LLM/native/E2E、許可済み`02_CAE`、BottomFrame実モデルは未実施である。
- 単位registryは設計仕様の初期表示・SI単位集合を対象にした合成契約であり、実STEP宣言単位やsolver profileとの実機適合性は未検証である。
- canonical serializerは共通の正規化関数と明示的path policyを固定したが、後続の全revision/evidence manifestが同じ関数を使うことは未検証である。
- 独立Astra Mediumレビュー、PMによるreview済みclean commit列のV2統合、V2へのpushは未実施である。

次タスクは、PMがこのclean candidateと証拠文書を独立レビューへ渡し、レビューで受理されたcommitだけを`V2`へfast-forward統合することである。今回のlocal/synthetic evidenceを実CAE受入れへ昇格させない。
