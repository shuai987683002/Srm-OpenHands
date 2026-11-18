# 内弹道模型（Internal Ballistics）

## 1. 燃速定律（Saint-Robert / Vieille）

$$r = a \, p_c^{\,n}$$

可选温度修正：$r = a\,p_c^{\,n}\,\exp\!\big(\sigma_p (T - T_\text{ref})\big)$。
系数 $a, n, \sigma_p$ **一律由数据字典或用户提供**，软件不内置真实配方数值。

代码：`srm_open.propellant.burn_rate`。

## 2. 平衡压强（零维，默认模型）

燃气生成率与喷管质量流量平衡：

$$\rho_p A_b(e)\, r = \frac{p_c A_t}{c^*}$$

代入 $r = a p_c^n$ 得闭式解：

$$p_c = \left(\frac{\rho_p\, c^*\, a\, A_b}{A_t}\right)^{\frac{1}{1-n}}$$

其中燃面 $A_b(e)$ 是燃去肉厚 $e$ 的函数，由燃面引擎给出；$e$ 与时间由
$\mathrm{d}e/\mathrm{d}t = r$ 联系。该模型假定容腔充填瞬态可忽略（准稳态）。

代码：`srm_open.ballistics.solver.solve_equilibrium`。

## 3. 瞬态充填模型（可选）

保留容腔质量积累：

$$\frac{\mathrm{d}}{\mathrm{d}t}\left(\frac{p_c V}{R T_c}\right)
= \rho_p A_b r - \frac{p_c A_t}{c^*}$$

用 `scipy.integrate.solve_ivp`（LSODA）积分，可捕捉点火瞬态与压强拖尾。

代码：`srm_open.ballistics.solver.solve_transient`。

## 4. 特征速度与推力

$$c^* = \frac{\sqrt{R T_c}}{\Gamma},\qquad
\Gamma = \sqrt{\gamma}\left(\frac{2}{\gamma+1}\right)^{\frac{\gamma+1}{2(\gamma-1)}}$$

$$F = C_F\, A_t\, p_c,\qquad
C_F = \Gamma\sqrt{\frac{2\gamma}{\gamma-1}\left[1-\left(\frac{p_e}{p_c}\right)^{\frac{\gamma-1}{\gamma}}\right]}
+ \frac{p_e - p_a}{p_c}\,\varepsilon$$

$$I_t = \int F\,\mathrm{d}t,\qquad I_{sp} = \frac{I_t}{m_p\, g_0}$$

代码：`srm_open.nozzle.model`、`srm_open.performance.analysis`。

## 5. 建模假设

1. 燃气为理想气体，比热比 $\gamma$、分子量、$T_c$ 在燃烧过程中取常数
   （由热化学在选定压强下求得的平衡值）。
2. 喷管为等熵、一维、冻结/平衡组分；忽略两相流损失、喉部烧蚀与侵蚀燃烧。
3. 忽略容腔充填瞬态（默认平衡模型）。
4. 燃面演化只依赖燃去肉厚 $e$，不考虑裂纹/脱粘导致的附加燃面。
