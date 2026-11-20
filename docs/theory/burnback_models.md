# 燃面演化模型（Burn-back Models）

燃面 $A_b$ 作为燃去肉厚 $e$ 的函数，是内弹道与性能计算的几何输入。软件提供
**两条独立通道**并互相校核。

## 1. 通道 A：几何解析法（默认）

燃烧面沿外法向推进。对端口（内孔）而言，这等价于端口多边形的
**Minkowski 膨胀**（`shapely` 的 `Polygon.buffer(e)`）；对外表面则是半径收缩。

- 端口燃烧：$A_b^\text{port}(e) = \text{perimeter}\big(\text{port} \oplus e\big)\times L$
- 外表面燃烧：$A_b^\text{ext}(e) = 2\pi r_o(e)\,L$
- 端面燃烧（cigarette）：$A_b = \pi (r_o^2 - r_i^2)$，不随 $e$ 变

肉厚 $e_\text{max}$：内孔到外壁的最小径向间隙（内外同时燃烧时取一半）。

代码：`srm_open.burnback.geometric`、`srm_open.geometry.analysis`。

### 支持药型族

| 族 | 端口几何 | 典型推力形状 |
|---|---|---|
| cylindrical | 实心（仅端面/外表面燃烧） | 恒定（端面） |
| tube / tube_inhibited | 圆孔 | 递增（内孔） |
| star | N 角星形 | 渐进-退减 |
| wagon_wheel | 轮辐 | 近中性/退减 |
| slotted | 纵向开槽 | 渐进 |
| finocyl | 翼+中心柱 | 高装填 |
| spherical | 球腔 | 递增 |
| conical | 锥腔 | 渐进 |
| multi_perforated | 多孔（7 孔等） | 渐进（末期孔洞合并，见注） |
| wedge | 等角扇形腔 | 近中性（末期退减） |
| airfoil | NACA 翼型叶片 | 近中性 |
| embedded_wire | 圆孔 + 径向金属丝 | 渐进（金属丝新增燃面） |
| custom_polygon | 用户多边形 | 任意 |

注：多孔药的 $A_b(e)$ 在孔间肉厚烧穿、孔洞合并时会出现一次阶跃下降
（本例约在 $e \approx 0.9$ mm），之后继续增长——总体渐进但曲线上有台阶，
不是单调曲线。孔间肉厚由 `web_fraction`（默认 0.15 倍孔径）控制，取 0 会使
各孔相切、肉厚为零，端口在燃去 0.5 mm 内即融合，燃面被算小一半。
各族的实测对比见 `docs/theory/grain_cavity_families.md`。

## 2. 通道 B：Level-Set / 网格法

在二维截面上以距离函数 $\phi$ 表示燃面，演化方程：

$$\frac{\partial \phi}{\partial t} + r\,|\nabla \phi| = 0,\qquad \phi=0 \text{ 为燃面}$$

燃速沿法向推进；用重初始化保持 $\phi$ 为符号距离函数。燃面由 $\phi=0$ 的
周长积分得到。该通道可处理自由曲面/非参数化药型。

代码：`srm_open.burnback.levelset`。

## 3. 双通道互校

`crosscheck()` 在共同 $e$ 区间上比较两通道的 $A_b(e)$，返回最大/平均相对偏差，
作为 `DesignResult` 的数值一致性校核项。实测：管形 7.1%、星形 4.0%（教学级网格）。

## 4. 建模假设

1. 二维截面假设：沿轴向无燃面变化（分段药柱按段分别计算后求和）。
2. 燃速各向同性、沿法向均匀；不考虑侵蚀燃烧导致的局部燃速提升。
3. 端口多边形以直线段/圆弧表示，倒角/圆角以 `fillet` 参数近似。
4. Level-Set 通道为教学级网格，精度随网格加密提高。
5. 端口允许多连通（多孔药），所有连通分量都参与燃面计算；几何修复只做
   有效性修补，不丢弃分量。
6. 嵌金属丝族的燃面含解析增面项 $N_w 2\pi r_w \min(e, e_{\max})$，其体积
   从推进剂体积中扣除；不建模金属丝的烧蚀与热效应。
