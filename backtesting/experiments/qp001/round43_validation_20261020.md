# QP001 round 43 validation — 2026-10-20

Worktree: `/home/are01/src/HQA_Project/HQA_qp001`; HEAD `c497f58`.

`--execute` was not run. No preregistration, FW001 inventory, main-checkout or git-index edits; no network.
All 236 protected file hashes match the starting snapshot. Registry bytes match HEAD.

## Verification

- Serial offline pytest: **78 passed in 4.62 s** across the five QP001 test files.
- Regressions cover design-fail/validation-success O linking; final hash mismatch, MemoryError and serialization aborts; fsync/publication before append; save-only idempotent recovery; relative output paths; request-scope skips; and streamed/uncached control equivalence.

## Real-data precheck

Status: `data_incomplete`; source snapshot unchanged: `True`.
All 103 months x S/O are retained with failure reasons, group sizes, stage gates, 60 decision-inclusive price hashes and a source-manifest hash.
Only trailing volatility and decision-date selection inputs are calculated. No PriceBook, holding-period returns, portfolio/NAV, e_t, executability/survival or control performance is used.

| Rule | Months | Complete | Min C_t/A_t | Month(s) at minimum |
|---|---:|---:|---:|---|
| S | 103 | 93 | 0.8417910447761194 | 2018-05 |
| O | 103 | 103 | 0.9416666666666667 | 2023-11 |

Rule S has 10 months below the preregistered 0.90 completeness threshold. All 206 selections were calculated; there were no selection-loader failures. The precheck exits 2 for data incompleteness.

| S incomplete month | C_t/A_t | Ratio |
|---|---:|---:|
| 2018-05 | 282/335 | 0.841791044776 |
| 2019-05 | 294/338 | 0.869822485207 |
| 2019-06 | 302/338 | 0.893491124260 |
| 2022-05 | 447/512 | 0.873046875000 |
| 2022-06 | 445/514 | 0.865758754864 |
| 2022-07 | 440/499 | 0.881763527054 |
| 2023-06 | 340/378 | 0.899470899471 |
| 2025-05 | 327/383 | 0.853785900783 |
| 2025-06 | 345/403 | 0.856079404467 |
| 2025-07 | 360/404 | 0.891089108911 |

Stage-specific O sensitivity availability: `{"design": true, "validation": true}`.
Both design and validation must be available for the combined availability flag.

Stored-version link counts: `{"currency_mismatch_or_missing": 66, "fraction": 0.9985531047198091, "invalid_total_assets": 57, "matched": 163562, "missing_total_assets": 109, "missing_version": 5, "total": 163799}`.
Raw parser skipped rows (unique requests, not repeated scans): `{"bsns_year": 60}`.
OFS fallback counts across decision months: `{"O": 7105, "S": 6718}`.
Integrity scan: `{"op_compared": 163446, "op_not_comparable": 353, "pending_derived": 0, "ready_requests": 1760}`; operating-income conflicts: `0`.

Rule-O correction links by stage (success / failure): design `11688 / 317`; validation `15472 / 317`.

Uncomputable ROA failure counts are division attempts across stock-months, not unique stocks:

- S: `{"CFS:missing_quarter": 2095, "CFS:missing_quarter_income": 231, "CFS:missing_total_assets": 20, "OFS:invalid_total_assets": 9, "OFS:missing_quarter": 1999, "OFS:missing_quarter_income": 307, "OFS:missing_total_assets": 31}`
- O: `{"CFS:missing_quarter": 554, "CFS:missing_quarter_income": 85, "CFS:missing_total_assets": 20, "OFS:invalid_total_assets": 9, "OFS:missing_quarter": 511, "OFS:missing_quarter_income": 108, "OFS:missing_total_assets": 31}`

Listing Y/K/N/E categories are a current profile proxy, separate from monthly C_t/A_t completeness.

Precheck peak RSS: 1,141,792,768 bytes (1141.8 MB).

## AV001 published-window reproduction

