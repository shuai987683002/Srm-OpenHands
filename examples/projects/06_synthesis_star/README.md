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
- 平均推力 ≈ 8000.0 N（对 8000 N 偏差 < 0.01%）；工作时间 ≈ 3.994 s（对 4 s 偏差 0.16%）。
- 校核：9/9 项全部通过。

## 说明
链路为：指标 → 药型选型 → 参数合成（体积/平均燃面/肉厚匹配）→ 迭代校正
工作时间 → **M9 局部精修**（以推力/工作时间/总冲为目标做坐标分步搜索：推力用
乘性步长，燃时用括号+二分）→ **全模型收口**。精修在提速的粗模型（`fast=True`）
上迭代，交付结果却按全分辨率重算，两者最大可差 2%，故最后在全模型上把燃时、
推力各校正一次。精修后推力偏差 <0.01%、燃时偏差 0.16%，均远小于校核容差
（推力/总冲默认 5%，燃时 15%），因此 9/9 通过。
关闭精修（`refine=False`）可观察"仅合成"的初值偏差。
