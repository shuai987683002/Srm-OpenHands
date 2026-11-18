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
- 平均推力 ≈ 2705 N，工作时间 ≈ 5.80 s。
- 校核：6/9 项通过。

## 说明
简单管形的可行参数空间较窄（仅内径与长度），在 0.13 m 外径、长径比 6 的
约束下，燃面—肉厚组合无法同时精确命中 2500 N / 5 s，故推力与工作时间
两项检查不通过。这体现了**正向外形合成**的边界：需要精确匹配时，应改用
逆向设计在药型参数空间上直接优化。
