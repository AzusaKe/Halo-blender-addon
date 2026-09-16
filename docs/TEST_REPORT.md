# Halo Pack Editor 0.5.1 测试报告

测试日期：2026-09-16
宿主：Blender 5.2.0 LTS（`fbe6228777e7`）  
交付包：`dist/halo_pack_editor-0.5.1.zip`
SHA-256：`C0D581860BBE3A15F6943864FE1FB6E78C3F7186FBC73B64FE2FA4E4652DABA2`

## 0.5.1 资源包兼容元数据维护

- 跟进 Minecraft Java 26.3 的资源包版本 97.1，将新建、缺省导出与 HaloPackTool 生成的兼容上界从 26.2 格式 88 扩展为实用开放上界 `2147483647`；旧版 `pack_format = 15`、跨版本 `supported_formats` 以及新版 `min_format` / `max_format` 同时保留。
- 插件在导出旧 `.blend` 时会迁移自身曾生成的精确默认值 `supported_formats.max_inclusive = 88` 与 `max_format = [88,0]`，但保留用户设置的其他自定义兼容范围。52 项插件纯 Python 测试、14 项 HaloPackTool 测试、Blender Extension 源码校验、场景导出和多来源合并测试均通过并断言开放上界。
- Java 解析器交叉检查不再假设 Gradle 缓存位于 C 盘用户目录：优先读取 `GRADLE_USER_HOME`，未设置时才使用当前用户的 `.gradle`，以适配缓存迁移后的发布环境。
- 完整 `scripts/build.ps1` 通过，包括 `JAVA_PARSER_OK 5`、动画与资源持久化、保存重开以及 EEVEE/Cycles 渲染；最终 ZIP 在隔离配置 `F:\codex-cache\halo-blender-addon\isolated-0.5.1-5dff7380eb96490d83ccf25cf6fd4424` 通过 `extension install-file` 安装和 `INSTALLED_PACKAGE_OK` 回归，未改动用户当前安装。

## 0.5.0 项目 Mesh 转原生 OBJ 专项验收

- 在 Halo 原生 Mesh 图元面板加入“项目 Mesh…”：从当前场景 Collection 选择普通 Mesh，可求值修改器，并可在“保持场景外观”和“仅源网格局部坐标”间切换。前者通过目标父组逆矩阵把对象 G/R/S 写入 OBJ 顶点，Blender→Minecraft→Blender 往返后的四个世界坐标与源对象逐项一致。
- 自定义 OBJ 写出器只生成 `v`、`vt`、三角 `f`；四边面实测输出 2 个三角形，并在复制到资源缓存前通过与 Halo 导入一致的 `parse_obj` 完整验证。退化三角形跳过并提醒，无 UV 的无材质模型写入合法占位 UV，不导出 MTL、骨骼、动画或其他不支持语句。
- “直接导入图像”实测从已打包 Blender Image 读取像素、保留活动 UV、生成当前定义命名空间下的 PNG 资源并立即重建预览，全程不启动 Cycles。同图像内容可复用已有生成资源；外部 PNG 继续由现有图片家族逻辑携带 labPBR 与元数据。
- “Cycles 烘焙”实测重新生成 Smart UV，以 32×32、2 像素 margin 对 Principled 材质完成颜色与 Alpha 两次烘焙，生成图片打包进 `.blend` 并进入最终 ZIP；源对象位置、Mesh 拓扑和原 UV 数据保持不变。AUTO 模式在全 Principled、全 Emission 和复杂/混合材质之间选择基础色、发光或综合烘焙。
- 修复 Smart UV 同时替换源材质采样 UV 导致贴图切碎的问题：专项把源四边面全部角固定采样同一蓝色像素，同时生成独立烘焙 UV；最终 PNG 的可见像素不出现原图其他红/绿色，证明源采样 UV 与目标 UV 已隔离。直接导入专项在 Image Texture 与 Principled 之间插入实际 `ShaderNodeMix`，确认可提取唯一图像并给出忽略节点运算提醒，不再强制回退 Cycles。
- 完整 `scripts/build.ps1` 通过：51 项纯 Python、Blender Hina/Mesh 转子组/项目 Mesh/原生 Mesh/多来源/动画/资源保存重开/EEVEE 与 Cycles 渲染、Extension 源码与 ZIP validate/build，以及 Halo Java 解析器 `JAVA_PARSER_OK 5`。日志中的批量选择、过渡边界和无效 JSON Error 是预期负向测试。
- 最终 ZIP 在隔离配置 `F:\codex-cache\halo-blender-addon\isolated-0.5.0-uvfix-20260914` 通过 `extension install-file` 安装启用，并以真实模块 `bl_ext.user_default.halo_pack_editor` 确认 `halo.import_scene_mesh` 注册及默认材质模式；未覆盖用户当前安装。Minecraft 客户端中的最终目视对照仍留给人工验收。

