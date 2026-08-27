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
- Mesh 转子组：支持当前 Blender 场景中的 Mesh 对象及其可见修改器结果。源网格局部坐标直接作为新包装组局部坐标；对象级 G/R/S 有意忽略。每个有效 polygon 独立投影到自身平面，使用凸包边方向搜索最小面积覆盖矩形，再转换为子组变换与 Billboard 尺寸。非矩形区域通过 PNG alpha 保留；非平面 n-gon 使用投影近似并给出警告。
- Mesh 材质烘焙：自动模式支持 Principled 基础色与 Alpha、直接 Emission，以及其他节点材质的 Cycles Combined 后备路径；也可明确选择基础色、发光或综合模式。生成贴图随 `.blend` 打包，并在导出时写到当前定义命名空间下。复杂透明节点图、依赖对象/生成坐标或场景灯光的材质属于尽力烘焙，结果可能需要人工检查。
- Mesh UV：0.2.1 起，源材质采样 UV 与目标烘焙 UV 完全分离；源 Mesh 的所有 UV Map 都会逐面复制，普通 Texture Coordinate `UV`、未连接图像纹理和显式命名 UV Map 均继续读取原始坐标。目标最小矩形 UV 只决定生成 PNG 的像素布局，不参与源材质采样。
- Mesh 边缘：0.2.2 起，可在导入对话框设置 0–64 圈边缘扩张，默认 2。每圈以 8 邻域向轮廓外扩展，只复制既有采样像素的完整 RGBA，不平均、不修改原轮廓内部像素；用于减少透明黑插值暗边和相邻面片的锯齿缝隙。
- Mesh 共面合并：0.2.3 起默认合并同材质槽、同法线方向、共面且通过共享边连通的 polygon。整个连通面簇计算一个最小覆盖矩形并生成一张联合遮罩贴图；源 polygon 的所有 UV Map 仍逐 loop 保留。材质槽不同、法线相反、仅共面但不连通的面保持独立，避免超大空白贴图和错误的单面朝向。
- Mesh 后台交互：0.2.4 起，面板发起的转换使用 Blender 模态计时器逐面簇推进，侧栏显示禁用滑块形式的进度条、数量、阶段和取消按钮，并同步状态栏。脚本/API 调用继续提供同步入口。`bpy` 与 Cycles bake 不能安全移到 Python 工作线程，因此界面响应和取消粒度为“一张面簇贴图”；正在执行的单次 bake 本身不可抢占。

缺失贴图、定义文件名与内部 ID 不一致，以及当前解析器未知的字段均不会导致整个资源包导入失败；验证器会报告相应警告。

常驻动画提供通道级增删、编辑和排序。startup/shutdown 支持默认 `segments` 以及按组 ID 的 `id_overrides`（对象包装和直接数组两种写法）；图形界面可管理段的 `duration/easing`，以及 `offset/scale/alpha/rotation` 的 `from/to`、属性级 `duration/easing` 和 rotation `degrees`。`opacity` 旧别名可读取并原样编辑。startup 每个通道首次出现的段要求 `from`，shutdown 最后出现的段要求 `to`，与 Java 解析器的边界规则一致。完整 Text Editor JSON 入口继续保留；高级 JSON 往返保留未知键的语义和值，但会统一格式化为空格缩进。

0.1.1 起，Billboard 与 Ring 导入时都会翻转纹理 V 轴，以匹配 Minecraft 与 Blender 不同的图像坐标约定。0.1.15 起，EEVEE 一致性预览采用“抖动”透明：避免“混合”模式只有对象级排序而导致透明像素遮挡后方图元。0.1.17 起，带独立内外纹理的 Ring 在 Blender 中使用单层双面几何，并在材质节点中按 `Backfacing` 选择观察侧贴图；JSON 与导出仍保留模组所需的 `outer_texture`/`inner_texture`。材质通过 `Light Path.Is Camera Ray` 隔离非摄像机射线，使 full-bright/glowing 预览不再产生 Cycles 间接照明、反射或阴影。旧版 `.blend` 的双层 Ring 会在加载或点击对应渲染器的“刷新材质”时自动升级。
