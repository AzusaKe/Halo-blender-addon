# Halo Pack Editor

Halo Pack Editor 是面向 Halo Minecraft 模组资源包的 Blender 5.2 LTS 扩展。它可以导入、预览、编辑并重新导出资源包中的光环定义，同时保留资源包内未知文件和 JSON 未识别字段。

## 功能

- 导入或导出资源包 ZIP、解包目录，以及包含多个命名空间和光环的整包。
- 使用 Blender Outliner 编辑与模组一致的组/图元父子树。
- 精确生成 billboard 与带内外表面的 ring，解析 Minecraft 资源路径和 PNG 透明材质。
- 编辑位置、YXZ 旋转、统一缩放、发光与 alpha/glow 继承等定义属性。
- 编辑并预览 `sin`、`cos`、`linear` 常驻动画和 startup/shutdown 过渡动画。
- 在光环局部空间或标准 Minecraft 玩家头部空间中预览。
- 从空白新建资源包、光环、组和图元，并导入或重链接 PNG 贴图。

OBJ/通用网格烘焙转换不包含在 0.1.x 中。

## 安装

1. 使用 Blender 5.2 LTS 或更新版本。
2. 打开“编辑 → 偏好设置 → 扩展”，选择“从磁盘安装”。
3. 选择 `halo_pack_editor-0.1.3.zip` 并启用扩展。
4. 在 3D 视图按 `N`，打开“Halo 光环”标签页。

## 基本工作流

1. 在“项目”面板选择“导入资源包 ZIP”或“导入资源包文件夹”。
2. 在 Outliner 中选择 Definition Root、Group 或 Primitive；属性面板会切换到对应编辑内容。
3. 编辑常驻动画时，先选择组（也可选择该组下的图元），再选择通道；现有项会逐条显示，可直接编辑、排序、删除或新增，无需填写索引。
4. startup/shutdown 可从“过渡动画”面板打开 Blender 多行 Text Editor，编辑后在 Text Editor 侧栏点击“应用动画 JSON”。
5. 使用“动画预览”选择常驻、启动、关闭或完整序列，并播放 Blender 时间轴。
6. 在“验证与导出”中先运行验证，再导出 ZIP 或文件夹。

导入不会修改原资源包。导出默认拒绝覆盖已存在目标；需要覆盖时必须显式启用。

## 坐标约定

- Minecraft `(x, y, z)` 映射到 Blender `(x, -z, y)`。
- JSON 旋转按 `[yaw, pitch, roll]`，采用 YXZ 顺序并以度为单位。
- billboard 在定义局部 XZ 平面，法线为 `-Y`。
- ring 的 `size` 是 `[radius, axial_width]`，不是径向厚度。

## JSON 兼容

类型化编辑器以 Halo schema `1.0.10` 为准。原始 JSON AST 会随项目保存；未知键、旧版 `primitive`/`shape` 写法和未识别字段在导出时保留。高级编辑器允许直接修改完整 JSON，应用前会重新解析并验证。

JSON 会统一输出为 UTF-8、两空格缩进；不保证原始空白逐字节不变。

## 开发与测试

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
blender --command extension validate halo_pack_editor
blender --command extension build --source-dir halo_pack_editor --output-filepath dist\halo_pack_editor-0.1.3.zip
```

Blender 后台集成测试脚本位于 `scripts/`。测试与下载缓存应放在 `F:\codex-cache\halo-blender-addon`，扩展自身不会依赖该路径。

已执行的验收结果见 `docs/TEST_REPORT.md`。

当前已知限制见 `docs/KNOWN_ISSUES.md`。
