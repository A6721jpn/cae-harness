# P0-B XPLT reader observation記録

日付: 2026-09-07
対象: P0-B `attempt-06` のFEBio 4.12.0 XPLTを、独立bounded readerで観測し、同じrunの直接text出力と照合した記録
調査開始base: `ff24fb02e71387a3cca32da0f2328eaadd9fd9a3`
ブランチ: `codex/p0-b-compatibility-research`
remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）
追跡変更: このMarkdownのみ。reader script、mutation copies、JSON結果はGit追跡外の`.local/verification/P0B-xplt-observation-01`に保存した。

## 1. Executive summary / 判定

固定した合成native runのXPLT `elastic-patch.xplt`（8966 bytes、SHA-256 `0B835386CF2606BFD408379DB9A99D2696307C62D321A13D11D9D3488F4094A4`）を、上流reader codeを使わない標準Pythonライブラリだけのbounded readerで解析した。readerは、FEBio identifier、root/header/dictionary、mesh、2 state、dictionaryに定義されたdisplacement・reaction forces・stressの値、各blockの境界とサイズを確認してexit `0`で受理した。

同じ`attempt-06`の直接text出力を別parserで読み、最終state `time=1`を照合した。XPLTはsingle-precision floatであり、反力のXPLT符号はこのrunのlog出力と逆だった。raw signed差は意図的に失敗として保存し、明示的に`negate_xplt_to_match_logfile`を適用した比較では次のとおり全thresholdを満たした。

| 項目 | XPLT–text比較 | threshold | 判定 |
|---|---:|---:|---|
| displacement最大絶対差 | `2.526212491824791e-13 m` | `1e-10 m` | `PASS` |
| reaction force最大絶対差（XPLTを反転） | `4.219358543111618e-11 N` | `1e-8 N` | `PASS` |
| stress最大絶対差 | `2.7038875032303622e-05 Pa` | `1e-4 Pa` | `PASS` |
| 反転前reaction force最大絶対差 | `3.327585754219359e-02 N` | `1e-8 N` | `FAIL`（符号差を保存） |

> **Inference**: この固定した合成native artifactについて、XPLTの必要なheader・dictionary・mesh・stateを独立に読み、直接textの最終値へ明示的な反力符号変換を適用すれば、指定threshold内で数値照合できる。

これは製品のXPLT adapter、圧縮XPLT、任意のFEBio version、FEBio Studio、FBS、実モデル、接触、剛体、またはBottomFrame real-model E2Eの合格を意味しない。`VERSION=0x35`、version-specific observed tags、圧縮有り経路は、別の互換性契約として扱う。

## 2. Context / 問題と意思決定

前段のnative elastic patchはprocess exit、normal termination、直接text、XPLTの生成を確認したが、XPLTが存在することだけでは、後処理がmesh・dictionary・state・必須変数を正しく読める証拠にならない。この調査の意思決定は、次の狭い問いに限定した。

1. 固定XPLTが公式Appendix Dのblock境界に従うか。
2. headerのversion/compression、dictionaryの型・format・変数名、meshのnode/domain/element region、stateのtime/dataをboundedに取り出せるか。
3. XPLTのdisplacement・reaction forces・stressを、同じrunの直接text出力と指定thresholdで照合できるか。
4. truncation、unknown tag、bad magic、unsupported version、nonzero compressionを黙って受理せず拒否できるか。

権威文書は[design v2](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)と[greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)であり、このreviewはそれらを置き換えない。

## 3. Scope, conditions, and assumptions / 範囲・条件・仮定

### 3.1 実行範囲

- 対象は`attempt-06`の合成elastic patchのみ。実部品、実STEP、`02_CAE`、credentials、GUI状態は使用していない。
- solverは前段runと同じFEBio 4.12.0。今回のtaskではsolverを再実行せず、固定したXPLT・直接text・attempt recordを読み取った。
- readerはこのworktreeのignored local probeであり、製品`src/`、`tests/`、authority docs、legacy code、upstream reader codeを変更・参照していない。
- readerには入力上限32 MiB、block上限10000、各blockのpayload上限16 MiB、finite float、親block境界、expected SHA-256を実装した。
- 正常入力ではexpected SHA-256を必須にした。mutationの構造拒否を観測する6回だけ、明示的な`--no-hash`でhash gateを越えて各構造検査へ到達させた。