## 0.4.0 Halo 2.0 原生 Mesh 历史验收

- 跟进 Halo 2.x 的 Mesh 尺寸契约：面板可切换 `preserve_proportions` 并编辑统一 `scale`；开启时保留 OBJ 作者坐标与原点、允许 JSON 省略 `size`，关闭时继续逐轴拟合。基础纹理与 Alpha Mask 的尺寸及长宽比不再受限，插件同步移除了导入警告、面板提示和场景验证规则。
- Blender 5.2 专项同时导入 `mesh_preserve_demo` 与 `mesh_mask_resolution_demo`，验证统一缩放包围盒及保持比例 JSON 往返；另将 32×32 主纹理与任意 2×3 遮罩组合，确认导入、场景校验和导出不因分辨率关系报错。
- 以 `F:\Halo` 当前 2.0.0 代码及自带 `mesh_demo`、`mesh_mask_demo`、`mesh_step_demo` 为契约，新增 schema 1.1.0 `MeshPrimitive`、OBJ 子集解析、三轴尺寸拟合、材质与 alpha_mask 类型模型；原始 AST 中未知 Mesh/效果字段继续保留。
- 51 项纯 Python 测试通过。覆盖 Mesh/遮罩无损往返、保持比例 JSON 与验证、UV 动画求值、OBJ 正负索引、UV V 翻转、平面凸四边形三角化、非法语句/缺 UV/非平面拒绝，以及外部 OBJ 同内容复用和异内容数字后缀。
- Blender 5.2 后台专项导入五个真实定义、六个 Mesh 图元：每个模型取得 175 个渲染顶点、288 个三角形；逐轴拟合包围盒严格对应 Minecraft `size`，`mesh_preserve_demo` 严格对应原始 OBJ × `scale 0.4`，且开关切换实时重建；主纹理、单双面与 linear/step 遮罩节点均建立成功。
- 时间轴从第 1 帧到第 21 帧验证 U 偏移 `0 → 0.125`。图形化操作器完成 U/V 项新增与编辑；外部 OBJ/遮罩 PNG 使用所属定义命名空间，并在导出 JSON 中保持。共享纹理的多个 Mesh 使用独立材质，避免逐图元 UV uniform 串扰。
- 删除编辑缓存后从 `.blend` 来源快照恢复 OBJ、主纹理及遮罩材质，再导出 ZIP 成功；Halo 2.0 当前已编译 `HaloDefinitionDeserializer` 对五个导出定义交叉解析结果为 `JAVA_PARSER_OK 5`。
- 全量构建通过：51 项纯 Python、Hina/Mesh 转子组/原生 OBJ/多来源、动画 Text、Serina、命名空间迁移、贴图裁剪、六种资源恢复、保存重开、EEVEE/Cycles 渲染、Extension 源码及 ZIP validate/build 均成功。生成并验证 `F:\HaloBlenderAddon\dist\halo_pack_editor-0.4.0.zip`。日志中的批量选择、过渡边界与无效 JSON Error 均为预期负向测试。Minecraft 客户端中的最终目视对照仍留给人工验收。
- 最终 ZIP 在隔离配置 `F:\codex-cache\halo-blender-addon\isolated-0.4.0-final-20260912` 通过 `extension install-file` 安装，并以真实模块路径 `bl_ext.user_default.halo_pack_editor` 完成注册、新建项目、PNG 命名空间、定义文件名和 Serina 动画回归，未改动用户当前安装。

