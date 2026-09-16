# 兼容性说明

- Blender：最低 5.2.0 LTS，不设置最高版本。
- 项目 Mesh 转原生 OBJ（0.5.0）：从当前场景选择非 Halo Mesh，可求值可见修改器，并选择把源对象相对目标父组的变换烘焙进模型或只使用源 Mesh 局部坐标。写出时把 Blender 坐标反变换为 Minecraft 坐标，所有面通过 loop triangle 输出为带 UV 三角形，仅保留 `v`、`vt`、`f`；无 UV 且不导入材质时生成占位 UV。直接材质模式优先读取 Principled/Emission 直连图像，也能从 Mix 等复杂图中提取全模型唯一的图像及 UV Map，同时明确忽略节点运算；多图像或 Mapping/Generated 坐标拒绝猜测。烘焙模式保留原活动/渲染 UV 供源材质采样，另建独立 Smart UV 作为 Cycles 目标与最终 OBJ UV，把多材质输出为单张可配置分辨率及 margin 的 PNG。复杂透明节点无法归约到 Principled Alpha 时按不透明并警告。Blender 5.2 不提供等价的 EEVEE 材质烘焙操作器。
- Halo 2.0 原生 Mesh（0.4.0）：类型化支持 schema 1.1.0 的 `mesh` 图元；按模组 `ObjMeshLoader` 接受带 UV 的三角形与平面凸四边形、正/负独立索引及可选法线，四边形按同样顺序三角化。默认由 `size[x,y,z]` 对 OBJ 被引用顶点的包围盒逐轴缩放并保留作者原点；`preserve_proportions:true` 时 `size` 可省略且被忽略，改由非负有限数 `scale` 统一缩放原始 XYZ（默认 1 OBJ 单位对应 1 格）。随后使用统一 Minecraft→Blender 坐标转换。OBJ 可从面板验证并复制/重链接到当前定义命名空间，同内容复用、异内容加后缀；缺失或非法模型使用醒目占位几何并给出验证警告，原 JSON 仍可导出。
- 资源路径大小写（0.4.0）：新导入 PNG/OBJ 的命名空间、资源路径和文件名全部规范为小写。导出旧工程时同步迁移 JSON 中的 `texture`、`inner_texture`、Mesh `model`/`alpha_mask.texture` 及对应文件家族；大小写折叠后同内容复用，异内容添加数字后缀，避免 Halo/Minecraft 因非法大写资源 ID 跳过整个定义。
- Mesh 简易 Shader（0.4.0）：图形化编辑 `material.double_sided` 以及唯一 `alpha_mask` 效果。遮罩 PNG 以 Non-Color、Closest、Repeat 读取红通道；linear 直接乘基础 PNG Alpha，step 精确实现 `red >= threshold`。U/V 偏移分别对 `sin`、`cos`、`linear` 项求和并循环到 `[0,1)`，在时间轴每帧更新；每个 Mesh 使用独立 Blender 材质，避免共享贴图的图元互相覆盖动画 uniform。跟随 Halo 2.x 当前契约，基础纹理与遮罩各自按原生分辨率和归一化 UV 采样，尺寸与长宽比无需相同；插件不再检查整数倍关系，也不重采样图片。
- ID 资源归属与动画播放（0.3.7）：图元导入 PNG 时始终使用所属光环 ID 的命名空间；相同材质家族仍按内容复用。定义 JSON 导出文件名由 ID 的名称部分生成，不再沿用 `halo.json` 等源文件名；规范化后目标路径冲突会在验证及导出时要求先修改 ID。动画播放每次从第 1 帧重新开始，按实际 startup/shutdown 队列设置时长，无 shutdown 时沿用反演 startup 的时长；完整序列包含启动、停留和关闭，重复点击播放会重新开始。
- 多行动画 JSON（0.3.6）：光环根与组的常驻动画、startup 和 shutdown Text 数据块拥有稳定的场景/定义/组绑定。显式应用、应用并返回、保存 `.blend` 及 ZIP/文件夹导出均通过同一同步入口更新类型化字段和原始定义 AST；重复打开不覆盖待应用内容。无效 JSON 保留在 `.blend` 中且不污染最后一次有效动画，导出会拒绝继续，修复后可再次应用。旧版已经保存但未同步的带标记 Text 会在加载时尝试恢复。
- 命名空间重命名（0.3.5）：光环 ID 的命名空间改变时，将当前定义实际使用的全部贴图复制到新命名空间，保留相对子目录并重写递归 `texture` / `outer_texture` / `inner_texture` 字段、图元属性和预览链接；未知字段与非贴图字符串不动。包括跨命名空间贴图、Ring 内侧、内嵌 Mesh 烘焙 PNG 和 labPBR/元数据。同内容复用、异内容加后缀并提示，缺图迁移引用及残存附图但仍显示缺图警告；文件迁移失败拒绝 ID 更改。共享旧资源不删不改，只在导出副本里排除无引用 PNG。仅改 ID 名称部分不触发迁移。
- 导出贴图可达性（0.3.4）：Blender ZIP/文件夹导出写完当前光环 JSON 后，仅保留其递归 `texture` / `outer_texture` / `inner_texture` 引用对应的 PNG 家族。`assets/*/textures/` 内其余 PNG 和图片元数据仅从临时输出树移除，空目录随之清除；非贴图未知文件不动。以实际资源路径为准，支持跨命名空间共享和省略 `.png`，无命名空间时按 `minecraft` 处理，不按相同文件名猜测缺失引用。Mesh 生成图片亦按最终 JSON 引用选择。编辑缓存/内嵌副本依然完整保留；纯 Python 无损 PackProject 导出接口不改变其原有保留语义。
- PNG 复用与失效 Temp 清理（0.3.3）：PNG 导入对完整图片家族（PNG、labPBR、图片元数据）做 SHA-256 内容比较，在当前来源和命名空间中复用已绑定图片及已有编号副本；不同内容保留冲突后缀。支持保存后的 Blender 相对路径，不重复创建 Image。旧工程恢复后删除无文件、无打包数据、无可用像素的 Halo Temp Image 数据块，Shader 链接重映射到占位图片；保留图元和原 JSON 资源 ID，不删除磁盘文件，不清理其他用户图片。手动修复当前场景时还保护其他场景使用的图片。
- `.blend` 资源持久化（0.3.2）：普通 PNG 和 Ring 内侧图片自动打包；保存前按来源内嵌完整资源 ZIP（Base64 Text 数据块、SHA-256 校验），保留未显示的 labPBR、图片元数据及未知文件。加载时恢复到 Blender 用户数据目录的独立缓存，导出前亦能恢复缓存丢失。恢复同时更新 Image 的路径与 ImagePackedFile 内部路径，避免 Blender“打包资源”仍访问旧 Temp。不同来源的相同纹理 ID 按来源/对象材质链接隔离。旧版单来源及本地项目会自动迁移；缺少原包且从未内嵌的已丢失图片无法自动恢复，会告警而不把占位棋盘格导出为原贴图。不修改源包或用户其他模型的图片。
- Halo definition schema：类型化支持 1.1.0，并继续读取 1.0.x；更高版本的未知字段按原始 JSON 保留。
- Minecraft 资源包：新建与缺省导出范围为 Java 1.20～26.3 及后续格式。`pack_format = 15` 作为 1.20/1.20.1 的旧版基准；`supported_formats = {min_inclusive: 15, max_inclusive: 2147483647}` 供 1.20.2 至旧元数据格式客户端读取；`min_format = [15,0]`、`max_format = 2147483647` 供资源包格式 65 及以上客户端读取。26.3 的正式资源包版本为 97.1；Minecraft 当前没有专门的开放上界标记，所以使用 Java 正整数上限作为实用开放范围，直到 Mojang 改变元数据契约。由于范围跨越格式 65，两套范围字段会同时保留。旧插件自动生成的 `88` / `[88,0]` 在导出时迁移，其他自定义兼容范围不覆盖。0.2.6 起，导入显示与导出会在缺失或空白时补齐 `description = "Halo Pack Editor export"`；已有字符串或文本组件描述以及未知元数据保持不变。
- 图元：`billboard`、`ring`、`mesh`；旧版 `shape` 只保证读取与原样保留。
- 图元计数：0.2.7 起，“光环属性”按当前定义 ID 实时统计场景中全部受管理的 Billboard、Ring 与 Mesh；计数只用于编辑信息显示，不写入 JSON。
- 动画函数：`sin`、`cos`、`linear`。
- 过渡缓动：`linear`、`ease_out_cubic`、`ease_in_out_cubic`。
- labPBR：保留或复制 `_n`、`_s`、`_e` 邻接贴图，但 Halo 的当前渲染器不会读取它们。
- 阻尼：图形化编辑 `linearFactor`、`angularFactor`、`maxLinearDistance`、`maxAngularDegrees`、`angularMomentumFactor`、`maxAngularMomentumDegrees` 和顶层 `allow_angular_momentum`；factor 越接近 1 越快贴近目标，越接近 0 跟随越慢。未知子字段与合法零值保留。Blender 暂不模拟阻尼轨迹。
- 树形结构：组可移动到同一定义的光环根或另一个组下；候选列表自动排除自身和全部子组。可选择保持世界外观并重算局部 JSON 变换，或保留原局部值。
- 复制与图元迁移：组复制会递归复制完整子树，图元复制只复制自身，副本位于原父级且使用全新 UUID 与唯一组 ID；相关 startup/shutdown `id_overrides` 会映射到新 ID。组移动可选择携带位置、旋转、缩放、常驻动画、发光/继承及扩展字段；图元迁移创建新属性副本组时提供相同选项，并额外控制 startup/shutdown `id_overrides`。选项默认全开，未携带字段使用 schema 默认值。
- 同父级批量编辑：0.2.8 起，复制和删除支持同一直接父级下的组/图元混选；组移动只接受同级组，图元迁移只接受同组图元。多个图元共用一个新属性副本组。目标列表会排除所有待移动组及其完整子树；跨父级、跨定义、非 Halo 对象和混合类型移动会在修改场景前整体拒绝。
- 面向摄像机动画：0.2.9 起，3D 视图绘制时会缓存当前视图朝向，时间轴每帧完成根/组动画求值后重新计算 `face_camera` 图元的父级局部旋转，因此父级正在位移或旋转时也会持续面向视图。重新打开文件会丢弃旧视图缓存；首次绘制前仅在存在场景摄像机时使用其朝向作为后备。
- 多来源合并：0.3.0 起，ZIP、文件夹和新建光环均追加到当前项目。每个导入定义绑定稳定来源 ID 和位于 Blender 用户数据目录的独立可写缓存；ZIP 可从源文件重新解包，文件夹会先复制后编辑，新建定义归入“本地编辑资源”，因此贴图重链接和 Mesh 转换不会修改磁盘源包。导出按来源列表顺序合并资源，后导入来源覆盖相同非定义资源路径。来源中任意嵌套层级的 definition JSON 会在临时导出树中统一移除，再只写当前光环列表，因此清除来源/光环后不会复活旧定义。
- 重名与可见性：相同 Definition ID 的后来者仅在 Blender 项目和导出副本中自动改为 `_2`、`_3` 等，源 JSON/ZIP 不变。`visible` 是 Blender 编辑器专用状态，会同时控制所属 Root/Group/Primitive 的视图与渲染隐藏，不属于 Halo 1.1.0 schema，也不会写入导出 JSON。
- Mesh 转子组：支持当前 Blender 场景中的 Mesh 对象及其可见修改器结果。源网格局部坐标直接作为新包装组局部坐标；对象级 G/R/S 有意忽略。每个有效 polygon 独立投影到自身平面，使用凸包边方向搜索最小面积覆盖矩形，再转换为子组变换与 Billboard 尺寸。非矩形区域通过 PNG alpha 保留；非平面 n-gon 使用投影近似并给出警告。
- Mesh 材质烘焙：自动模式支持 Principled 基础色与 Alpha、直接 Emission，以及其他节点材质的 Cycles Combined 后备路径；也可明确选择基础色、发光或综合模式。生成贴图随 `.blend` 打包，并在导出时写到当前定义命名空间下。复杂透明节点图、依赖对象/生成坐标或场景灯光的材质属于尽力烘焙，结果可能需要人工检查。
- Mesh UV：0.2.1 起，源材质采样 UV 与目标烘焙 UV 完全分离；源 Mesh 的所有 UV Map 都会逐面复制，普通 Texture Coordinate `UV`、未连接图像纹理和显式命名 UV Map 均继续读取原始坐标。目标最小矩形 UV 只决定生成 PNG 的像素布局，不参与源材质采样。
- Mesh 边缘：0.2.2 起，可在导入对话框设置 0–64 圈边缘扩张，默认 2。每圈以 8 邻域向轮廓外扩展，只复制既有采样像素的完整 RGBA，不平均、不修改原轮廓内部像素；用于减少透明黑插值暗边和相邻面片的锯齿缝隙。
- Mesh 共面合并：0.2.3 起默认合并同材质槽、同法线方向、共面且通过共享边连通的 polygon。整个连通面簇计算一个最小覆盖矩形并生成一张联合遮罩贴图；源 polygon 的所有 UV Map 仍逐 loop 保留。材质槽不同、法线相反、仅共面但不连通的面保持独立，避免超大空白贴图和错误的单面朝向。
- Mesh 后台交互：0.2.4 起，面板发起的转换使用 Blender 模态计时器逐面簇推进，侧栏显示禁用滑块形式的进度条、数量、阶段和取消按钮，并同步状态栏。脚本/API 调用继续提供同步入口。`bpy` 与 Cycles bake 不能安全移到 Python 工作线程，因此界面响应和取消粒度为“一张面簇贴图”；正在执行的单次 bake 本身不可抢占。
- Mesh 直接 UV 采样：0.2.5 起，导入对话框提供默认关闭的“直接 UV 采样”。启用后，纯色材质以及 Base Color/Emission Color 直接连接单张 Flat Image Texture 的简单材质会按指定或活动 UV Map 直接栅格化，支持 Closest/线性采样和纹理延展方式，并保留 Alpha 与边缘扩张。Mapping、混合节点、程序纹理、UDIM/序列/视频和其他复杂节点自动回退 Cycles，不会中止整个转换。

