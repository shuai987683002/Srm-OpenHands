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
- 平均推力 ≈ 8064 N（对 8000 N 偏差 ≈ +0.8%），工作时间 ≈ 4.35 s。
- 校核：8/9 项通过（唯一未过项为工作时间对指标偏差，见下）。

## 说明
工作时间由"web 目标 ← 实测燃速加权平均压强"的迭代校正逼近目标；
推力在 0D 平衡模型下由设计压强定喉径，存在小幅系统偏差——
**精确命中推力/总冲属逆向设计职责**（见 CLI `srm inverse` 与 notebook §5）。