## 0.3.7 历史验收

## 0.3.7 ID 资源归属、动画播放与定义文件名专项验收

- PNG 操作器专项将默认 `minecraft:halo` 改为 `trinity:serina`，从外部导入 `serina_detail.png`，确认资源 ID 和落盘路径均使用 `trinity`；再次导入同一图片复用原文件，不产生数字后缀。该测试使用真实 Blender Image、操作器、资源缓存和材质重建路径。
- 使用真实 `F:\HaloPackTool\input\serina.zip` 验证常驻动画：20 FPS 下第 1～21 帧组位移从 0 变到 0.02、glow 从 0.75 变到 1.0；startup 第 1～6 帧缩放从 0 变到约 0.9625。播放准备会把任意当前帧重置为第 1 帧；startup 与无显式 shutdown 的反演时长均为 0.6 秒，反演关闭实际由缩放 1 播放到 0，完整序列为 2.2 秒。
- 定义输出文件名仅由 ID 决定：源路径即使为 `assets/minecraft/halo_definitions/halo.json`，`trinity:serina` 仍只导出为 `assets/trinity/halo_definitions/serina.json`。规范化后目标路径冲突由纯 Python、Blender 验证器及实际导出共同拒绝，并列出冲突 ID，不再静默添加文件名后缀。
- 全量构建通过：39 项纯 Python，Hina/Mesh/合并项目、资源持久化、命名空间迁移、贴图清理、动画 Text、EEVEE/Cycles、Extension validate/build 均成功。日志：`F:\codex-cache\halo-blender-addon\build-0.3.7-20260909.log`；日志中的批量选择、过渡边界和无效 JSON Error 均来自预期负向测试。
- 最终 ZIP 在隔离配置 `F:\codex-cache\halo-blender-addon\isolated-0.3.7-final-20260909` 通过 `extension install-file` 安装启用；实际模块 `bl_ext.user_default.halo_pack_editor` 再次验证 PNG 命名空间、ID 文件名及 Serina 常驻动画。日志：`F:\codex-cache\halo-blender-addon\installed-0.3.7-20260909.log`，未覆盖用户当前安装。

## 0.3.6 多行动画 JSON 持久化专项验收

- 根因：旧操作器只在当前 Text Editor 上下文中把文本写回部分 PropertyGroup；未建立独立动画文本指针，也没有接入 `.blend` 保存、加载和资源包导出，定义原始 AST/Root/组之间的副本亦可能不同步。文本编辑器右栏关闭时，应用入口不可见。
- 新增统一动画 Text 同步层：常驻、startup、shutdown 的场景、定义及组 UUID 绑定独立于完整定义 JSON；显式“应用动画 JSON”、“应用并返回 3D 视图”、播放预览、保存 `.blend`、加载旧 Text 和 ZIP/文件夹导出共用同一应用逻辑。定义 ID 重命名会同步更新打开的 Text 绑定。
- Blender 专项覆盖光环根常驻动画、组常驻动画、startup、shutdown：验证重复打开不丢待应用修改、显式应用、返回时应用、保存时自动应用、重命名后应用、保存重开、导出前自动应用，以及 Definition/Root/Group/导出 JSON 四层一致。
- 无效待应用 JSON 会保留文本且不覆盖最后一次有效动画；显式应用显示解析位置，导出拒绝继续。修复文本后可正常应用。文本块启用 fake user，随 `.blend` 保存。
- 全量 37 项纯 Python、Hina/Mesh/合并项目、资源持久化、命名空间迁移、EEVEE/Cycles、Extension validate/build 均通过。日志：`F:\codex-cache\halo-blender-addon\build-0.3.6-20260901.log`。负向无效 JSON 与缺失示例 PNG 提示均为预期验证，无 Traceback。
- 最终 ZIP 在 `F:\codex-cache\halo-blender-addon\isolated-0.3.6-20260901` 安装启用成功，使用实际安装模块 `bl_ext.user_default.halo_pack_editor` 重新完成动画 Text 专项。日志：`installed-animation-text-0.3.6-20260901.log`。0.3.6 后续已提交为 `0baa0d3`。