### 3.2 Assumption

| 項目 | 仮定 | 置換・確認条件 |
|---|---|---|
| reader scope | `FEBio 4.12.0`のこのartifactで観測した`VERSION=0x35`を唯一のsupported versionとする | 別versionのXPLTと公式version契約を確認した時に拡張する |
| data order | dictionary itemの順序とstate data itemの順序を対応付ける | 複数region、array variable、surface variableを含む別XPLTで確認する |
| reaction sign | raw比較後に、XPLT反力を反転するとlogfile値へ対応すると扱う | 製品のoutput contractで符号規約を明記し、別load caseで再検証する |
| compression | `COMPRESSION=0`のみ実装し、非zeroはunsupportedとして拒否する | 公式に設定方法と解凍形式が確認でき、fresh native compressed runを取得した時に追加する |
| units | 前段runのSI契約を引き継ぐ | product profileのunit contractと等価unit runで確認する |

物理的な材料、荷重、支持、接触、ROIをgeometryやXPLTから推定していない。XPLT値の数値照合は、solverの物理的正しさや実部品の許容値を証明しない。

## 4. Method and evidence ledger / 方法と証拠台帳

raw evidence root:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-elastic-patch-01\attempt-06
```

bounded reader evidence root:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-xplt-observation-01
```

主要artifactのSHA-256は次のとおりである。

| artifact | bytes | SHA-256 |
|---|---:|---|
| `attempt-06\elastic-patch.xplt` | `8966` | `0B835386CF2606BFD408379DB9A99D2696307C62D321A13D11D9D3488F4094A4` |
| `attempt-06\node-data.txt` | `8594` | `872BE160D58897D40D6232D99F5F030AED88382192CC9C02874B4A27A8D845CD` |
| `attempt-06\element-data.txt` | `2950` | `EE8A253517F7C02BA1B20250DB390BDAD7150539B23BB7877931300B01348336` |
| `attempt-06\attempt-record.json` | `16552` | `39AE1F428FCA894F0E4F8826E3E1A84357CA98289C14F5CC43307A0B1559B46E` |
| ignored `bounded_xplt_probe.py` | — | `5772FC0F13AA52B8CD42E996CE7EC47630A6837A9A1B5DD931AA0B652BF535D7` |
| ignored `accepted-observation.json` | — | `C543ADD86BF1E7AC0BD41D7FAA84071A445B5D07A069ECAC05AC438EF4923ADD` |
| ignored `mutations\mutation-results.json` | — | `50778A8BB47D827B863C2802BEEF8DD924EA7588AA185EC88722FD60971947FD` |

| ID | Type | Claim / value | Applicability | Source / raw evidence | Report section |
|---|---|---|---|---|---|
| PUB-X1 | Published | fileはidentifierの後に`DWORD identifier`, `DWORD size`, payloadのblock hierarchyを持つ | FEBio User Manual 4.12 Appendix D.2.1/D.2.2 | [D.2.1](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.2.1.html), [D.2.2](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.2.2.html) | 5 |
| PUB-X2 | Published | root、mesh、stateのtagとheader/dictionary/state dataのtag | FEBio User Manual 4.12 Appendix D.3–D.5 | [D.3.1](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.3.1.html), [D.3.2](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.3.2.html), [D.5.2](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.5.2.html) | 5, 6 |
| PUB-X3 | Published | node coordinates、Tet10 element、node/domain dataの型・formatの定義 | FEBio User Manual 4.12 Appendix D.4 | [D.4.1](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.4.1.html), [D.4.2](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.4.2.html) | 5, 6 |
| CALC-X1 | Calculation | 固定XPLTのSHA、block bounds、counts、state valuesを独立parserで復号 | このtaskのraw artifactに限定 | `bounded_xplt_probe.py`, `accepted-observation.json` | 6, 7 |
| CALC-X2 | Calculation | `max(abs(xplt-text))`を各変数へ適用し、float32読出し差を評価 | 最終state、node/element IDと順序のshapeが一致する範囲 | accepted JSON | 7 |
| CALC-X3 | Calculation | mutationをhash gate後の構造検査へ送り、各process exitとerror codeを記録 | 6つのfresh Python process | `mutations/mutation-results.json` | 8 |
| INF-X1 | Inference | この固定synthetic artifactのXPLT read boundaryは、明示的な反力符号変換を含めて照合可能 | product-wide capabilityへ一般化しない | §6–§7 | 1, 9 |