| Mode | Rule | Months | Max absolute monthly e difference |
|---|---|---|---:|
| hc003 | S | 2017-05, 2017-06, 2017-07 | 3.4694469519536142e-18 |
| hc003 | O | 2017-05, 2017-06, 2017-07 | 2.6020852139652106e-18 |
| group_weights_alpha_1 | S | 2017-05, 2017-06, 2017-07 | 3.4694469519536142e-18 |
| group_weights_alpha_1 | O | 2017-05, 2017-06, 2017-07 | 2.6020852139652106e-18 |

All 12 mode/rule/month comparisons meet 1e-12. The new branch uses a single group with alpha=1 and asserts its group-weight report for every month.
Published AV001 SHA256: `6c5e6e54779c305cee1dbb312e20a659d43db764f42541b68d5ac822dada16f9`.

## Synthetic memory measurement

103 months, 1,500 stocks, 200 paths, 8 control sets (164,800 path-months), 16 paired cost scenarios. Peak RSS: **662,343,680 bytes (662.3 MB)**; RLIMIT_AS: 1,400,000,000 bytes.
Inputs peak RSS: 419,438,592 bytes. Streamed control details: 192,921,881 bytes. Serialized result: 159,819,748 bytes. Measurement duration: 750.68 s (excluding module imports).
The complete inputs and accumulated scenarios stay resident through the final streaming/atomic/fsynced result write. Temporary synthetic result/detail files are removed after measurement.
Scope: Full paired accounting, 8 control sets and result serialization with 206 selections and a 103 x 1500 quote book; excludes raw financial/archive parsing (measured by precheck).
The real performance run was not executed; its peak RSS is not an observed result. Raw financial parsing is measured separately by the real-data precheck.

## Changed files

Compared with the preserved starting uncommitted runner; unchanged pre-existing untracked files are omitted.

| File | Change | SHA256 |
|---|---|---|
| `backtesting/experiments/qp001/README.md` | modified | `63046af1c6e4fcf2a454f7e9d5d2b8f254eb4e86c4b85ef9fc5ea48beefb27bb` |
| `backtesting/experiments/qp001/artifacts.py` | new | `ae4c37d37348c75c81c28de1d47e5cb70046f851ab8e27f4310c22bfaf8d7a68` |
| `backtesting/experiments/qp001/financials.py` | modified | `435236f6ce99a6d66f47a47d5caaf775918d9fef291f42063dcd978457265d2c` |
| `backtesting/experiments/qp001/memory_probe.py` | new | `b75712fd225edc0499ec68ed1f150034f5ee19c4540e88a64eb72647ab89fc1e` |
| `backtesting/experiments/qp001/portfolio.py` | modified | `7e02cda9ebd3f9516bf62647db99dcd3f7e20a70ed0509c4222dad0610de11bd` |
| `backtesting/experiments/qp001/runner.py` | modified | `8eec545d380960ddcd02afb58699a812cfabf2e39a53dbe87ea82117991e7e2a` |
| `backtesting/experiments/qp001/selection.py` | new | `2ef09003b44b8e69b45619cc394cccb47312c2456e46b909b7478c06fbc1f652` |
| `tests/test_experiments_qp001_financials.py` | modified | `74c20d941fd4508b8e6b33157f95504c62b3bc1359d14b120af1dc4056ab2023` |
| `tests/test_experiments_qp001_portfolio.py` | modified | `a673b5e0c012145630bbdf59f3c1c70fb0975e19cd1d3f717a0379cb66d58ecc` |
| `tests/test_experiments_qp001_runner.py` | modified | `9fd6be321fe34e3fa0a6a167f5b79975d800bcf73afb8ca1eb2614d6537d54de` |

New evidence artifacts: `precheck_20261020.json`, `memory_probe_20261020.json`, and this report.

Artifact SHA256:

- Precheck: `91660fbb1da3df1fab41d7fcf5aa2e840427257b46ea237af7e7c378ac3ea88b`
- Memory probe: `13674828ed4ea41dd3726153d3f16576eee2bc70c26bed97913c35ff043eeb26`

Runtime: `{"PyYAML": "6.0.3", "exchange-calendars": "4.13.2", "numpy": "2.4.4", "pandas": "3.0.2", "python": "3.12.3"}`.

