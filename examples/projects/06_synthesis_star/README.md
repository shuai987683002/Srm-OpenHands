# 06 指标驱动合成（星形）

## 目的
演示**正向设计的合成链路**（FR-2）：仅给出总体指标，软件自动完成
药型选型 → 参数合成 → 燃面/内弹道/性能计算，并迭代使工作时间收敛到目标。

## 输入
```yaml
requirement:
  thrust: 8000.0            # N
  burn_time: 4.0            # s
  max_chamber_pressure: 9.0e6
  outer_diameter: 0.16      # m
  length_ratio: 5.0
  thrust_profile: neutral   # 近中性 -> 选星形
propellant: ap_htpb_80
family: star
```

## 运行
```bash
PYTHONPATH=src python examples/run_case.py examples/projects/06_synthesis_star
```

## 预期结果（实测）
- 合成药型：星形。
- 平均推力 ≈ 7999.8 N（对 8000 N 偏差 < 0.01%），工作时间 ≈ 4.0000 s。
- 校核：9/9 项全部通过。

## 说明
链路为：指标 → 药型选型 → 参数合成（体积/平均燃面/肉厚匹配）→ 迭代校正
工作时间 → **M9 局部精修**（以推力/工作时间/总冲为目标，在同一正演模型上做
warm-start Nelder-Mead 抛光）。精修后推力、工作时间与总冲均命中指标，
因此 9/9 通过。关闭精修（`refine=False`）可观察"仅合成"的初值偏差。