### 4.1 Exact commands and exits / コマンドとexit

正常probeは次の1 processで実行した。

```text
python -X utf8 .local\verification\P0B-xplt-observation-01\bounded_xplt_probe.py \
  --xplt .local\verification\P0B-elastic-patch-01\attempt-06\elastic-patch.xplt \
  --node-text .local\verification\P0B-elastic-patch-01\attempt-06\node-data.txt \
  --element-text .local\verification\P0B-elastic-patch-01\attempt-06\element-data.txt \
  --json .local\verification\P0B-xplt-observation-01\accepted-observation.json
```

結果は`exit 0`、`accepted=true`、`blocks=137`である。mutationは同じscriptをmutation fileごとに新しいPython processで実行し、各回`--no-hash`、exit `2`とした。中断run、collection error、partial outputは合格件数に含めていない。

## 5. Published compatibility facts / 公開仕様との対応

公式Appendix D.2.2は、最初のDWORDがFEBio identifier `0x00464542`で、identifierにはsize fieldが続かず、その後にroot、mesh、複数stateのblockが続くと説明する。D.2.1は各blockをidentifier、byte-size、payloadの3 fieldとして定義する。今回のreaderはこの境界を最初の入口として使用した。

Appendix D.3.1はheaderのVERSION、COMPRESSION、AUTHOR、SOFTWAREを定義し、D.3.2はdictionary itemの型・format・nameを定義する。D.4.1/D.4.2はnode座標とelement connectivity、D.5.1/D.5.2はstate timeとstate dataを定義する。今回のreaderは、これらの公開tagに加え、実artifactで必要だったversion-specific observed tagを構造だけ検証した。

重要な版差は次のとおりである。

- 4.12 Appendix D.3.1本文は`VERSION`の現在値を`0x0008`と記載しているが、対象XPLTの`VERSION` payloadは`0x00000035`（decimal `53`）だった。
- 対象XPLTには、公式ページの表にない`0x01020007`（dictionary item後置64 bytes）、`0x01046000`系（element-set region）、`0x02010003`、`0x02030000`系（state ancillary chunks）が含まれた。
- これらのchunkはbytes・child boundary・known fixed layoutを確認したが、公式ページに根拠のない物理的・意味的ラベルを付けていない。readerは別のtagへ変異すると拒否する。
- `COMPRESSION=0`は確認した。公式文書は圧縮flagの存在を述べるが、圧縮形式自体はこの版のAppendix Dで説明していないため、nonzeroを解凍済みとして扱わない。

## 6. XPLT parse result / binary観測結果

### 6.1 Top-level bounds

offsetはfile先頭を`0x0000`とする。block endはheader 8 bytesを含むpayload後端である。

| block | offset | payload size | end | 内容 |
|---|---:|---:|---:|---|
| FEBio identifier | `0x0000` | — | `0x0004` | `0x00464542` |
| ROOT | `0x0004` | `658` | `0x029E` | header + dictionary |
| HEADER | `0x000C` | `62` | `0x0052` | version/compression/software/unit |
| DICTIONARY | `0x0052` | `580` | `0x029E` | node/domain descriptors |
| MESH | `0x029E` | `3544` | `0x107E` | nodes, domain, regions, part |
| STATE 0 | `0x107E` | `2364` | `0x19C2` | time `0` |
| STATE 1 | `0x19C2` | `2364` | `0x2306` | time `1` |

`0x2306 = 8966`でfile末尾と一致し、137 blocksを数えた。各blockのsizeは親境界とfile境界内にあり、leafのexpected byte countと一致した。

### 6.2 Header and dictionary