缺失贴图、定义文件名与内部 ID 不一致，以及当前解析器未知的字段均不会导致整个资源包导入失败；验证器会报告相应警告。

常驻动画提供通道级增删、编辑和排序。startup/shutdown 支持默认 `segments` 以及按组 ID 的 `id_overrides`（对象包装和直接数组两种写法）；图形界面可管理段的 `duration/easing`，以及 `offset/scale/alpha/rotation` 的 `from/to`、属性级 `duration/easing` 和 rotation `degrees`。`opacity` 旧别名可读取并原样编辑。startup 每个通道首次出现的段要求 `from`，shutdown 最后出现的段要求 `to`，与 Java 解析器的边界规则一致。完整 Text Editor JSON 入口继续保留；高级 JSON 往返保留未知键的语义和值，但会统一格式化为空格缩进。

0.1.1 起，Billboard 与 Ring 导入时都会翻转纹理 V 轴，以匹配 Minecraft 与 Blender 不同的图像坐标约定。0.1.15 起，EEVEE 一致性预览采用“抖动”透明：避免“混合”模式只有对象级排序而导致透明像素遮挡后方图元。0.1.17 起，带独立内外纹理的 Ring 在 Blender 中使用单层双面几何，并在材质节点中按 `Backfacing` 选择观察侧贴图；JSON 与导出仍保留模组所需的 `outer_texture`/`inner_texture`。材质通过 `Light Path.Is Camera Ray` 隔离非摄像机射线，使 full-bright/glowing 预览不再产生 Cycles 间接照明、反射或阴影。旧版 `.blend` 的双层 Ring 会在加载或点击对应渲染器的“刷新材质”时自动升级。
