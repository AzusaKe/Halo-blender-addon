# Changelog

本项目的重要变化记录于此。版本号与 GitHub Release 标签保持一致。

## [0.5.3] - 2026-09-29

### 新增

- 在“项目 / 资源包”面板直接编辑最终 `pack.mcmeta` 的资源包描述，同时保留兼容范围及未知元数据。
- 可导入 PNG、JPEG、WebP、BMP 或 TGA 作为资源包封面；非 PNG 图片自动转换并以根目录 `pack.png` 导出。
- 自定义封面作为本地编辑资源随 `.blend` 内嵌，合并多个来源时始终作为最终覆盖层；也可一键恢复使用来源包封面。

### 验证

- 57 项纯 Python 测试通过。
- Blender 5.2 多来源合并测试覆盖描述编辑、未知元数据保留、封面导入和 ZIP 导出。

## [0.5.2] - 2026-09-27

### 新增

- 为 Billboard、Ring 与 Halo 2.x 原生 Mesh 图元增加 labPBR 1.3 法线贴图（`_n`）和高光贴图（`_s`）导入界面。
- 直接集成 labPBR 1.3 Surface/Decoder 节点图，预览法线、高度、AO、粗糙度、金属、多孔性、SSS 与 `_s` Alpha 自发光，无需安装额外 Blender 扩展。
- 支持普通资源 PNG 及仅存在于 `.blend` 中的编辑器生成固有色作为 PBR 基础贴图。

### 行为与兼容性

- PBR 导入要求先选择有效固有色，并固定写为 `<固有色文件名>_n.png` / `<固有色文件名>_s.png`；原始导入文件名不会进入资源 ID。
- 更换固有色时自动复制已有 `_n/_s` 到新材质族。共享旧固有色的其他图元仍可继续使用旧文件，最终导出仅保留 JSON 可达的材质族。
- 已存在 PBR 旁车时，重复选择同一固有色会复用原资源，不再错误生成数字后缀。
- 导入 PBR 后，按“来源缓存 + 实际固有色文件”查找全部共享图元，将各自所属组写为 `glowing: false` 并立即刷新组内预览；其他导入包中碰巧同名的资源 ID 不受影响。
- `_n/_s` Image 使用 Non-Color；高光贴图使用 Closest。图片与 `.png.mcmeta` 参与 `.blend` 内嵌、缓存恢复、命名空间迁移及导出可达性清理。

### 验证

- 54 项纯 Python 测试通过。
- Blender 5.2 Extension 源码及 ZIP validate/build 通过。
- Hina、原生 Mesh、合并项目、动画、命名空间迁移、资源持久化、保存重开、EEVEE/Cycles 渲染回归通过。
- Halo Java 解析器交叉检查：`JAVA_PARSER_OK 5`。
- 隔离的“从磁盘安装”测试：`INSTALLED_PACKAGE_OK`。

## [0.5.1] - 2026-09-16

- 将生成的 Minecraft 资源包兼容元数据扩展至 Java 1.20、26.3 及后续格式。
- 默认开放上界改为 `2147483647`，并迁移插件旧版生成的 26.2 默认上界。
- Java 解析器检查支持 `GRADLE_USER_HOME`。

## [0.5.0] - 2026-09-14

- 支持从当前 Blender 项目 Mesh 生成 Halo 2.x 原生三角 OBJ。
- 增加直接图像导入与 Cycles 材质烘焙，并修复源 UV 与烘焙 UV 相互覆盖的问题。

[0.5.3]: https://github.com/AzusaKe/Halo-blender-addon/releases/tag/v0.5.3
[0.5.2]: https://github.com/AzusaKe/Halo-blender-addon/releases/tag/v0.5.2
[0.5.1]: https://github.com/AzusaKe/Halo-blender-addon/releases/tag/v0.5.1
[0.5.0]: https://github.com/AzusaKe/Halo-blender-addon/releases/tag/v0.5.0
