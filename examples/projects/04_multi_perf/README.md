# 04 多孔装药（经典多管）

## 目的
演示**多孔药柱**（`multi_perforated`，7 孔）的燃面特性。各孔独立燃烧时燃面
随孔径增大而上升；孔间肉厚烧穿、孔洞合并的瞬间燃面阶跃下降，之后按合并空腔
继续增长——总体渐进，但曲线上有台阶。

## 输入
- 药型：多孔（`multi_perforated`），7 孔，单孔直径 0.012 m，
  外径 0.18 m，长 0.5 m，端面抑制。
- 推进剂：`ap_htpb_80`。
- 名义指标：推力 4000 N，工作时间 5 s，压强上限 10 MPa，外径 0.14 m。

## 运行
```bash
PYTHONPATH=src python examples/run_case.py examples/projects/04_multi_perf
```

## 预期结果（实测）
- 平均推力 ≈ 6774 N，工作时间 ≈ 7.07 s。
- 初始燃面 0.1319 m²，肉厚 70.2 mm，装填率 96.9%。
- 校核：5/8 项通过。固定几何算例不调整药型，未达名义指标属预期
  （见 `docs/requirements_compliance.md`）。

## 说明
孔间肉厚由 `web_fraction`（默认 0.15 倍孔径）控制。取 0 会让各孔相切、
肉厚为零，端口在燃去 0.5 mm 内即融合，燃面会被算小一半——这是曾经发生过的
缺陷，现由 `test_multi_perforated_holes_are_separated_by_a_web` 守护。

燃面曲线在上凸段之后有一次阶跃下降（本例约在 e ≈ 0.9 mm），校核压强峰值时
必须覆盖该点。
