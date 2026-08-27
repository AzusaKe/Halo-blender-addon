# 兼容性说明

- Blender：最低 5.2.0 LTS，不设置最高版本。
- Halo definition schema：类型化支持 1.0.10；更高版本的未知字段按原始 JSON 保留。
- Minecraft 资源包：新建包默认 `pack_format = 15`，该值可编辑。
- 图元：`billboard`、`ring`；旧版 `shape` 只保证读取与原样保留。
- 动画函数：`sin`、`cos`、`linear`。
- 过渡缓动：`linear`、`ease_out_cubic`、`ease_in_out_cubic`。
- labPBR：保留或复制 `_n`、`_s`、`_e` 邻接贴图，但 Halo 的当前渲染器不会读取它们。
- 阻尼：图形化编辑 `linearFactor`、`angularFactor`、`maxLinearDistance`、`maxAngularDegrees`、`angularMomentumFactor`、`maxAngularMomentumDegrees` 和顶层 `allow_angular_momentum`；factor 越接近 1 越快贴近目标，越接近 0 跟随越慢。未知子字段与合法零值保留。Blender 暂不模拟阻尼轨迹。
- 树形结构：组可移动到同一定义的光环根或另一个组下；候选列表自动排除自身和全部子组。可选择保持世界外观并重算局部 JSON 变换，或保留原局部值。
- 复制与图元迁移：组复制会递归复制完整子树，图元复制只复制自身，副本位于原父级且使用全新 UUID 与唯一组 ID；相关 startup/shutdown `id_overrides` 会映射到新 ID。组移动可选择携带位置、旋转、缩放、常驻动画、发光/继承及扩展字段；图元迁移创建新属性副本组时提供相同选项，并额外控制 startup/shutdown `id_overrides`。选项默认全开，未携带字段使用 schema 默认值。

缺失贴图、定义文件名与内部 ID 不一致，以及当前解析器未知的字段均不会导致整个资源包导入失败；验证器会报告相应警告。

常驻动画提供通道级增删、编辑和排序。startup/shutdown 支持默认 `segments` 以及按组 ID 的 `id_overrides`（对象包装和直接数组两种写法）；图形界面可管理段的 `duration/easing`，以及 `offset/scale/alpha/rotation` 的 `from/to`、属性级 `duration/easing` 和 rotation `degrees`。`opacity` 旧别名可读取并原样编辑。startup 每个通道首次出现的段要求 `from`，shutdown 最后出现的段要求 `to`，与 Java 解析器的边界规则一致。完整 Text Editor JSON 入口继续保留；高级 JSON 往返保留未知键的语义和值，但会统一格式化为空格缩进。

0.1.1 起，Billboard 与 Ring 导入时都会翻转纹理 V 轴，以匹配 Minecraft 与 Blender 不同的图像坐标约定。0.1.2 恢复 0.1.0 的透明重叠与透明背面设置，仅保留纹理方向修复。
