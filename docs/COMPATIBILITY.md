# 兼容性说明

- Blender：最低 5.2.0 LTS，不设置最高版本。
- Halo definition schema：类型化支持 1.0.10；更高版本的未知字段按原始 JSON 保留。
- Minecraft 资源包：新建包默认 `pack_format = 15`，该值可编辑。
- 图元：`billboard`、`ring`；旧版 `shape` 只保证读取与原样保留。
- 动画函数：`sin`、`cos`、`linear`。
- 过渡缓动：`linear`、`ease_out_cubic`、`ease_in_out_cubic`。
- labPBR：保留或复制 `_n`、`_s`、`_e` 邻接贴图，但 Halo 的当前渲染器不会读取它们。

缺失贴图、定义文件名与内部 ID 不一致，以及当前解析器未知的字段均不会导致整个资源包导入失败；验证器会报告相应警告。

常驻动画提供通道级增删、编辑和排序；startup/shutdown 可在面板 JSON 字段或 Blender Text Editor 的完整定义 JSON 中编辑。高级 JSON 往返保留未知键的语义和值，但会统一格式化为空格缩进。

0.1.1 起，Billboard 与 Ring 导入时都会翻转纹理 V 轴，以匹配 Minecraft 与 Blender 不同的图像坐标约定。0.1.2 恢复 0.1.0 的透明重叠与透明背面设置，仅保留纹理方向修复。
