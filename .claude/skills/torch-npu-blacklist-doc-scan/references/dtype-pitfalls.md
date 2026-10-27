# 常见误判与陷阱（dtype/内存格式批量核验专用，摘自 torch-npu-test-failure-diagnose 的陷阱 7-9）

### 陷阱 1：用"找标志字符串就截断"的简化逻辑提取报错信息，漏掉截断点之后的真实报错

**症状**：批量脚本化处理大量用例日志时，用类似"找到 `\nTo execute this test` 就截断，只分析截断前的内容"这种逻辑提取报错文本，导致判定为"无错误信号"或"无 dtype 信号"。
**误判**：认为该用例报错信息不完整/不包含有效错误特征，归入"需要人工复查"或"非本类问题"。
**为什么会漏**：至少两种异常包装格式会把真正的底层报错行放在常见截断标志**之后**：`Exception: Original OpInfo is broken`（meta/dispatch 交叉验证测试）和 `Exception: The Inner error is reported as above...current working operator name is X`（NPU 异步算子调用失败）。这两种格式下，截断标志紧跟在简短的外层异常摘要后面出现得很早，真正的 `AclNN_Parameter_Error`/`Execution_Error` 报错反而在 pytest 回溯的 `E   ` 前缀段落里，位于截断点之后。一次实测中，986 条用例因为这个截断逻辑被误判为"无 dtype 信号"，对完整日志全文重新搜索后，391 条其实带有完整的 CANN 报错和支持列表。
**正确流程**：批量提取报错特征时，永远对**完整原始日志全文**做关键词/正则搜索，不要依赖"找第一个标志行就截断"的简化逻辑。如果第一轮正则没有匹配上，先换一个更宽的关键词网重新扫全文（见 `unsupported-patterns-excerpt.md` 类别 1 和 `dtype-doc-verification.md`），排除掉 `SampleInput(...)`/`Tensor[size=...]` 这类纯数据回显噪音后再确认是否真的没有信号，不要在第一次正则落空时就下"无信号"结论。

### 陷阱 2：报错文案命中"dtype 不支持"正则就直接下最终结论，不核验官方文档

**症状**：错误日志命中 `unsupported-patterns-excerpt.md` 类别 1 的正则（如 `not implemented for DT_X, should be in dtype support list [...]`），文案本身看起来已经非常详实（甚至自带完整支持列表）。
**误判**：认为报错信息已经足够详实，直接下"CANN 不支持该 dtype"的结论，跳过查官方文档这一步。
**为什么不对**：运行时报错文案和"文档确认真的不支持"是两件独立的事。实测发现至少 4 个算子（`aclnnNeTensor`/UINT64、`aclnnIm2col`/BOOL、`aclnnMaxDim`/`aclnnMinDim`/INT32）的运行时报错和类别 1 正则完全匹配、文案也很详实，但官方文档（版本匹配的分支）明确把该 dtype 列为支持——文档与运行时矛盾。如果只看报错文案详实程度就下结论，这几个算子会被错误地当作"CANN 确认不支持"处理。另外，至少 3 种报错措辞提到 dtype 但根因其实是参数一致性校验或 shape/axis 问题，不是真的不支持（见 `dtype-doc-verification.md`"为什么模式匹配不够"一节）。
**正确流程**：凡是结论会被用于**长期生效的判定**（写入屏蔽名单、向 CANN 团队提需求），必须按 `dtype-doc-verification.md` 的步骤找到部署环境版本匹配的官方文档分支，核对该 dtype 是否真的不在支持列表里，确认一致才能下结论；文档和运行时冲突时改判"文档矛盾"，不进屏蔽表。

### 陷阱 3：把 torch_npu 源码里的 TORCH_CHECK/硬编码限制当成"已核验文档"（禁止）

**症状**：报错栈能追到 torch_npu 源码（`op_plugin` 里的 `TORCH_CHECK(xxx.dtype()==at::kFloat, "xxx only supported Float, but get...")`）里的一行硬编码限制，报错文案直接引用这行代码的字符串，看起来已经把"限制"讲得很清楚了。
**误判**：把这行 TORCH_CHECK 的文案当成"已经查过文档"的证据，直接下"CANN 不支持"的结论并拉黑。
**为什么不对**：源码里的硬编码限制可能比真实的 CANN 能力更严格——torch_npu 开发者当初可能是基于过时信息、或者偷懒直接写死了一个保守范围，而真正执行计算的 CANN 算子其实支持更宽的类型。`svd_npu` 家族就是实例：源码 `TORCH_CHECK(A.dtype()==at::kFloat, "svd_npu only supported Float, but get...")` 看起来权威，但顺着调用链（`op_plugin/config/op_plugin_functions.yaml` 的 `gen_opapi: exec: aclnnSvd` 配置）查到真正执行计算的 `aclnnSvd` 的官方文档后，发现其支持 `FLOAT/DOUBLE/COMPLEX64/COMPLEX128`，跟源码限制直接矛盾——这说明源码这道限制本身是错的，不是 CANN 的锅。这一个误判一度导致 628 条用例被错误拉黑。
**正确流程**：报错来自 torch_npu 源码里的硬编码检查（通常是没有 `AclNN_Parameter_Error`/`error code is 161002` 这种 ACL 原生格式的纯 C++ 异常文本）时，**必须**先顺着调用链找到真正执行计算的 CANN 算子——手写实现找 `EXEC_NPU_CMD(aclnnXxx, ...)`，没有手写实现就查 `op_plugin/config/op_plugin_functions.yaml` 里的 `gen_opapi: exec:` 配置——再查**这个 aclnn 算子自己**的官方文档，不能把 torch_npu 源码的文案当成文档核验的替代品。详见 `dtype-doc-verification.md`"源码里的硬编码限制 ≠ 文档依据"一节。