## 0.3.5 命名空间与贴图迁移专项验收

- 根因：Definition ID 回调原来只更新光环 ID、对象标记与 UI 选择，未迁移图片文件或贴图引用；仅靠导出清理不能将有效的旧命名空间引用变为新路径。
- 新增 6 项纯 Python 测试：PNG/labPBR/元数据家族字节保持、显式与省略扩展名引用合一、共享原文件保留、冲突后缀及重复迁移复用、多个来源的路径冲突保护、缺失主图及残存附图、默认 minecraft、旧式/未知 JSON 字段重映射及路径安全。总计 37 项通过。
- 新增 Blender 后台专项：空白光环缺图不阻止重命名；`a:b → d:b → e:new → e:renamed` 连续编辑后，Ring 内外贴图、跨命名空间材质、高级字段、仅内嵌的 Mesh PNG 同步到新命名空间。逐字节验证附图与烘焙 PNG；同包中另一隐藏光环及其原 Image/JSON 不变。仅改名称部分不迁移；重复 ID、非法命名空间及模拟文件复制失败保留原 ID。
- 预览材质链接已刷新且 PNG 已内嵌；ZIP 和文件夹仅含最终引用所需图片，共享原图片保留、无用中间命名空间排除。保存 `.blend` 再打开后再次导出通过。
- 全量构建、Hina/Mesh/合并项目、六种跨进程资源恢复测试、EEVEE/Cycles、Extension validate/build 均通过。日志：`F:\codex-cache\halo-blender-addon\build-0.3.5-20260831.log`；无 Traceback 或非预期错误，负向验证用例保留预期错误提示。
- 最终 ZIP 在隔离配置 `F:\codex-cache\halo-blender-addon\isolated-0.3.5-20260831` 安装并启用成功，使用实际安装模块 `bl_ext.user_default.halo_pack_editor` 完整重跑命名空间迁移专项通过。日志：`installed-namespace-0.3.5-20260831.log`；未覆盖用户安装，未修改用户实际 `.blend`。本轮与之前累计改动均未暂存、未提交。

## 0.3.4 无引用贴图导出清理专项验收

- 根因：导出先复制每个来源的完整编辑缓存，原先仅清理旧定义 JSON，未筛选其中 PNG；缓存与内嵌副本中保留的历史贴图因此进入成品包。
- 新增 7 项纯 Python 测试通过：旧命名空间筛除、嵌套/旧式图元/Ring 双面/跨命名空间/多定义引用、labPBR 和图片元数据、省略扩展名及默认 minecraft、缺图不按文件名误匹配、保留非贴图文件、路径越界及非字符串引用防护、按大小写精确引用已有文件。总计 31 项纯 Python 测试。
- `blender_export_texture_test.py` 模拟 `minecraft:halo → oldname:halo → newname:final`，每阶段改用新贴图，并保留历史 Image、旧来源 JSON 和缓存文件。ZIP/文件夹导出都只包含最终引用；空的 minecraft/oldname 目录消失，缓存原始字节、Image 数据块不变。
- 验证隐藏光环 Ring 内外贴图保留，跨光环共享贴图保留；删除隐藏光环后仅删除其独用内侧 PNG 家族，共用外侧仍导出；删除全部光环后不导出纹理目录内 PNG。`pack.png`、未知二进制文件和非纹理目录中的未知图片保持原样。
- Mesh 风格的打包图片也按最终 JSON 选择：当前图元与保留扩展字段引用的图片导出，无引用的旧命名空间生成图片不导出。保存 `.blend` 后从完整内嵌缓存恢复并再次导出，历史贴图不会重新混入。
- 全套构建、Hina/Mesh/多来源/六种跨进程资源测试、EEVEE/Cycles、Extension validate/build 通过。日志：`F:\codex-cache\halo-blender-addon\build-0.3.4-20260831.log`，无 ERROR/Traceback。
- 最终 ZIP 在 `F:\codex-cache\halo-blender-addon\isolated-0.3.4-20260831` 安装启用成功，实际安装模块从测试 `.blend` 输出 ZIP 和文件夹，确认旧贴图排除、共享与生成图片保留、来源缓存字节未改。未覆盖用户安装，本轮及之前改动均未提交。