## Monthly completeness

A zero A_t yields an undefined ratio, shown as `None`; it is not replaced with a number.

| Month | S C_t/A_t | S ratio | S complete | O C_t/A_t | O ratio | O complete |
|---|---:|---:|---|---:|---:|---|
| 2017-05 | 337/371 | 0.9083557951482479 | True | 374/382 | 0.9790575916230366 | True |
| 2017-06 | 360/389 | 0.9254498714652957 | True | 387/396 | 0.9772727272727273 | True |
| 2017-07 | 348/378 | 0.9206349206349206 | True | 375/385 | 0.974025974025974 | True |
| 2017-08 | 328/356 | 0.9213483146067416 | True | 346/353 | 0.9801699716713881 | True |
| 2017-09 | 336/368 | 0.9130434782608695 | True | 358/367 | 0.9754768392370572 | True |
| 2017-10 | 319/348 | 0.9166666666666666 | True | 339/347 | 0.9769452449567724 | True |
| 2017-11 | 351/365 | 0.9616438356164384 | True | 376/377 | 0.9973474801061007 | True |
| 2017-12 | 357/372 | 0.9596774193548387 | True | 382/385 | 0.9922077922077922 | True |
| 2018-01 | 362/376 | 0.9627659574468085 | True | 388/390 | 0.9948717948717949 | True |
| 2018-02 | 377/394 | 0.9568527918781726 | True | 402/405 | 0.9925925925925926 | True |
| 2018-03 | 354/371 | 0.954177897574124 | True | 382/386 | 0.9896373056994818 | True |
| 2018-04 | 261/277 | 0.9422382671480144 | True | 259/263 | 0.9847908745247148 | True |
| 2018-05 | 282/335 | 0.8417910447761194 | False | 329/335 | 0.982089552238806 | True |
| 2018-06 | 292/324 | 0.9012345679012346 | True | 322/328 | 0.9817073170731707 | True |
| 2018-07 | 277/304 | 0.9111842105263158 | True | 301/307 | 0.9804560260586319 | True |
| 2018-08 | 310/329 | 0.9422492401215805 | True | 323/329 | 0.9817629179331308 | True |
| 2018-09 | 312/331 | 0.9425981873111783 | True | 323/329 | 0.9817629179331308 | True |
| 2018-10 | 300/318 | 0.9433962264150944 | True | 315/318 | 0.9905660377358491 | True |
| 2018-11 | 247/261 | 0.946360153256705 | True | 263/264 | 0.9962121212121212 | True |
| 2018-12 | 245/259 | 0.9459459459459459 | True | 263/265 | 0.9924528301886792 | True |
| 2019-01 | 259/273 | 0.9487179487179487 | True | 274/275 | 0.9963636363636363 | True |
| 2019-02 | 262/277 | 0.9458483754512635 | True | 279/280 | 0.9964285714285714 | True |
| 2019-03 | 276/288 | 0.9583333333333334 | True | 286/287 | 0.9965156794425087 | True |
| 2019-04 | 355/366 | 0.9699453551912568 | True | 372/375 | 0.992 | True |
| 2019-05 | 294/338 | 0.8698224852071006 | False | 339/341 | 0.9941348973607038 | True |
| 2019-06 | 302/338 | 0.893491124260355 | False | 338/340 | 0.9941176470588236 | True |
| 2019-07 | 321/345 | 0.9304347826086956 | True | 344/346 | 0.9942196531791907 | True |
| 2019-08 | 345/367 | 0.9400544959128065 | True | 365/367 | 0.9945504087193461 | True |
| 2019-09 | 336/357 | 0.9411764705882353 | True | 357/359 | 0.9944289693593314 | True |
| 2019-10 | 336/356 | 0.9438202247191011 | True | 358/358 | 1.0 | True |
| 2019-11 | 320/339 | 0.943952802359882 | True | 341/342 | 0.9970760233918129 | True |
| 2019-12 | 315/332 | 0.9487951807228916 | True | 332/333 | 0.996996996996997 | True |
| 2020-01 | 309/329 | 0.939209726443769 | True | 330/332 | 0.9939759036144579 | True |
| 2020-02 | 310/329 | 0.9422492401215805 | True | 333/335 | 0.9940298507462687 | True |
| 2020-03 | 296/312 | 0.9487179487179487 | True | 308/308 | 1.0 | True |
| 2020-04 | 322/340 | 0.9470588235294117 | True | 331/331 | 1.0 | True |
| 2020-05 | 311/327 | 0.9510703363914373 | True | 325/327 | 0.9938837920489296 | True |
| 2020-06 | 333/352 | 0.9460227272727273 | True | 353/356 | 0.9915730337078652 | True |
| 2020-07 | 344/366 | 0.9398907103825137 | True | 362/365 | 0.9917808219178083 | True |
| 2020-08 | 296/312 | 0.9487179487179487 | True | 313/317 | 0.9873817034700315 | True |
| 2020-09 | 296/314 | 0.9426751592356688 | True | 312/318 | 0.9811320754716981 | True |
| 2020-10 | 302/319 | 0.9467084639498433 | True | 316/322 | 0.9813664596273292 | True |
| 2020-11 | 362/391 | 0.9258312020460358 | True | 389/396 | 0.9823232323232324 | True |
| 2020-12 | 363/388 | 0.9355670103092784 | True | 387/392 | 0.9872448979591837 | True |
| 2021-01 | 377/401 | 0.940149625935162 | True | 397/403 | 0.9851116625310173 | True |
| 2021-02 | 376/400 | 0.94 | True | 398/404 | 0.9851485148514851 | True |
| 2021-03 | 377/395 | 0.9544303797468354 | True | 393/398 | 0.9874371859296482 | True |
| 2021-04 | 369/387 | 0.9534883720930233 | True | 390/395 | 0.9873417721518988 | True |
| 2021-05 | 509/549 | 0.9271402550091075 | True | 552/555 | 0.9945945945945946 | True |
| 2021-06 | 501/538 | 0.9312267657992565 | True | 539/544 | 0.9908088235294118 | True |
| 2021-07 | 517/550 | 0.94 | True | 551/557 | 0.9892280071813285 | True |
| 2021-08 | 546/575 | 0.9495652173913044 | True | 573/581 | 0.9862306368330465 | True |
| 2021-09 | 542/573 | 0.9458987783595113 | True | 569/580 | 0.9810344827586207 | True |
| 2021-10 | 549/577 | 0.951473136915078 | True | 572/582 | 0.9828178694158075 | True |
| 2021-11 | 466/485 | 0.9608247422680413 | True | 482/490 | 0.9836734693877551 | True |
| 2021-12 | 455/472 | 0.9639830508474576 | True | 469/476 | 0.9852941176470589 | True |
| 2022-01 | 458/477 | 0.960167714884696 | True | 472/480 | 0.9833333333333333 | True |
| 2022-02 | 457/474 | 0.9641350210970464 | True | 471/479 | 0.9832985386221295 | True |
| 2022-03 | 458/472 | 0.9703389830508474 | True | 483/491 | 0.9837067209775967 | True |
| 2022-04 | 464/477 | 0.9727463312368972 | True | 486/493 | 0.9858012170385395 | True |
| 2022-05 | 447/512 | 0.873046875 | False | 504/509 | 0.9901768172888016 | True |
| 2022-06 | 445/514 | 0.8657587548638133 | False | 505/512 | 0.986328125 | True |
| 2022-07 | 440/499 | 0.8817635270541082 | False | 489/497 | 0.9839034205231388 | True |
| 2022-08 | 455/479 | 0.9498956158663883 | True | 474/483 | 0.9813664596273292 | True |
| 2022-09 | 456/479 | 0.9519832985386222 | True | 468/481 | 0.972972972972973 | True |
| 2022-10 | 446/465 | 0.9591397849462365 | True | 449/466 | 0.9635193133047211 | True |
| 2022-11 | 430/447 | 0.9619686800894854 | True | 433/447 | 0.9686800894854586 | True |
| 2022-12 | 445/461 | 0.96529284164859 | True | 448/462 | 0.9696969696969697 | True |
| 2023-01 | 451/468 | 0.9636752136752137 | True | 457/472 | 0.9682203389830508 | True |
| 2023-02 | 460/476 | 0.9663865546218487 | True | 464/480 | 0.9666666666666667 | True |
| 2023-03 | 368/380 | 0.968421052631579 | True | 365/376 | 0.9707446808510638 | True |
| 2023-04 | 384/396 | 0.9696969696969697 | True | 386/397 | 0.9722921914357683 | True |
| 2023-05 | 337/373 | 0.903485254691689 | True | 360/371 | 0.9703504043126685 | True |
| 2023-06 | 340/378 | 0.8994708994708994 | False | 364/376 | 0.9680851063829787 | True |
| 2023-07 | 327/351 | 0.9316239316239316 | True | 341/351 | 0.9715099715099715 | True |
| 2023-08 | 349/376 | 0.9281914893617021 | True | 367/384 | 0.9557291666666666 | True |
| 2023-09 | 339/362 | 0.93646408839779 | True | 355/370 | 0.9594594594594594 | True |
| 2023-10 | 342/362 | 0.9447513812154696 | True | 355/369 | 0.962059620596206 | True |
| 2023-11 | 333/355 | 0.9380281690140845 | True | 339/360 | 0.9416666666666667 | True |
| 2023-12 | 319/337 | 0.9465875370919882 | True | 324/343 | 0.9446064139941691 | True |
| 2024-01 | 322/339 | 0.9498525073746312 | True | 339/346 | 0.9797687861271677 | True |
| 2024-02 | 334/350 | 0.9542857142857143 | True | 352/360 | 0.9777777777777777 | True |
| 2024-03 | 308/321 | 0.9595015576323987 | True | 319/321 | 0.9937694704049844 | True |
| 2024-04 | 303/315 | 0.9619047619047619 | True | 314/316 | 0.9936708860759493 | True |
| 2024-05 | 373/394 | 0.9467005076142132 | True | 397/397 | 1.0 | True |
| 2024-06 | 368/385 | 0.9558441558441558 | True | 388/388 | 1.0 | True |
| 2024-07 | 356/370 | 0.9621621621621622 | True | 371/371 | 1.0 | True |
| 2024-08 | 369/391 | 0.9437340153452686 | True | 394/398 | 0.9899497487437185 | True |
| 2024-09 | 350/371 | 0.9433962264150944 | True | 370/376 | 0.9840425531914894 | True |
| 2024-10 | 348/368 | 0.9456521739130435 | True | 369/374 | 0.9866310160427807 | True |
| 2024-11 | 315/326 | 0.9662576687116564 | True | 331/339 | 0.976401179941003 | True |
| 2024-12 | 330/344 | 0.9593023255813954 | True | 347/359 | 0.9665738161559888 | True |
| 2025-01 | 321/330 | 0.9727272727272728 | True | 337/343 | 0.9825072886297376 | True |
| 2025-02 | 321/332 | 0.9668674698795181 | True | 340/347 | 0.9798270893371758 | True |
| 2025-03 | 316/328 | 0.9634146341463414 | True | 338/340 | 0.9941176470588236 | True |
| 2025-04 | 335/350 | 0.9571428571428572 | True | 349/355 | 0.9830985915492958 | True |
| 2025-05 | 327/383 | 0.8537859007832899 | False | 373/378 | 0.9867724867724867 | True |
| 2025-06 | 345/403 | 0.8560794044665012 | False | 397/402 | 0.9875621890547264 | True |
| 2025-07 | 360/404 | 0.8910891089108911 | False | 397/402 | 0.9875621890547264 | True |
| 2025-08 | 362/370 | 0.9783783783783784 | True | 363/370 | 0.981081081081081 | True |
| 2025-09 | 352/364 | 0.967032967032967 | True | 355/365 | 0.9726027397260274 | True |
| 2025-10 | 348/358 | 0.9720670391061452 | True | 349/359 | 0.9721448467966574 | True |
| 2025-11 | 436/450 | 0.9688888888888889 | True | 448/458 | 0.9781659388646288 | True |
