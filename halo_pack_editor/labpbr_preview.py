"""Native labPBR 1.3 node graphs integrated with the author's permission.

The implementation is shared with the standalone ``labpbr_nodes`` extension.
It has no runtime shader callbacks and is cached by explicit kind/revision
metadata, so both extensions can safely reuse the same Blender node groups.
"""

import bpy


DECODER_REVISION = 1
SURFACE_REVISION = 1
METALS = (
    (230, "Iron", (2.9114, 2.9497, 2.5845), (3.0893, 2.9318, 2.7670)),
    (231, "Gold", (0.18299, 0.42108, 1.3734), (3.4242, 2.3459, 1.7704)),
    (232, "Aluminum", (1.3456, 0.96521, 0.61722), (7.4746, 6.3995, 5.3031)),
    (233, "Chrome", (3.1071, 3.1812, 2.3230), (3.3314, 3.3291, 3.1350)),
    (234, "Copper", (0.27105, 0.67693, 1.3164), (3.6092, 2.6248, 2.2921)),
    (235, "Lead", (1.9100, 1.8300, 1.4400), (3.5100, 3.4000, 3.1800)),
    (236, "Platinum", (2.3757, 2.0847, 1.8453), (4.2655, 3.7153, 3.1365)),
    (237, "Silver", (0.15943, 0.14512, 0.13547), (3.9291, 3.1900, 2.3808)),
)


def metal_f0(n, k):
    return tuple(((a - 1) ** 2 + b * b) / ((a + 1) ** 2 + b * b)
                 for a, b in zip(n, k)) + (1.0,)


TEXTURE_INPUTS = (
    ("Albedo Color", "NodeSocketColor", (0.8, 0.8, 0.8, 1), None, None, "颜色贴图 Color；图像使用 sRGB"),
    ("Albedo Alpha", "NodeSocketFloat", 1, 0, 1, "颜色贴图 Alpha；1 为不透明"),
    ("Normal Color", "NodeSocketColor", (0.5, 0.5, 1, 1), None, None, "_n Color；Non-Color，RG 为 DirectX 法线，B 为 AO"),
    ("Normal Alpha", "NodeSocketFloat", 1, 0, 1, "_n Alpha；Channel Packed，1 为表面，0 为最深"),
    ("Specular Color", "NodeSocketColor", (0.5, 0.04, 0, 1), None, None, "_s Color；Non-Color，光滑度 / F0 或金属 / 多孔性或 SSS"),
    ("Specular Alpha", "NodeSocketFloat", 1, 0, 1, "_s Alpha；Channel Packed，255/255 表示不发光"),
)
CONTROLS = (
    ("Normal Strength", "NodeSocketFloat", 1, 0, 10, "切线空间法线强度"),
    ("Bump Distance", "NodeSocketFloat", 0, 0, 10, "最大凹凸距离（场景单位）；默认关闭"),
    ("Emission Strength", "NodeSocketFloat", 1, 0, 1000, "自发光亮度倍数"),
    ("AO Strength", "NodeSocketFloat", 1, 0, 1, "AO 乘基础颜色的预览近似强度"),
    ("Wetness", "NodeSocketFloat", 0, 0, 1, "湿润预览近似；不改变解码数据"),
    ("SSS Scale", "NodeSocketFloat", 0.005, 0, 10, "次表面散射尺度（场景单位）"),
)
DATA_OUTPUTS = (
    ("Normal", "NodeSocketVector", "解码法线（世界空间），含 Normal Strength，不含高度凹凸"),
    ("AO", "NodeSocketFloat", "原始材质 AO，1 为无遮挡"),
    ("Height", "NodeSocketFloat", "归一化高度 0..1；位移前自行确定物理尺度"),
    ("Linear Roughness", "NodeSocketFloat", "线性微表面粗糙度 (1-smoothness)^2"),
    ("F0", "NodeSocketColor", "介电质标量或金属 RGB 正入射反射率，金属已乘 Albedo 色调"),
    ("Metallic", "NodeSocketFloat", "金属分类掩码 0 或 1"),
    ("Porosity", "NodeSocketFloat", "解码多孔性 0..1"),
    ("SSS", "NodeSocketFloat", "解码次表面散射权重 0..1"),
    ("Emission Mask", "NodeSocketFloat", "解码自发光 0..1，255 特例已排除"),
    ("Alpha", "NodeSocketFloat", "基础颜色透明度"),
)