## 0.3.3 PNG 复用与 Temp 清理专项验收

- 6 项新增纯 Python 用例通过：同 PNG 不加后缀、同名不同内容保留原文件并复用已有编号副本、已绑定的不同名称图片复用、labPBR/图片元数据一致性、不同来源/命名空间隔离、保护孤立附图及拒绝不存在的输入。全套纯 Python 测试现为 24 项。
- Blender 中将同一 PNG 重复导入另一个图元、Ring 外侧及内侧，确认资源 ID 相同、Image 数量不增长；外部文件改名但内容与附图相同仍复用。保存 `.blend` 变成相对路径后再次导入，继续复用原 Image。
- `cleanup_prepare` / `cleanup_reopen` 独立进程构造并重开旧工程：两张无原文件的 Halo Temp 图片（一张闲置、一张被图元使用）在恢复后自动删除 Image 数据块；可恢复的外侧/内侧、已打包图片、非 Halo Temp 图片与非 Temp 图片均保留。另验证仍有像素数据的图片不会删除。图元、材质节点及 JSON 贴图 ID 保留，缺图使用占位显示，保存重开后不复活旧失效 Image；Blender“打包资源”不再访问那些旧路径。
- 构建全量回归通过，包含 24 项纯 Python 测试、六种跨进程资源测试、Hina/Mesh/多来源编辑及重开、EEVEE/Cycles 渲染、Extension validate/build。日志：`F:\codex-cache\halo-blender-addon\build-0.3.3-20260831-final.log`，无 ERROR/Traceback；清理测试保留预期的缺图警告。
- 最终 ZIP 在 `F:\codex-cache\halo-blender-addon\isolated-0.3.3-20260831-verified` 从磁盘安装并启用成功；实际安装模块验证 PNG 导入复用、自动恢复与自动清理通过。已修正安装注册时访问 `_RestrictData.images` 的问题；没有改动用户正在使用的扩展安装。
- 本轮与 0.3.2 改动均保留在工作区，未提交；未对用户实际 `.blend` 执行删除操作。

## 0.3.2 贴图持久化专项验收

