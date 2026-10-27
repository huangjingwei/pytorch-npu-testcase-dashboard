# 已知不支持模式速查（dtype + 内存格式，聚焦本 skill 所需的 2 类）

> 这是从 `torch-npu-test-failure-diagnose` 技能的 `knowledge/common/unsupported-patterns.md`（完整 6 类已知不支持模式）里摘出的聚焦版，只保留本 skill 用得到的类别 1（dtype）和类别 4（内存格式）。另外 4 类（硬件维度限制/CUDA 独有/特定未实现算子白名单/测试自身设备限制）跟 dtype/memory_format 无关，不在本 skill 范围内，不收录。

## 使用方式

将错误日志（**完整原始日志全文**，不要只取截断后的 header/第一行，见下方截断陷阱）与以下 2 个类别逐一匹配。命中任意一条即列为候选，进入 `trace-to-real-component.md`（追踪真正失败组件）和 `dtype-doc-verification.md`（文档核验）流程——命中正则只是初步信号，不能直接下最终结论。

---

## 类别 1：CANN dtype 不支持

**泛化规则**（覆盖实测遇到的全部报错措辞，完整格式目录和每种格式的示例见 `dtype-doc-verification.md`"报错文案格式速查"一节）：

```
not implemented for DT_\w+
dtype=torch\.\w+.*not (currently )?supported
dtype=torch\.\w+.*not implemented
(aclnn\w+ failed, error code 161002).*(not implemented for) DT_\w+
(does not include|should be in).*dtype support list
only supports dtype=.*Got dtype=
does not support the complex number type
SupportInfo\[\d+\]:.*\(DT_\w+
Io input dtype or format is not supported
Dynamic shape check.*data type DT_\w+.*not supported
```

**说明**：CANN/aclnn API 存在但缺少特定 dtype 实现。可能是 CANN 版本未升级，或该 dtype 在硬件上本不支持。

**排除条件**（命中以下措辞时，先怀疑不是"该 dtype 整体被排除"的 blanket 不支持，转入人工复查，不要直接当作类别 1 定论）：

```
expected dtype is DT_\w+ but found DT_\w+          # 参数一致性测试，两侧 dtype 可能都合法
is not support \d+ when input type is BF\d+        # 措辞像 dtype 限制，实际常是 0 维/越界轴问题
```

> ⚠️ **这条排除条件只是"不要当场归为 blanket 不支持"，不等于"该结论永远不能拉黑"**。如果报错带 `AclNN_Parameter_Error`+`error code is 161002`（CANN 原生格式），且该算子文档的"返回值/报错场景"表格把这个参数关系明确列为错误触发条件（如"src 须与 self 一致"、"推导后的结果类型须能安全转换为 out 类型"），这类关系性约束同样满足拉黑标准，不比独立枚举列表弱。详见 `dtype-doc-verification.md`"报错发生在哪一层"一节。

> ⚠️ **提取报错文本时必须搜索完整原始日志全文，不能依赖"找到某个标志字符串就截断"的简化逻辑**。`Exception: Original OpInfo is broken` 和 `The Inner error is reported as above...current working operator name is X` 这两种异常包装会把真正的 CANN 报错行放在常见截断点（如 `\nTo execute this test`）**之后**。一次实测里，986 条用例因为这类截断被误判"无 dtype 信号"，全文重新搜索后 391 条其实有完整支持列表。

**判定**：候选命中——但这只是"报错文案像 dtype 不支持"的初步判定，**命中正则不代表可以直接下最终结论**，必须走文档核验（`dtype-doc-verification.md`）。

> 🔴 **结论用于"永久屏蔽/向 CANN 提需求"等长期判定时，必须先核验版本匹配的官方文档，确认该 dtype 确实不在文档支持列表里**。实测中至少 4 个算子（`aclnnNeTensor`/UINT64、`aclnnIm2col`/BOOL、`aclnnMaxDim`/`aclnnMinDim`/INT32）运行时报错和这里的正则完全匹配，但官方文档明确把该 dtype 列为支持——文档与运行时矛盾时，不能下"CANN 不支持"结论，要转为"文档矛盾，需 live 复现或提 CANN 问题单"，不进入屏蔽表。

---

## 类别 4：内存格式/布局限制

**泛化规则**：

```
(only|must be|requires?)\s+(contiguous|preserve_format)\s+(memory format|format|tensor)
(contiguous|preserve_format).*(supported|required)
(format|layout).*not (currently )?supported
only.*strided.*supported
channels_last.*not.*supported
```

**说明**：NPU 对非 contiguous 或特定 memory format（如 channels_last）的支持有限。核验方式和 dtype 不同，走的是 torch_npu 自己的官方 API 文档，不是 CANN aclnn 文档，见 `SKILL.md` 步骤 4。

**排除条件**：如果同时出现 `contiguous()` 或 `.contiguous()` 调用建议，则该错误可能是**可修复的**（补 contiguous 守卫），不自动归类为不支持，转人工复查。

**判定**：
- 无排除条件：候选命中，进入文档核验流程。
- 有排除条件：转人工复查（可能是行为不一致，不是不支持）。

---

## 示例匹配

| 日志片段 | 匹配类别 | 判定 |
|---------|---------|------|
| `aclnnAbs failed, error code 161002 ... not implemented for DT_COMPLEX` | 类别 1 | dtype 不支持（需文档核验后才能定论） |
| `The operator does not support the complex number type.` | 类别 1 | dtype 不支持（blanket，无枚举列表） |
| `scatter_reduce only supports dtype=float32, reduce=sum, include_self=true. Got dtype=X,...` | 类别 1 | dtype 不支持（组合约束，非 blanket） |
| `Execution_Error(EZ1009):...SupportInfo[0]: x(DT_FLOAT16, ND)...` | 类别 1 | dtype 不支持（SupportInfo 枚举格式） |
| `Tensor self expected dtype is DT_FLOAT but found DT_DOUBLE.` | 类别 1 排除条件 | 先怀疑是参数一致性测试；但若长期判定用途，仍要查文档"报错场景"表格是否明确列出这个参数关系，列出了同样可以拉黑，不要一律当作"不支持" |
| `RuntimeError: xxx_npu only supported Float, but get ...`（纯 TORCH_CHECK 文本，无 `AclNN_Parameter_Error`/`error code is 161002`） | 类别 1（需二次核验） | 来自 torch_npu 源码的硬编码限制，不是 CANN 原生报错；必须先顺调用链查真正的 CANN 算子文档再下结论，不能直接采信这行文案 |
| `RuntimeError: Only contiguous_format or preserve_format is supported` | 类别 4 | 格式限制 |
| `DispatchStub: missing kernel for npu` | 无匹配 | 不是 dtype/格式问题，不在本 skill 范围 |