class Graph:
    def __init__(self, tree):
        self.tree = tree
        self.depth = {}

    def node(self, kind, label):
        node = self.tree.nodes.new(kind)
        node.label = label
        node.name = label
        node.width = 190
        return node

    def put(self, socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, operation, first, second=None, label=None):
        node = self.node("ShaderNodeMath", label or operation.title())
        node.operation = operation
        self.put(node.inputs[0], first)
        if second is not None:
            self.put(node.inputs[1], second)
        return node.outputs[0]

    def clamp(self, value):
        return self.math("MINIMUM", self.math("MAXIMUM", value, 0), 1)

    def color(self, blend, first, second, factor=1, label=None):
        node = self.node("ShaderNodeMixRGB", label or blend.title())
        node.blend_type = blend
        self.put(node.inputs[0], factor)
        self.put(node.inputs[1], first)
        self.put(node.inputs[2], second)
        return node.outputs[0]

    def separate(self, color, label):
        node = self.node("ShaderNodeSeparateColor", label)
        node.mode = "RGB"
        self.put(node.inputs[0], color)
        return node.outputs

    def layout(self):
        nodes = list(self.tree.nodes)
        parents = {node: [] for node in nodes}
        for link in self.tree.links:
            parents[link.to_node].append(link.from_node)

        def depth(node):
            if node not in self.depth:
                self.depth[node] = max((depth(parent) + 1 for parent in parents[node]), default=0)
            return self.depth[node]

        rows = {}
        for node in nodes:
            column = depth(node)
            row = rows.get(column, 0)
            node.location = (column * 250, -row)
            rows[column] = row + max(180, 85 + 25 * max(len(node.inputs), len(node.outputs)))


def _interface(tree, inputs, outputs):
    for name, kind, default, minimum, maximum, description in inputs:
        socket = tree.interface.new_socket(name=name, in_out="INPUT", socket_type=kind)
        socket.default_value = default
        if minimum is not None:
            socket.min_value, socket.max_value = minimum, maximum
        socket.description = description
    for name, kind, description in outputs:
        socket = tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=kind)
        socket.description = description


def _cached(kind, revision):
    return next((tree for tree in bpy.data.node_groups
                 if tree.bl_idname == "ShaderNodeTree" and not tree.library
                 and tree.get("labpbr_kind") == kind
                 and tree.get("labpbr_revision") == revision
                 and tree.get("labpbr_complete")), None)


def _new_tree(kind, revision):
    tree = bpy.data.node_groups.new(f"labPBR 1.3 · {kind} v{revision}", "ShaderNodeTree")
    tree["labpbr_kind"] = kind
    tree["labpbr_revision"] = revision
    tree["labpbr_standard"] = "1.3"
    return tree