- 根因：普通 PNG 未打包，仅保存外部路径；旧版 PNG 导入还会建立系统 Temp 根。Mesh 烘焙图片虽已打包，但不能保护普通导入图片。另复现 Blender ImagePackedFile 保留旧路径，即使 Image 路径已修改，“打包资源”仍尝试访问失效 Temp 的问题。
- `blender_resource_persistence_test.py` 四个独立 Blender 进程模式通过：普通 PNG / Ring 内侧立即打包；保存自动内嵌三个来源（文件夹、ZIP、本地 PNG）的全部资源；随后把测试原包和缓存可恢复地移走；另一进程未启用扩展时就能从打包数据读取图片像素，启用后恢复完整缓存。
- 按来源逐字节比较所有资源，包括共享同 ID 但颜色不同的两张 PNG、Ring 内侧、`_n/_s/_e`、`.png.mcmeta`、`pack.png`、未知二进制文件及本地重链接 PNG。验证恢复后材质不串源、几何刷新不丢图、部分缓存丢失可以恢复，ZIP/文件夹导出资源字节一致。
- 旧工程回归：构造缺少来源列表、未打包图片、原 Temp 根失效且含闲置旧 Image 数据块的 `.blend`；新进程从仍在原位的 ZIP 恢复外侧/内侧及闲置图片，迁移到持久目录，执行“打包资源”无找不到文件报错。修复不重导入光环 JSON，不改变组的编辑数据。
- 内嵌 ZIP 路径穿越拒绝；损坏快照有诊断且保存不会覆盖它；真正不存在的图片有明确警告，且不会将占位棋盘格写成资源 PNG。未修改资源的快照 SHA-256 稳定；普通用户模型的图片不会被自动打包。保存/加载处理器注册与注销通过。
- 全量构建使用隔离 Blender 环境，不加载用户已安装的旧版扩展。日志：`F:\codex-cache\halo-blender-addon\build-0.3.2-20260831-final.log`。18 项纯 Python 测试、Hina/动画/Mesh/多来源/保存重开、EEVEE/Cycles 渲染及 Extension validate/build 全部通过；日志中无 ERROR/Traceback。
- 另在 `F:\codex-cache\halo-blender-addon\isolated-0.3.2-20260831` 安装最终 ZIP 并启用。使用实际安装的 `bl_ext.user_default.halo_pack_editor` 打开原包/缓存已移走的测试工程，验证自动恢复、Blender 打包资源和 ZIP 导出通过。未覆盖用户现有安装。
- 限制：没有取得用户实际损坏的 `.blend`，本轮以可复现同类路径丢失的工程验收。原包与图片字节均已丢失的旧工程无法凭 JSON 自动重建；需恢复原包位置或重链接 PNG。下列其他模组样例与 Java 解析项包含既有版本验收记录；本轮未运行 Minecraft 客户端目视对照。

## 自动化结果