| field | decoded value |
|---|---|
| magic | `0x00464542` / `BEF\0` |
| VERSION | `0x35` / `53` |
| COMPRESSION | `0`（このreaderはnonzeroをreject） |
| SOFTWARE | `FEBio 4.12.0` |
| unit system | `SI` |
| AUTHOR | absent |

| category | name | type | format | expected values/state payload |
|---|---|---|---|---:|
| nodeset | `displacement` | `VEC3F` (3 float32) | `NODE` (0) | `63 × 3 = 189` / `756 bytes` |
| nodeset | `reaction forces` | `VEC3F` (3 float32) | `NODE` (0) | `63 × 3 = 189` / `756 bytes` |
| domain | `stress` | `MAT3FS` (6 float32: xx, yy, zz, xy, yz, xz) | `ITEM` (1) | `24 × 6 = 144` / `576 bytes` |

state dataの各DATA leafには8-byte observed prefixがあり、`<storage_code, uncompressed_bytes>`として記録した。node variablesは`<0,756>`、domain stressは`<1,576>`だった。prefixのapplication-level意味は断定せず、expected payload lengthの検証に使った。

### 6.3 Mesh

| 項目 | decoded value |
|---|---:|
| nodes | `63`, dimension `3`, coordinate IDs `1..63` |
| domain | `Patch`, `TET10`, element type `7`, part ID `1` |
| elements | `24`, each `10` connectivity entries |
| parts | ID `1`, name `elastic_patch` |
| element-set region | ID `1`, name `Patch`, `24` zero-based element indices |
| nodesets | ID1 `Patch` `63`; ID2 `all_nodes` `63`; ID3 `x0_nodes` `13`; ID4 `xL_nodes` `13` |
| node-set index range | ID1/2 `0..62`; ID3 `0..24`; ID4 `4..29` |

XPLT connectivityとnodeset indicesはzero-basedで保存されているため、direct textのone-based node/element IDsと比較する場合は`+1`を適用する境界を明記する。今回のstate valuesはnode section orderとdictionary orderをshape検査して対応付けた。

### 6.4 States and variables

| state | time | displacement | reaction forces | stress | auxiliary values |
|---:|---:|---:|---:|---:|---:|
| 0 | `0.0` | `189` values, all zero | `189` values, all zero | `144` values, all zero | 24 values, unique `{1}` |
| 1 | `1.0` | `189` values | `189` values | `144` values | 24 values, unique `{1}` |

state 0/1ともstate header auxiliary valueは`0`、node region IDsは順に`1`, `2`、domain stress region IDは`1`だった。auxiliary chunksは構造とcountのみ確認し、非可変data、rigid data、element activationなどの意味は推定していない。

## 7. Direct text comparison / 数値照合

### 7.1 Method and units

最終state `time=1`について、XPLT float32 valuesを変数ごとに3/6成分へ再構成し、直接textの以下の列と行IDで対応付けた。

```text
node-data.txt:    id,x,y,z,ux,uy,uz,Rx,Ry,Rz
element-data.txt: id,sx,sy,sz,sxy,syz,sxz
```

各比較は、

```text
e_max = max_i |value_xplt[i] - value_text[i]|
```

とした。displacementの単位は前段inputの`m`、reaction forceは`N`、stressは`Pa`である。text parserはXPLT parserとは別にheader、state、row width、finite値、ID uniqueness、63/24 row countを検査した。

### 7.2 Results

| variable | direct text states | final XPLT count | `e_max` | threshold | 判定 |
|---|---:|---:|---:|---:|---|
| displacement | 2 states × 63 rows | `189` | `2.526212491824791e-13 m` | `1e-10 m` | `PASS` |
| reaction forces, raw signed | 2 states × 63 rows | `189` | `3.327585754219359e-02 N` | `1e-8 N` | `FAIL`（符号差） |
| reaction forces, XPLT negated | 同上 | `189` | `4.219358543111618e-11 N` | `1e-8 N` | `PASS` |
| stress | 2 states × 24 rows | `144` | `2.7038875032303622e-05 Pa` | `1e-4 Pa` | `PASS` |