def build_decoder():
    tree = _cached("Decode", DECODER_REVISION)
    if tree:
        return tree
    tree = _new_tree("Decode", DECODER_REVISION)
    try:
        _interface(tree, TEXTURE_INPUTS + CONTROLS[:1], DATA_OUTPUTS)
        graph = Graph(tree)
        inputs = graph.node("NodeGroupInput", "原始 labPBR 输入").outputs
        output = graph.node("NodeGroupOutput", "解码数据").inputs
        normal_r, normal_g, normal_b = graph.separate(inputs["Normal Color"], "分离 _n RGB")[:3]
        x_value = graph.math("SUBTRACT", graph.math("MULTIPLY", normal_r, 2), 1, "Normal X")
        y_value = graph.math("SUBTRACT", 1, graph.math("MULTIPLY", normal_g, 2), "DirectX Y → Blender Y")
        xy_squared = graph.math("ADD", graph.math("MULTIPLY", x_value, x_value), graph.math("MULTIPLY", y_value, y_value))
        z_value = graph.math("SQRT", graph.math("MAXIMUM", graph.math("SUBTRACT", 1, xy_squared), 0), label="重建安全 Z")
        vector = graph.node("ShaderNodeCombineXYZ", "切线法线 XYZ")
        for index, value in enumerate((x_value, y_value, z_value)):
            graph.put(vector.inputs[index], value)
        normalize = graph.node("ShaderNodeVectorMath", "归一化异常 XY")
        normalize.operation = "NORMALIZE"
        graph.put(normalize.inputs[0], vector.outputs[0])
        encoded = graph.node("ShaderNodeVectorMath", "重新编码 0..1")
        encoded.operation = "MULTIPLY_ADD"
        graph.put(encoded.inputs[0], normalize.outputs[0])
        graph.put(encoded.inputs[1], (0.5, 0.5, 0.5))
        graph.put(encoded.inputs[2], (0.5, 0.5, 0.5))
        normal = graph.node("ShaderNodeNormalMap", "切线 → 世界空间法线")
        normal.space = "TANGENT"
        graph.put(normal.inputs["Color"], encoded.outputs[0])
        graph.put(normal.inputs["Strength"], inputs["Normal Strength"])
        specular_r, specular_g, specular_b = graph.separate(inputs["Specular Color"], "分离 _s RGB")[:3]
        smoothness = graph.clamp(specular_r)
        inverse = graph.math("SUBTRACT", 1, smoothness)
        roughness = graph.math("MULTIPLY", inverse, inverse, "线性粗糙度 α")
        green = graph.clamp(specular_g)
        code = graph.math("FLOOR", graph.math("ADD", graph.math("MULTIPLY", green, 255), 0.5), label="金属编码（最近整数）")
        metallic = graph.math("GREATER_THAN", code, 229, "Metallic mask")
        metal_color = inputs["Albedo Color"]
        for bit, name, n_value, k_value in METALS:
            equals = graph.math("COMPARE", code, bit, f"{bit}: {name}")
            equals.node.inputs[2].default_value = 0.1
            tinted = graph.color("MULTIPLY", inputs["Albedo Color"], metal_f0(n_value, k_value), label=f"{name} F0 × Albedo")
            metal_color = graph.color("MIX", metal_color, tinted, equals, f"选择 {name}")
        f0_value = graph.color("MIX", green, metal_color, metallic, "介电质 / 金属 F0")
        blue = graph.math("MULTIPLY", graph.clamp(specular_b), 255, "B 字节范围")
        porous_mask = graph.math("LESS_THAN", blue, 64.5)
        porosity = graph.math("MULTIPLY", graph.clamp(graph.math("DIVIDE", blue, 64)), porous_mask)
        sss = graph.clamp(graph.math("DIVIDE", graph.math("SUBTRACT", blue, 65), 190, "SSS 解码"))
        specular_alpha = graph.clamp(inputs["Specular Alpha"])
        emission = graph.math("MULTIPLY", graph.clamp(graph.math("MULTIPLY", specular_alpha, 255 / 254)),
                              graph.math("LESS_THAN", specular_alpha, 1), "Alpha 255 不发光")
        values = (normal.outputs[0], graph.clamp(normal_b), graph.clamp(inputs["Normal Alpha"]), roughness,
                  f0_value, metallic, porosity, sss, emission, graph.clamp(inputs["Albedo Alpha"]))
        for (name, _, _), value in zip(DATA_OUTPUTS, values):
            graph.put(output[name], value)
        graph.layout()
        tree["labpbr_complete"] = True
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def build_surface():
    tree = _cached("Surface", SURFACE_REVISION)
    if tree:
        return tree
    decoder = build_decoder()
    tree = _new_tree("Surface", SURFACE_REVISION)
    try:
        _interface(tree, TEXTURE_INPUTS + CONTROLS,
                   (("Shader", "NodeSocketShader", "连接 Material Output 的 Surface"),) + DATA_OUTPUTS)
        graph = Graph(tree)
        inputs = graph.node("NodeGroupInput", "贴图与预览参数").outputs
        output = graph.node("NodeGroupOutput", "着色与原始解码输出").inputs
        decode = graph.node("ShaderNodeGroup", "labPBR 1.3 解码（双击编辑）")
        decode.node_tree = decoder
        for socket in decode.inputs:
            graph.put(socket, inputs[socket.name])
        data = decode.outputs
        shader = graph.node("ShaderNodeBsdfPrincipled", "Blender 表面近似")
        perceptual = graph.math("SQRT", data["Linear Roughness"], label="Principled 感知粗糙度")
        wetness = graph.clamp(inputs["Wetness"])
        absorption = graph.math("MULTIPLY", wetness, data["Porosity"])
        darkening = graph.math("SUBTRACT", 1, graph.math("MULTIPLY", absorption, 0.4))
        wet_roughness = graph.math("MULTIPLY", perceptual,
                                   graph.math("SUBTRACT", 1, graph.math("MULTIPLY", wetness, 0.6)))
        ao_value = graph.math("SUBTRACT", 1, graph.math("MULTIPLY",
                              graph.math("SUBTRACT", 1, data["AO"]), graph.clamp(inputs["AO Strength"])))
        base = graph.color("MULTIPLY", inputs["Albedo Color"], graph.math("MULTIPLY", darkening, ao_value))
        base = graph.color("MIX", base, data["F0"], data["Metallic"], "金属使用 RGB F0")
        dielectric = graph.separate(data["F0"], "介电质 F0 标量")[0]
        root = graph.math("SQRT", graph.math("MINIMUM", graph.math("MAXIMUM", dielectric, 0), 229 / 255))
        ior = graph.math("DIVIDE", graph.math("ADD", 1, root), graph.math("SUBTRACT", 1, root), "F0 → IOR")
        bump = graph.node("ShaderNodeBump", "可选高度凹凸（默认关闭）")
        graph.put(bump.inputs["Height"], data["Height"])
        graph.put(bump.inputs["Normal"], data["Normal"])
        graph.put(bump.inputs["Distance"], inputs["Bump Distance"])
        for name, value in (
            ("Base Color", base), ("Metallic", data["Metallic"]),
            ("Roughness", wet_roughness), ("IOR", ior), ("Alpha", data["Alpha"]),
            ("Normal", bump.outputs[0]), ("Subsurface Weight", data["SSS"]),
            ("Subsurface Scale", inputs["SSS Scale"]), ("Emission Color", inputs["Albedo Color"]),
            ("Emission Strength", graph.math("MULTIPLY", data["Emission Mask"], inputs["Emission Strength"])),
        ):
            graph.put(shader.inputs[name], value)
        shader.inputs["Specular IOR Level"].default_value = 0.5
        graph.put(output["Shader"], shader.outputs[0])
        for name, _, _ in DATA_OUTPUTS:
            graph.put(output[name], data[name])
        graph.layout()
        tree["labpbr_complete"] = True
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


__all__ = ["build_decoder", "build_surface"]