- 纯 Python：39 项测试全部通过。覆盖命名空间贴图迁移、贴图引用筛选、PNG 内容复用及冲突保护、ID 派生定义文件名及冲突拒绝、空 shutdown 反演 startup、未知 JSON 字段与旧式写法保留、ZIP 安全、原子导出、资源路径与 PNG/labPBR、坐标矩阵、YXZ 旋转、动画函数、继承、过渡补值/反演/缓动和 `degrees` 行程。
- Blender Extension：源码 manifest 校验、ZIP 构建、构建包校验均通过。
- 全新安装：0.3.7 最终 ZIP 在隔离配置中安装启用成功，并完成 PNG 命名空间、ID 文件名导出和真实 Serina 动画测试，见上方专项验收。
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
- 0.2.2 边缘原色与扩张：以 2 圈扩张转换上述绿色三角形，断言不透明像素数量超过未扩张三角形的 50% 覆盖面积，同时全部可见像素的绿色通道值完全一致，且没有引入红色图集区域；确认扩张复制完整 RGBA，而非与透明黑或相邻颜色平均。
- 0.2.3 共面合并：把水平矩形拆为两个共享对角边、同材质的三角形，并与一个垂直异材质四边形一同转换。启用合并时断言三个源面聚类为 `[2, 1]`、最终仅生成两个子组和两张贴图；水平联合贴图的全部 alpha 均为不透明，证明共享对角线没有透明裂缝，同时颜色仍只来自两个三角形各自的原始 UV。关闭合并时同一聚类器返回三个独立面簇。
- 0.2.4 增量转换与取消：同一转换核心先返回 `PREPARED 0/2`，再只烘焙一个面簇并返回 `BAKING 1/2`；随后关闭迭代任务，断言临时 packed image、文件和部分 Halo 层级全部清理。同步脚本入口继续完整转换 3 个源面为 2 张贴图。隔离安装另验证进度属性、交互标记和取消操作器均已注册。
- 0.2.5 直接 UV 采样：断言操作器选项默认关闭；显式启用后，同一绿色图集材质按源 UV 直接生成贴图，全部有效像素保持一致且未采到红色区域；纯色 Principled 面也走直接路径。带 Mapping 节点的坐标链会被保守识别为不支持并回退 Cycles。
- 0.2.6 资源包描述补全：分别通过纯 Python 导出器和 Blender 场景导出器移除 `pack.description` 后导出，断言结果自动补为 `Halo Pack Editor export`、`pack_format` 保持 15、未知元数据保留；另断言已有自定义中文描述不会被覆盖。
- 0.2.7 当前光环图元总数：Hina 导入后按当前定义统计为 29；Mesh 转换临时新增两个 Billboard 后实时变为 31；删除转换层级后恢复 29。计数基于当前场景与定义 ID，不污染 JSON。
- 0.2.8 同父级批量编辑：在临时组下创建两个子组和两个图元；混选一个组和一个图元后批量复制，断言生成两个同父级、全新 UUID 的独立副本，再批量删除并恢复原子树。两个同级组一起移动到 Hina 既有组并移回时持续保持多选；两个同组图元迁移后进入同一个属性副本组且索引为 0/1。另断言跨父级复制、组/图元混选移动均整体拒绝，所有原对象父级不变；清理后 Hina 恢复 57 组/29 图元。
- 0.2.9 动画期间 `face_camera`：为 Billboard 父组注入每秒 45 度的常驻旋转，在两个相隔帧确认父组世界旋转确实变化；同时缓存一个固定 3D 视图四元数，并在每帧动画求值后断言 Billboard 世界朝向始终与该视图一致。
- 0.3.0 多来源合并：后台构造一个文件夹包和一个 ZIP 包，二者包含相同 `demo:halo` ID、相同纹理资源 ID 但不同 PNG，以及各自未知文件。文件夹导入后断言编辑根位于 Blender 用户数据缓存且源目录字节不变；连续导入后定义自动成为 `demo:halo`/`demo:halo_2`、两图元使用各自来源图片；清空 ZIP 缓存路径后确认能从原 ZIP 重新安全解包并重绑材质；再次新建重名光环得到 `_3` 并绑定独立“本地编辑资源”。合并导出后核心解析取得三个定义、两个来源未知文件及本地缓存文件；清除 `_3` 后导出确认定义不会复活，清除文件夹来源后只剩 ZIP 来源的 `_2` 定义与资源。另验证嵌套 definition 路径在导出清理范围内。
- 0.3.0 可见性与场景隔离：关闭第一个定义的预览可见性后，断言其 Root/Group/Primitive 全部同时设置视图与渲染隐藏，第二个定义及另一个 Blender Scene 中同 ID 对象不受影响；清除来源后第二场景对象仍存在。重新开启后完整恢复，导出的三个定义均不含 `visible` 字段。
- 0.3.1 资源包兼容范围（后于 2026-09-16 更新）：纯 Python、Blender 场景导出和多来源合并导出均断言 `pack_format = 15`、`supported_formats = 15～2147483647`、`min_format = [15,0]`、`max_format = 2147483647`，同时保留自定义描述和未知元数据。

## 人工验收边界

本轮未自动启动 Minecraft 图形客户端，因此“与游戏画面逐像素人工对照”仍属于发布前人工检查项。Blender 几何、坐标、动画和 Java 解析兼容性已由上述自动化覆盖。Mesh 转换已覆盖场景内 Mesh；OBJ 等外部格式可先用 Blender 自身导入，再调用“Mesh 转子组”。复杂材质与非平面 n-gon 的边界见 `docs/KNOWN_ISSUES.md`。

当前仍有一项已知预览差异：定义了 startup 但缺少 shutdown 时，“关闭过渡”与“完整序列”尚未按模组行为反演 startup。本轮按要求只记录、不修复；详见 `docs/KNOWN_ISSUES.md`。