反力の符号差は、一部の代表値だけで決めず、全63 node・3成分のraw比較で発見した。xL node set（13 nodes）のx反力は、direct textが`-0.09982757252489077 N`、XPLT rawが`+0.09982757271995228 N`、XPLT反転後が`-0.09982757271995228 N`だった。これは符号変換後のXPLT–text差が約`1.95e-10 N`で、`1e-8 N`以内であることを補助的に示す。

stressの代表範囲は、XPLT `sxx=-997.677001953125 Pa`、direct text `sxx=-997.677028992 Pa`付近である。single-precision storageとtext側のdecimal出力差を含むため、完全なbyte equalityではなく、事前に定めた絶対thresholdで判定した。

> **Calculation**: `all_thresholds_pass=true`は、displacement、反転後reaction、stressの3つだけをthreshold gateへ入れた結果である。raw signed reactionは別diagnosticとして`pass=false`を保持しており、符号差を隠していない。

## 8. Mutation rejection / 変異・破損入力の拒否

mutation filesは元XPLTのcopyをbyte-levelに変更しただけで、実solverを再実行していない。各入力について新しいPython processを起動した。`--no-hash`は構造拒否を観測するためだけに使い、通常probeの固定SHA gateは維持した。

| mutation | 変更 | process exit | accepted | error code |
|---|---|---:|---|---|
| `truncate-one-byte.xplt` | file末尾1 byte削除 | `2` | `false` | `TRUNCATED_BLOCK_PAYLOAD` |
| `bad-magic.xplt` | first DWORDを`XEF\0`へ変更 | `2` | `false` | `BAD_MAGIC_OR_ENDIAN` |
| `unsupported-version.xplt` | VERSIONを`0x08`へ変更 | `2` | `false` | `UNSUPPORTED_VERSION` |
| `compression-one.xplt` | COMPRESSIONを`1`へ変更 | `2` | `false` | `UNSUPPORTED_COMPRESSION` |
| `unknown-root-tag.xplt` | root child tagを`0x01080000`へ変更 | `2` | `false` | `UNKNOWN_TAG` |
| `unknown-state-tag.xplt` | state auxiliary tagを`0x0203FF00`へ変更 | `2` | `false` | `UNKNOWN_TAG` |

truncateは最終state blockのpayload end `0x2306`がparent/file end `0x2305`を越えたことで拒否された。unknown tagはroot/stateの早い段階で拒否され、未知のpayloadを解釈しなかった。これは「壊れた入力を値が読める範囲だけで成功扱いしない」というbounded-reader観測であり、製品の正式security parser gateではない。

## 9. Recommendations / 推奨

1. **この固定synthetic profileのXPLT observationを、互換性matrixの部分証拠として登録する。** 根拠はexact SHA、bounds、dictionary/mesh/state counts、direct textとのfresh numeric comparison、6 mutation rejectionである。
2. **product XPLT contractには反力の符号規約を明文化する。** 今回はraw XPLTとdirect textが逆符号だったため、adapterでの暗黙反転は禁止し、source format、output channel、load case、全componentの証拠とともに明示する。
3. **`VERSION=0x35`の公式compatibility authorityを確認する。** 4.12 Appendix D.3.1の`0x0008`記載との差を未解決の版差として扱い、readerをversion-independentと宣言しない。
4. **nonzero compressionのfresh native artifactを取得してから実装する。** compression formatが公式に定義され、解凍後のmesh/stateと直接textを同じthresholdで照合できるまで、nonzeroはrejectのままにする。
5. **次の実施単位はproduct adapterへの隔離されたreader testである。** その際もこのlocal probeをproduct successの代用にせず、real FBS/FEBio/Studio/BottomFrame gatesを別証拠として収集する。

## 10. Verification plan / 残る検証計画

