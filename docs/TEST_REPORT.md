# Halo Pack Editor 0.1.3 测试报告

测试日期：2026-08-26  
宿主：Blender 5.2.0 LTS（`fbe6228777e7`）  
交付包：`dist/halo_pack_editor-0.1.3.zip`  
SHA-256：`150B39A334C3E1C13657BF5EBBF3188CA36E70FFFB8F8276E9A9E694C5733CCE`

## 自动化结果

- 纯 Python：17 项测试全部通过。覆盖未知 JSON 字段与旧式写法保留、ZIP 安全、原子导出、资源路径与 PNG/labPBR、坐标矩阵、YXZ 旋转、动画函数、继承、过渡补值/反演/缓动和 `degrees` 行程。
- Blender Extension：源码 manifest 校验、ZIP 构建、构建包校验均通过。
- 全新安装：在 `F:\codex-cache\halo-blender-addon\isolated-0.1.3-20260826` 隔离用户目录中执行 `extension install-file --enable`，成功以 `bl_ext.user_default.halo_pack_editor` 加载并注册 `Scene.halo_project`。
- Hina：导入 57 个组、29 个图元、至少四层结构；检查 Billboard 法线/UV、Ring 表面数与剔除、材质 Alpha 乘法节点；场景同步、ZIP 往返、原子文件夹往返、保存 `.blend` 与重新打开均通过。
- Shiroko：startup 与 `id_overrides` 在 Blender 时间轴求值无错误，`degrees` 字段由核心求值器验收；从真实常驻动画通道读取既有项，并通过逐行操作器完成新增、编辑、排序与删除测试。
- 动画 JSON 编辑：成功把 startup 打开为 Blender Text 数据块，在多行 Text Editor 中修改并通过侧栏操作器重新解析、应用；无效 JSON 会阻止应用并报告错误。
- `ring_face_camera_test`：成功导入，3D View draw handler 已注册并识别 `face_camera` 图元。
- Rio：确认同组多图元。
- Toki：确认 `glowing:false`，非发光中性环境预览路径通过。
- Ring_default：确认文件名与内部 ID 不一致时以内置 ID 为准，并产生预期缺贴图警告。
- 合并包：Blender 导入 60 个定义、导出 ZIP、核心重新导入 60 个定义；164 个资源文件保留，7 条诊断均为样例中的预期缺贴图。
- Java 交叉验证：使用 `F:\Halo` 已编译的 `HaloDefinitionDeserializer` 只读解析 Blender 导出的合并包，结果 `JAVA_PARSER_OK 60`。
- 0.1.2 视觉修复继续保留：Billboard 与 Ring 的全部 UV 均断言执行 Minecraft→Blender 的 V 轴翻转；透明重叠和透明背面维持 0.1.0 的关闭状态，仅保留材质方向修复。
- 0.1.3 动画编辑 UI：现有常驻动画项会直接列出；面板不再暴露内部项索引；选择图元时会自动编辑其父组动画；startup/shutdown 使用真正的多行 Text Editor，而不是单行字符串字段。

## 人工验收边界

本轮未自动启动 Minecraft 图形客户端，因此“与游戏画面逐像素人工对照”仍属于发布前人工检查项。Blender 几何、坐标、动画和 Java 解析兼容性已由上述自动化覆盖；OBJ/通用网格烘焙按计划留到第二阶段。

当前仍有一项已知预览差异：定义了 startup 但缺少 shutdown 时，“关闭过渡”与“完整序列”尚未按模组行为反演 startup。本轮按要求只记录、不修复；详见 `docs/KNOWN_ISSUES.md`。
