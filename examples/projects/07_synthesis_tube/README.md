# 07 指标驱动合成（管形 / 渐进）

## 目的
演示在**渐进**推力偏好下，合成链路选到**管形**药柱，并给出对应性能。

## 输入
```yaml
requirement:
  thrust: 2500.0            # N
  burn_time: 5.0            # s
  max_chamber_pressure: 8.0e6
  outer_diameter: 0.13      # m
  length_ratio: 6.0
  thrust_profile: progressive  # 渐进 -> 选管形
propellant: ap_htpb_80
family: tube
```

## 运行
```bash
PYTHONPATH=src python examples/run_case.py examples/projects/07_synthesis_tube
```

## 预期结果（实测）
- 合成药型：管形（内部燃烧，渐进）。
- 平均推力 ≈ 2500.0 N，工作时间 ≈ 4.9979 s。
- 校核：9/9 项全部通过。

## 说明
与 `06` 相同链路，本例在**渐进**推力偏好下选到管形。管形参数空间较窄
（内径与长度），但经 M9 局部精修后仍精确命中 2500 N / 5 s，9/9 通过。
关闭精修（`refine=False`）时仅靠合成初值无法同时命中，可观察到该差异——
这正是精修步骤（方案 §5.1 第 7 步）存在的意义。
