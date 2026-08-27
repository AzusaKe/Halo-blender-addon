# Halo Pack Editor

Halo Pack Editor 是面向 Halo Minecraft 模组资源包的 Blender 5.2 LTS 扩展。它可以导入、预览、编辑并重新导出资源包中的光环定义，同时保留资源包内未知文件和 JSON 未识别字段。

## 功能

- 导入或导出资源包 ZIP、解包目录，以及包含多个命名空间和光环的整包。
- 使用 Blender Outliner 编辑与模组一致的组/图元父子树。
- 精确生成 billboard 与带内外表面的 ring，解析 Minecraft 资源路径和 PNG 透明材质。
- 编辑位置、YXZ 旋转、统一缩放、发光与 alpha/glow 继承等定义属性。
- 编辑 `damping` 跟踪参数与角动量开关；当前仅做字段编辑和无损导入导出，不模拟阻尼运动。
- 编辑并预览 `sin`、`cos`、`linear` 常驻动画和 startup/shutdown 过渡动画。
- 在光环局部空间或标准 Minecraft 玩家头部空间中预览。
- 从空白新建资源包、光环、组和图元，并导入或重链接 PNG 贴图。
- 在当前父级复制组或图元；也可把图元迁移到目标父级下自动创建的属性副本组中。

OBJ/通用网格烘焙转换不包含在 0.1.x 中。

## 安装

1. 使用 Blender 5.2 LTS 或更新版本。
2. 打开“编辑 → 偏好设置 → 扩展”，选择“从磁盘安装”。
3. 选择 `halo_pack_editor-0.1.13.zip` 并启用扩展。
4. 在 3D 视图按 `N`，打开“Halo 光环”标签页。

## 基本工作流

1. 在“项目”面板选择“导入资源包 ZIP”或“导入资源包文件夹”。
2. 在中文 Blender 的“大纲视图”（默认布局右上角的对象树）中选择光环根、部件组或图元；3D 视图右侧栏中的属性面板会切换到对应编辑内容。若没有看到右侧栏，把鼠标移到 3D 视图后按 `N`，再点击右侧竖排的“Halo 光环”标签。
   - 在“光环属性”中修改 Definition ID 会原子更新当前选择、完整对象树、原始 JSON 与导出 ID；重命名后无需重新选择或重新导入。空 ID 和重复 ID 会被拒绝。
   - 静态 JSON 变换以侧栏面板为唯一数据源。受管理的 Root、Group 和 Primitive 会锁定 Blender 原生 G/R/S，防止产生无法保存或错误回写的临时状态。
   - 选择部件组或 Billboard/Ring 图元时，可使用“粗调/细调”以及各轴的 `−/+` 控件编辑所属组的位置、YXZ 旋转和统一缩放；步长可分别设置。
   - 同组多个图元按照 Halo JSON 语义共享所属组变换。
   - 图元类型、纹理、尺寸、Ring 分段数和 `face_camera` 修改后会自动刷新预览，“强制刷新”用于手动恢复。
   - Ring 提供独立的“导入外侧 PNG”和“导入内侧 PNG”；“使用外侧”会清空 `inner_texture`，恢复模组使用外侧纹理绘制双面的默认行为。
   - 关闭 `face_camera` 时会从面板字段重新构建图元并恢复单位局部变换，清除预览摄像机旋转。
3. 要把一个组设为另一个组的子组：在“大纲视图”或 3D 视图中选择要移动的组，在 3D 视图右侧栏的“Halo 光环”标签中展开“树形编辑”，点击“选择新父级…”，在列表中选择目标组并确认。该面板现在始终可见：选中图元时可先点“选择所属部件组”，选中光环根时会提示继续选择其下的组。选择“光环根（顶层）”可移回顶层；当前组及其所有子组会自动从候选项中排除以避免循环。“保持世界位置”开启时会重新计算局部 JSON 变换来保持当前外观，关闭时保留原局部值。
4. 选择组或图元后，“部件 / 图元”面板底部的“在当前父级复制”会创建独立副本：组会连同完整子树复制，图元只复制自身；所有新节点都会获得新 UUID，组 ID 会自动改为唯一值。选择图元后，“迁移到其他父级…”会让你选择目标父级和新组 ID；扩展会复制原所属组的静态变换、常驻动画、发光/继承属性、未知字段以及 startup/shutdown `id_overrides`，创建新子组并仅把当前图元移入其中，原组的其他图元和子组不受影响。
5. 编辑常驻动画时，先选择组（也可选择该组下的图元），再选择通道；现有项会逐条显示，可直接编辑、排序、删除或新增，无需填写索引。
6. startup/shutdown 在“过渡动画”面板中图形化编辑：先选择启动/关闭和组 ID，再添加、排序或删除段；每段可设置时间、缓动，并添加 `offset`、`scale`、`alpha`、`rotation` 通道。通道支持 `from`/`to`、独立时间、独立缓动以及 rotation `degrees`。完整 JSON 入口继续用于高级字段与故障修复。
7. 使用“动画预览”选择常驻、启动、关闭或完整序列，并播放 Blender 时间轴。
8. 在“验证与导出”中先运行验证，再导出 ZIP 或文件夹。

导入不会修改原资源包。导出默认拒绝覆盖已存在目标；需要覆盖时必须显式启用。

## 坐标约定

- Minecraft `(x, y, z)` 映射到 Blender `(x, -z, y)`。
- JSON 旋转按 `[yaw, pitch, roll]`，采用 YXZ 顺序并以度为单位。
- 玩家头部预览按模组矩阵顺序先应用头部锚点和整体缩放，再在该局部坐标系内应用光环根动画的位移、旋转与缩放。
- billboard 在定义局部 XZ 平面，法线为 `-Y`。
- ring 的 `size` 是 `[radius, axial_width]`，不是径向厚度。

## JSON 兼容

类型化编辑器以 Halo schema `1.0.10` 为准。原始 JSON AST 会随项目保存；未知键、旧版 `primitive`/`shape` 写法和未识别字段在导出时保留。高级编辑器允许直接修改完整 JSON，应用前会重新解析并验证。

JSON 会统一输出为 UTF-8、两空格缩进；不保证原始空白逐字节不变。

## 开发与测试

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
blender --command extension validate halo_pack_editor
blender --command extension build --source-dir halo_pack_editor --output-filepath dist\halo_pack_editor-0.1.13.zip
```

Blender 后台集成测试脚本位于 `scripts/`。测试与下载缓存应放在 `F:\codex-cache\halo-blender-addon`，扩展自身不会依赖该路径。

已执行的验收结果见 `docs/TEST_REPORT.md`。

当前已知限制见 `docs/KNOWN_ISSUES.md`。
