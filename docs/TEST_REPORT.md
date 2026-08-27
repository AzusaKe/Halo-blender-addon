# Halo Pack Editor 0.2.1 测试报告

测试日期：2026-08-27
宿主：Blender 5.2.0 LTS（`fbe6228777e7`）  
交付包：`dist/halo_pack_editor-0.2.1.zip`
SHA-256：`40ECBB43EF99A9D95CF3DC6BF5A759FA62E715E7A6F04B3F1979FA392D12A6AB`

## 自动化结果

- 纯 Python：17 项测试全部通过。覆盖未知 JSON 字段与旧式写法保留、ZIP 安全、原子导出、资源路径与 PNG/labPBR、坐标矩阵、YXZ 旋转、动画函数、继承、过渡补值/反演/缓动和 `degrees` 行程。
- Blender Extension：源码 manifest 校验、ZIP 构建、构建包校验均通过。
- 全新安装：在 `F:\codex-cache\halo-blender-addon\isolated-0.2.1-20260827` 隔离用户目录中执行 `extension install-file --enable`，确认扩展可加载、Cycles 可启用，并可执行材质刷新与 Mesh 转换操作器。
- Hina：导入 57 个组、29 个图元、至少四层结构；检查 Billboard 法线/UV、Ring 表面数与剔除、材质 Alpha 乘法节点；场景同步、ZIP 往返、原子文件夹往返、保存 `.blend` 与重新打开均通过。
- Shiroko：startup 与 `id_overrides` 在 Blender 时间轴求值无错误，`degrees` 字段由核心求值器验收；从真实常驻动画通道读取既有项，并通过逐行操作器完成新增、编辑、排序与删除测试。
- 动画 JSON 编辑：成功把 startup 打开为 Blender Text 数据块，在多行 Text Editor 中修改并通过侧栏操作器重新解析、应用；无效 JSON 会阻止应用并报告错误。
- `ring_face_camera_test`：成功导入，3D View draw handler 已注册并识别 `face_camera` 图元。
- Rio：确认同组多图元。
- Toki：确认 `glowing:false`，非发光中性环境预览路径通过。
- Ring_default：确认文件名与内部 ID 不一致时以内置 ID 为准，并产生预期缺贴图警告。
- 合并包：Blender 导入 60 个定义、导出 ZIP、核心重新导入 60 个定义；164 个资源文件保留，7 条诊断均为样例中的预期缺贴图。
- Java 交叉验证：使用 `F:\Halo` 已编译的 `HaloDefinitionDeserializer` 只读解析 Blender 导出的合并包，结果 `JAVA_PARSER_OK 60`。
- 0.1.2 视觉修复继续保留：Billboard 与 Ring 的全部 UV 均断言执行 Minecraft→Blender 的 V 轴翻转。
- 0.1.3 动画编辑 UI：现有常驻动画项会直接列出；面板不再暴露内部项索引；选择图元时会自动编辑其父组动画；startup/shutdown 使用真正的多行 Text Editor，而不是单行字符串字段。
- 0.1.5 面板权威变换：Root、Group、Primitive 的原生 G/R/S 均锁定；即使脚本强行修改 Group 对象，场景同步和导出仍以面板 MC 位置/YXZ 旋转/统一缩放为准并恢复对象外观。
- 0.1.5 粗调/细调：断言可切换两组独立步长，并通过各轴 `−/+` 操作器精确修改所属组变换，结果即时写入预览与 JSON。
- 0.1.5 `face_camera` 修复：断言开启后模拟摄像机旋转与意外局部位移，再关闭属性会重新构建 Mesh/材质、恢复图元单位变换且不改变父组位置；“强制刷新”执行相同复位流程。
- 0.1.5 即时预览继续保留：图元类型、纹理、尺寸、Ring 分段数、`face_camera` 与组 `glowing` 修改均会自动刷新。
- 0.1.6 Definition ID 重命名：在 Hina 上修改当前 ID 后，断言项目当前选择、Definition PropertyGroup、Root、57 个 Group、29 个 Primitive、Root 原始 JSON 和 Definition 原始 JSON 同步更新；导出 ZIP 再由核心导入后仍取得新内部 ID。空 ID 与包内重复 ID 会回退并显示错误。
- 0.1.7 Ring 内侧材质：实际清空 `inner_texture` 后断言 Mesh 回到单表面/单材质槽；随后通过“导入内侧 PNG”选择另一张真实贴图，断言双表面、第二材质槽、内侧 polygon material index 和导出 `inner_texture` 全部恢复。
- 0.1.8 根动画局部坐标：在 MC 玩家头部预览中设置非零 yaw/pitch/roll、非单位 `positioning.scale`，再注入固定根位移与 YXZ 旋转；断言 Blender 结果严格符合模组的 `T(anchor) × R(anchor) × S(positioning) × T/R/S(root animation)` 顺序，并明确不再等于世界轴位移结果。
- 0.1.9 图形化过渡编辑：使用真实组 ID 创建 startup/shutdown `id_overrides`，覆盖段新增、编辑、排序、删除和覆盖清除；覆盖 `offset/scale/alpha/rotation` 通道端点、属性级 duration/easing 与 rotation `degrees`。断言 startup 首个活动通道缺 `from`、shutdown 最后活动通道缺 `to` 时拒绝保存，并确认未知顶层 JSON 字段保留。
- 0.1.10 阻尼编辑：在 Hina 定义中图形化修改六个 `damping` 数值和 `allow_angular_momentum`，断言 Definition/Root 两份 JSON 立即同步；特别覆盖 `linearFactor: 0` 与 `maxLinearDistance: 0`，确认不会被默认值替换，并在 ZIP 导出重导入后保留未知 `damping` 子字段。
- 0.1.11 阻尼提示与树形编辑：修正三个 factor 的提示方向为“越接近 1 越快”；在真实 Hina 树中确认父级列表包含光环根和合法同定义组，排除当前组及全部子组，并完成保留局部值的“顶层组 → 另一个组 → 光环根”往返。
- 0.1.12 树形面板可发现性：移除“仅选中组才显示”的面板条件，使“树形编辑”在 3D 视图右侧栏的“Halo 光环”标签中始终可见并提前到动画面板之前；分别为未选择 Halo 对象、光环根、图元和组提供中文引导，图元可一键选择其所属部件组。
- 0.1.13 复制与图元迁移：在 Hina 真实层级中分别复制完整组子树和单个图元，断言父级不变、所有副本 UUID 唯一且 PropertyGroup UUID 同步、全部复制组 ID 与原树无冲突、未知字段保留。随后把图元副本迁移到另一组下的新属性副本组，断言只移动所选图元，静态变换、常驻动画、未知字段及 startup `id_overrides` 均复制到新组 ID，并清理测试对象后维持 57 组/29 图元的原始导出结果。
- 0.1.14 移动属性选择：组重设父级和图元迁移的属性选项默认全部启用；另以选择性测试取消位置、缩放、常驻动画、发光/继承和扩展字段，仅保留旋转，确认未选字段重置为 schema 默认值且原局部旋转精确保留。图元迁移另覆盖全部取消，确认新包装组使用单位变换、空动画、默认渲染/继承、删除未知字段且不复制 startup `id_overrides`。
- 0.1.15 EEVEE 一致性预览：修复 Blender 5.2 中旧 `blend_method=BLEND` 覆盖 `surface_render_method=DITHERED` 的问题；断言新建和旧场景升级后的全部 Halo 材质均保持抖动透明。新增真实离屏渲染测试：两个同原点图元前后重叠时，前图透明半幅正确显示绿色后图而非世界背景；显式内外纹理 Ring 从外部观察只显示红色外表面，蓝色共面内表面被背面剔除。
- 0.1.16 Cycles 一致性预览：显式内外纹理 Ring 保持完全共面几何，在材质节点中按 `Geometry.Backfacing` 选择观察侧贴图；真实 Cycles 离屏渲染断言从圆环外部只显示红色外纹理、从圆环内部只显示蓝色内纹理。旧 0.1.15 材质可从对象的双材质槽恢复配对关系并自动升级，同时 EEVEE 透明与外侧剔除回归继续通过。
- 0.1.17 Cycles 共面与发光修复：显式内外纹理 Ring 的 Blender 预览改为单层圆柱面、单材质槽，通过 `Geometry.Backfacing` 选择两张纹理；JSON 和导出仍保留两个资源 ID。真实 EEVEE/Cycles 渲染分别验证外视红色、内视蓝色。使用强度 1000 的 Halo 发光面和白色接收面验证 `Light Path.Is Camera Ray`：正常材质接收面保持全黑，绕过隔离节点的阳性对照达到明显照明，确认 Halo 不再产生间接发光。旧版双层 Ring 刷新后自动折叠为单层。
- 0.2.0 Mesh 转子组：在 Hina 定义根下转换一个具有水平三角形、竖直四边形、两种 Principled 材质、UV、非默认对象变换的源 Mesh。断言对象 G/R/S 被忽略；包装组使用默认变换；两个面各生成扁平子组、固定朝向 Billboard 和独立透明 PNG；水平面法线为 +Z、竖直面法线为 +X，局部顶点与源面最小覆盖矩形一致；三角形矩形外 alpha 为零，蓝色材质保持蓝色。生成贴图不写入导入源包，但包含在 ZIP 导出中。
- 0.2.0 保存重开：把转换后的层级及生成贴图保存为 `.blend`，重新打开后确认两张贴图均为 packed image，随后再次导出 ZIP 并确认资源路径完整。
- 0.2.0 任意朝向旋转：修正 Blender 矩阵回写 Halo `Ry × Rx × Rz` 的逆解，避免非水平面通过通用 Euler 转换后朝向偏差；上述水平/竖直几何重建测试覆盖此路径。
- 0.2.1 源 UV 精确采样：构造一张仅右上区域为绿色、其余区域为红色的分区纹理，材质通过 Texture Coordinate 的普通 `UV` 输出连接 Image Texture，源三角形 UV 全部限制在绿色小区域。转换后统计全部不透明像素，确认绿色总量超过红色四倍，证明烘焙没有再把整张纹理映射到目标矩形。目标烘焙使用显式 UV Map，源活动 UV 及全部命名 UV Map 保持独立。

## 人工验收边界

本轮未自动启动 Minecraft 图形客户端，因此“与游戏画面逐像素人工对照”仍属于发布前人工检查项。Blender 几何、坐标、动画和 Java 解析兼容性已由上述自动化覆盖。Mesh 转换已覆盖场景内 Mesh；OBJ 等外部格式可先用 Blender 自身导入，再调用“Mesh 转子组”。复杂材质与非平面 n-gon 的边界见 `docs/KNOWN_ISSUES.md`。

当前仍有一项已知预览差异：定义了 startup 但缺少 shutdown 时，“关闭过渡”与“完整序列”尚未按模组行为反演 startup。本轮按要求只记录、不修复；详见 `docs/KNOWN_ISSUES.md`。
