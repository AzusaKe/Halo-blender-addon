# 已知问题

## shutdown 缺失时的关闭预览

记录于 2026-08-26，暂不在本轮修复。

当定义包含 `startup`、但没有定义 `shutdown` 时，模组行为应当反演 startup 作为关闭动画。目前 Blender 的“关闭过渡”和“完整序列”预览在部分此类定义中不会播放反演动画。该问题只影响 Blender 预览，不会在导出时生成或改写 `shutdown`，也不会改变资源包在模组内的行为。

## Mesh 材质与非平面面

Mesh 转子组的“自动”烘焙精确支持常见 Principled 基础色/Alpha 和直接 Emission。复杂透明节点网络，以及依赖 Object/Generated 坐标、外部对象或场景灯光的材质，会使用尽力而为的 Cycles 后备路径，建议转换后检查生成的 Billboard。非平面 n-gon 无法由单个平面 Billboard 精确表达；扩展会投影到拟合平面并报告警告。如需精确结果，请先把它三角化或拆成平面面。