| 次の検証 | 入力 | 計測 | 合格基準 | 現状 |
|---|---|---|---|---|
| version authority | FEBio 4.12.0の公式binary database contractまたは対応release artifact | VERSION、tag table、reader policy | `0x35`の意味とsupported rangeを一次資料で確定 | 未実施 |
| compressed XPLT | official compression settingを理解したfresh native run | compressed header、解凍mesh/state、直接text | decode成功、counts一致、numeric threshold pass | 未実施 |
| multi-region variables | surface/global/array variableを含むsynthetic input | region ID、dictionary order、storage format | 各categoryのorder/shape/IDを独立照合 | 未実施 |
| Studio read confirmation | 採用XPLT | GUI起動、file load、state/variable表示 | captureとprocedureを保存 | 未実施 |
| product gate | CAE harnessのnative CLI | pytest、ruff、mypy、build、installed smoke | required local gatesをfreshに実行 | 未実施 |
| real E2E | authorized real FEBio/FBS/model route | authority、lease、drain、receipt、report | real official evidenceを各gateで確認 | 未実施 |
| final BottomFrame | authorized real BottomFrame model | full E2Eとrelease gates | fresh passing evidence | 未実施 |

## 11. Limitations and unresolved items / 限界と未解決

- これは固定した合成native XPLTのbounded observationであり、製品XPLT readerの実装・test・release gateではない。
- XPLTの`VERSION=0x35`は対象artifactで観測した値だが、4.12 Appendix D.3.1本文の`0x0008`と一致しない。readerはこの不一致を無視せず、0x35だけをsupportedとしている。
- `0x01020007`、`0x01046000`系、`0x02010003`、`0x02030000`系は、対象artifactで必要なobserved structuresとして境界・sizeを検証しただけであり、公式文書に基づく完全なsemantic decoderではない。
- COMPRESSION=0のみであり、圧縮ありXPLTを生成・解凍・照合していない。mutationでflag=1をrejectしたことはcompressed compatibilityの証拠ではない。
- reaction sign conversionは今回のdirect text/XPLT pairからのinferenceであり、全output channel・全load typeへ一般化していない。
- float32 XPLTとdecimal textの差はthreshold内だったが、solverの物理的正解、mesh convergence、material calibration、force equilibriumの正式acceptanceを証明しない。
- FEBio Studio、FBS、official FBS receipt、real model、real `02_CAE`、process ownership、descendant drain、timeout cleanup、final BottomFrame E2Eは未検証である。
- `pytest`、`ruff`、`mypy`、`build`、installed smoke、real E2E、release gateはこのdocs-only taskでは実行していない。未実行をpassとして数えていない。

したがって、この報告の最終判定は次である。

> `P0-B XPLT OBSERVATION RECORDED FOR ONE FEBio 4.12.0 SYNTHETIC ARTIFACT; SIGN-EXPLICIT DIRECT-TEXT COMPARISON PASSED; VERSION-SPECIFIC EXTENSIONS, COMPRESSION, PRODUCT/FBS/STUDIO/REAL-MODEL COMPATIBILITY UNVERIFIED`

## 12. References / 参考資料

1. [FEBio LLM CAE Harness design v2](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) — repository authority.
2. [FEBio CAE Harness greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md) — repository authority.
3. [FEBio User Manual 4.12, D.2.1 Overview](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.2.1.html) — abstract block hierarchy and endian independence.
4. [FEBio User Manual 4.12, D.2.2 Parsing the FEBio plot file](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.2.2.html) — identifier and top-level root/mesh/state layout.
5. [FEBio User Manual 4.12, D.3.1 Header Section](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.3.1.html) — header tags and compression flag.
6. [FEBio User Manual 4.12, D.3.2 Dictionary Section](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.3.2.html) — dictionary items, types, formats.
7. [FEBio User Manual 4.12, D.4.1 Node Section](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.4.1.html) — node count, dimension, coordinate payload.
8. [FEBio User Manual 4.12, D.4.2 Domain Section](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.4.2.html) — domain, Tet10, element ID and connectivity payload.
9. [FEBio User Manual 4.12, D.5.1 State Header](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.5.1.html) and [D.5.2 State Data Section](https://help.febio.org/docs/FEBioUser-4-12/UM412-Subsection-D.5.2.html) — state time and variable data sections.
10. [Previous native elastic patch observation](2026-09-07-p0-b-elastic-patch.md) — source run, solver, input, direct text, and XPLT artifact provenance.
