# dtype 不支持结论的文档核验方法

> 用于 `references/candidate-screening.md`/`references/unsupported-patterns-excerpt.md` 类别 1（CANN dtype 不支持）。命中该类别的正则只代表"运行时报错像是 dtype 不支持"，**不能直接作为"该 dtype 永久不支持、可拉黑/可向 CANN 提需求"的结论**。必须核验版本匹配的官方文档后才能定论。三次大规模核验（2026-10-08/09/10，对 2000+ 条用例逐条核对，另对 1900+ 条新增条目做 A2/A3 硬件代际核验）得到的经验全部收录于此。

## 源码里的硬编码限制 ≠ 文档依据

torch_npu 源码（`op_plugin` 里的 `TORCH_CHECK`、硬编码的 dtype 判断等）**永远不能被当作"已经查过文档"的证据**，哪怕它看起来就是在描述一条 dtype 限制、哪怕报错文案直接引用了这行 TORCH_CHECK 的文本。必须顺着调用链找到**真正执行计算的那个 CANN 算子**，查**它自己**的官方文档，才能判断是否该拉黑。

**典型反例（svd_npu 家族，2026-10-09）**：`op_plugin/ops/opapi/LinalgSvdKernelNpuOpApi.cpp` 里有 `TORCH_CHECK(A.dtype() == at::kFloat, "svd_npu only supported Float, but get...")`，看起来就是在声明"只支持 Float"。如果把这行 TORCH_CHECK 当成"文档依据"直接拉黑，就错了——顺着调用链查 `op_plugin/config/op_plugin_functions.yaml` 里这个函数条目的 `gen_opapi: exec: aclnnSvd, ...` 配置，确认它实际调用的是 `aclnnSvd`，再查 `aclnnSvd` 自己的官方文档，发现真实支持列表是 `FLOAT/DOUBLE/COMPLEX64/COMPLEX128`——跟源码里的 TORCH_CHECK 直接矛盾。这说明 torch_npu 自己加的这道限制本身就是过严/错误的，不是 CANN 的限制，**不能拉黑**，应该当作 torch_npu 的真实 bug（限制应该放宽）记录下来。这一个误判曾经导致 628 条用例被错误拉黑，后来整批撤回。

**如何找到真正执行计算的 CANN 算子**：
- 手写 C++ 实现：在对应的 `op_plugin/ops/opapi/XxxKernelNpuOpApi.cpp` 里找 `EXEC_NPU_CMD(aclnnXxx, ...)` 调用。
- 找不到手写实现时查自动生成配置：`op_plugin/config/op_plugin_functions.yaml` 里该函数条目的 `gen_opapi: exec: aclnnXxx, ...` 字段，这是 codegen 自动生成调用代码的声明，指明了真正调用的 aclnn 算子名。
- 多个上层 API 共享同一个底层算子是常见情况（例如 `torch.linalg.det/inv/pinv/qr/eig/...` 等几十个 API，因为没有各自专属的 NPU kernel，全部走 PyTorch 通用的 SVD 分解组合实现，最终都落到同一个 `aclnnSvd`）——报错信息里的函数名和查到的 CANN 算子名对不上，不代表两者无关，先确认调用链。

**核心原则**：torch_npu 源码的限制比 CANN 文档更严格时，只能说明 torch_npu 自身可能有待修复的问题，不等于"CANN 不支持"——这两者是完全不同性质的问题，不能混为一谈，也不要在分析里引入"这是 CANN 的锅还是 torch_npu 的锅"这类与"是否该拉黑"无关的框架。

## 报错发生在哪一层：决定了该怎么核验

判断之前先看报错文本的来源层级，这决定了后续走哪条核验路径：

**CANN/ACL 当场拒绝**（报错带 `AclNN_Parameter_Error(EZxxx)` + `NPU function error: call aclnnXxx failed, error code is 161002` 这种 ACL 原生格式）：说明 torch_npu 已经把调用传给了真实的 CANN 算子，是 **CANN 自己在执行时拒绝的**，不存在"torch_npu 没调用到 CANN"的可能。这种情况下：
- 直接按下面的核验步骤查该算子自己的官方文档即可下结论，不需要纠结"torch_npu 加个前置检查是不是就能绕开这个报错"——就算加了前置检查，底层 CANN 该拒绝还是会拒绝，只是报错时机提前、文案变得更友好，**问题本质不变，不影响"是否该拉黑"的判断**。
- 报错文案是"参数间关系性约束"（如 `Tensor src expected dtype is DT_X but found DT_Y`、`Result type DT_X can't be cast to the desired output type DT_Y`）而不是"独立枚举列表"时，**不能仅因为是关系性措辞就排除出拉黑范围**——去查该算子文档的"返回值/报错场景"表格，只要这个具体的参数组合/类型推导关系被文档列为 `ACLNN_ERR_PARAM_INVALID` 的触发条件之一，就视为文档已明确说明该情况不支持，满足拉黑标准。不要套用"一致性校验 = 不是 blanket 不支持 = 不能拉黑"这种过度简化的排除逻辑（下面"为什么模式匹配不够"第 2 点要配合这一条一起看）。

**torch_npu 调用 CANN 之前的拦截**（报错是 `TORCH_CHECK` 风格的 C++ 异常文本，**没有** ACL 错误码/`error code is 161002` 格式）：说明 torch_npu 自己的代码在**还没来得及调用 CANN 算子之前**就报错了。这种情况**必须**顺着调用链找到真正会被调用的 CANN 算子，查它自己的文档（见上一节）——torch_npu 这道拦截完全可能是错的/过严的（svd_npu 就是实例），不能只看 torch_npu 层的报错文案或 torch_npu 自己代码里的字符串/注释就下结论。

**实操判断**：报错文本里有 `AclNN_Parameter_Error` 或 `error code is 161002` 字样 → CANN 原生拒绝，直接查文档判断，不用管 torch_npu 层理论上能不能修；没有这些字样、只是普通的 C++/Python 异常文本（没有 ACL 错误码）→ 先定位真正会被调用的 CANN 算子，查它的文档，不能直接采信 torch_npu 这层的限制文案。

## 为什么模式匹配不够：两类会让你下错结论的情况

1. **文档与运行时矛盾**：运行时报错文案和"真的查不到支持"完全是两件事。实测发现至少 4 个算子（`aclnnNeTensor`/UINT64、`aclnnIm2col`/BOOL、`aclnnMaxDim`/`aclnnMinDim`/INT32）运行时明确拒绝某 dtype，但该 dtype **确实写在文档的支持列表里**。如果只看报错文案就下"CANN 不支持"的结论，这几个算子会被错误拉黑。
2. **报错文案提到 dtype ≠ 该 dtype 真的不被支持**：至少 3 种常见措辞是"参数一致性/类型提升校验"，不是"blanket 不支持"：
   - `Tensor self expected dtype is DT_X but found DT_Y.`（self 与 other/target 要求类型一致，测试故意传入不一致组合验证报错；X 和 Y 可能都在支持列表里）
   - `Expected self dtype [DT_X] and target dtype [DT_X] to be promotable but check failed.`（两边类型相同却仍然 check failed，说明是该 dtype 整体被排除，不是"不一致"问题——这种反而要继续按类别 1 处理，不要被"两边类型相同"误导成"肯定是参数不一致"）
   - `The {sort|dim} axis value is not support N when input type is BFN.`（措辞像 dtype 限制，实际根因是 0 维张量 + 越界轴，文档里 BF16 是被支持的；`aclnnKthvalue`/`aclnnMedianDim`/`aclnnNanMedianDim` 都是这种误导性文案）

   判断口诀：**消息里出现"expected...found"或"promotable"且两侧都是可能合法的 dtype → 先怀疑是一致性/提升校验，不是"该 dtype 整体被排除"的 blanket 不支持**；"not support when input type is BFxx"这种绑定在某个具体 axis/dim 校验失败场景里的 dtype 措辞 → 先怀疑是 shape/axis 问题。

   > ⚠️ **"是一致性/提升校验" ≠ "不能拉黑"**。2026-10-10 的教训：不要看到"expected X found Y"这类关系性措辞就直接排除出拉黑范围。正确流程是先看报错是不是 CANN/ACL 原生格式（`AclNN_Parameter_Error` + `error code is 161002`，见上面"报错发生在哪一层"一节）——如果是，再去查该算子文档的"返回值/报错场景"表格，只要这个具体的参数关系（如"src 须与 self 一致"、"推导后的结果类型须能安全转换为 out 类型"）被文档列为错误触发条件之一，就同样满足拉黑标准，不比独立枚举列表的情况弱。只有当这道检查是 torch_npu 在调用 CANN **之前**自己加的（没有 ACL 错误码），才需要进一步追踪真正的 CANN 算子文档来判断 torch_npu 这道检查本身是否过严。

## 核验步骤

1. **定位真正执行计算的那个底层算子**，不要停在最外层报错。torch_npu 有时会用"位重解释"或"组合已有算子"实现一个 API（例如 `fft_fftshift` 内部把 ComplexFloat 输入位重解释成 Long 再调 `roll`），这时报错文案（"Autograd not support dtype: Long"）会和真实原因（`roll` 的底层算子 `aclnnRoll` 不支持 complex64）看起来完全不相关。往下查到真正的 `aclnnXxx` 调用名，再查它的文档。本步骤的完整方法论另见 `trace-to-real-component.md`。
2. **确定部署环境的实际 CANN 版本**（从环境记录文件找，别猜），找到对应的 `ops-math`/`ops-nn`/`ops-cv`/`ops-transformer` 等仓库里**匹配该版本号的 git 分支**（如 `9.1.0-beta.3`），不要用 `master`——master 是未发布的未来版本，可能比部署版本多/少支持某些 dtype，用它核对会得出错误的"矛盾"或"确认"结论。
3. **用 `git ls-tree -r origin/<版本分支> --name-only` 搜文档路径，不要用本地工作区的 `find`/`ls`**。同一个算子的文档在不同版本间会换目录（例如 `aclnnPolar` 在 9.1.0-beta.3 是 `math/complex/docs/`，master 是 `math/polar/`；`aclnnCoalesceSparse` 的整个 docs 子目录在 master 已被删除，只有历史分支还有）。如果检出的工作区恰好没有某个文件，不代表该版本真的没有这个文档——先用 `git ls-tree` 确认。
4. **用 `git show origin/<分支>:<路径>` 直接读取该版本的文档内容**，路径用正斜杠（git tree 路径始终是 POSIX 风格，即使在 Windows 上）。在参数表里找到报错里实际命中的那个参数名（注意同一算子不同入参可能有不同的 dtype 列表，比如 `aclnnMaxUnpool2d` 的 `indices` 只支持 INT32/INT64，但 `self` 的限制是另一张单独的表，两者是独立约束，不能混用）。

   > ⚠️ **原地（Inplace）和非原地版本的参数表是分开的，不能混用**。同一份文档文件常同时包含 `aclnnXxx`（非原地）和 `aclnnInplaceXxx`（原地）两个算子的独立小节，dtype 列表可能完全不同——非原地版本的输入参数（如 `self`）通常能接受更宽的类型（算完结果写入新建的 `out`，`out` 的列表更窄即可），但原地版本的 `selfRef`（输入同时也是输出，复用同一块内存，不能隐式转换类型）往往只支持 `out` 那张更窄的列表。实例：`aclnnErfinv`（非原地）的 `self` 支持 `FLOAT、FLOAT16、BFLOAT16、BOOL、INT8、INT16、INT32、INT64、UINT8`，但 `aclnnInplaceErfinv` 的 `selfRef` 只支持 `FLOAT、FLOAT16、BFLOAT16`——如果查文档时误读成非原地版本那张更宽的表，会放过一个真实该拉黑的 case。核对前先确认测试调的是 `aclnnXxx` 还是 `aclnnInplaceXxx`（测试名的 `inplace_`/`_inplace_` 前后缀、或报错栈里的具体函数名能区分），再去文档里找 `## aclnnInplaceXxxGetWorkspaceSize` 这个专属小节下的参数表，不要直接用文档靠前出现的那张。
5. **核对报错里"不支持"的那个 dtype 是否真的不在文档列表里**：
   - 不在 → 可以下"CANN 文档确认不支持"结论。
   - 在 → 这是矛盾案例（见上），不能下"文档确认不支持"结论，需要走 live 复现或提 CANN 问题单的路径，不要套用类别 1 的判定。
6. **多算子共享同一张支持列表时，参数名不必字面匹配也能复用结论**：例如 `aclnnMatmul`/`aclnnInplaceAddmm` 报错里的参数名是 `matA`，但官方文档参数表里写的是 `self`/`mat1`/`mat2`——因为该算子的全部张量输入参数共享完全相同的 dtype 列表，不管 `matA` 具体指代哪一个，结论都成立。如果不同参数的列表不同（常见于 index 类算子，如 `indices` 和 `self`），则不能这样类推，要单独查命中参数自己的那一行。
7. **文档只存在于 master、所有已发布版本分支都没有时**，默认不能作为版本匹配证据使用，按步骤 5"矛盾/查无文档"路径处理，标记为悬而未决。只有同时满足以下两个条件才能接受为例外证据：master 文档内容与运行时报错逐字吻合，且该限制明确绑定硬件产品线（如"Atlas A2/A3 系列产品"）而非软件版本号——这种限制通常不会随 CANN 版本升级改变。即便满足条件，引用文本里也必须显式写出"此为版本缺口的例外，非标准版本匹配"，不能伪装成普通的版本匹配文档。这类例外必须让用户知情并明确同意，不能自行决定。

## 报错文案格式速查（持续累积）

同一个"dtype 不支持"的根因，CANN/torch_npu 在不同算子、不同调用路径下会产出完全不同措辞的报错。下表是实测遇到过的全部格式，新发现的格式应追加到这里，而不是另开文件。

| 格式 | 示例 | 备注 |
|------|------|------|
| 标准 blanket 列表 | `Tensor self not implemented for DT_INT32, should be in dtype support list [DT_FLOAT,DT_FLOAT16,...].` | 最常见，可直接提取"支持列表" |
| 括号/下标参数名 | `Tensor (*self)[0] not implemented for DT_INT16, should be in dtype support list [...]` | 参数名前有 `(*x)[N]` 这类前缀，正则里 `\w+` 不会匹配，要单独处理或放宽到"任意内容 + not implemented for" |
| 组合约束（非 blanket） | `scatter_reduce only supports dtype=float32, reduce=sum, include_self=true. Got dtype=X, reduce=Y, include_self=Z.` | 不是"这个 dtype 永远不支持"，是"只有这一种参数组合支持"；此时没有单独的"dtype 列表"可读，整句就是约束本身 |
| blanket 无列表 | `The operator does not support the complex number type.` | 没有枚举列表，直接声明整类 dtype 不支持 |
| Execution_Error 枚举 | `Execution_Error(EZ1009): ...details of parameter are x(DT_COMPLEX128, ND)...` 后跟多行 `SupportInfo[N]: x(DT_FLOAT16, ND), out(DT_FLOAT16, ND)` | 支持列表拆成多行 `SupportInfo[N]`，每行一个完整支持组合，不是逗号分隔的单行列表 |
| TBE/AIcoreEngine 算子查找失败里嵌的 dtype 限制 | `Optype [DynamicLSTMGradCell] ...[Dynamic shape check]: data type DT_DOUBLE of input [init_c] is not supported. All supported data type and format of tensor input0.init_c is: Data Type: {DT_FLOAT16,DT_FLOAT}` | 出现在 TBE 自定义算子（`DynamicRNNV2Grad`/`DynamicGRUV2` 等）的 kernel 查找失败信息里，这类算子通常没有公开 aclnn 文档（不在 ops-math/ops-nn/ops-cv/ops-transformer 体系内），查不到文档时诚实标记"查无文档"，不要强行套用其他算子的结论 |
| Io 多参数联合校验（常被截断） | `Io input dtype or format is not supported, get io input info is x1(DT_BFLOAT16, ND) x2(DT_BFLOAT16, ND) scalars(DT_INT64, ND) out(DT_BFLOAT16, ND) but supported list is:` | 冒号后的列表经常在日志截取时被切掉，**不要因为列表缺失就放弃**，改查该算子文档里对应参数自己的 dtype 行，通常能独立确认 |
| 一致性/提升校验（关系性约束，不是独立列表，但同样可能有文档依据，见"报错发生在哪一层"一节） | `Tensor self expected dtype is DT_FLOAT but found DT_DOUBLE.` / `promoteType dtype DT_X should be in dtype support list [...]` / `Result type DT_X can't be cast to the desired output type DT_Y.` | 先确认是不是 `AclNN_Parameter_Error`+`error code is 161002` 格式（CANN 原生）；是的话查该算子文档"返回值/报错场景"表格，只要这个参数关系被列为错误触发条件就可以拉黑，不要因为"是关系性措辞"就排除 |
| 空 tensor 场景专项约束（非 blanket） | `when input is empty tensor, out cannot be DT_COMPLEX128, should be in dtype support list [...]` | `self`/`out` 的通用列表本身包含该 dtype，只是"输入为空 tensor"这个特定场景下专项排除，文档里通常有对应的单独说明（如 `aclnnProd` 文档明确写"输入为空tensor时，输出类型不能是复数类型"） |
| ACL 原生参数校验（判断"是否 CANN 当场拒绝"的关键标志） | `[PID: xxx] AclNN_Parameter_Error(EZ1001): Tensor src expected dtype is DT_DOUBLE but found DT_FLOAT.` 包裹在 `NPU function error: call aclnnXxx failed, error code is 161002` 里 | 出现这个格式 = CANN/ACL 自己在执行时拒绝的，不是 torch_npu 调用前的拦截，直接查文档判断即可，见"报错发生在哪一层"一节 |
| torch_npu 调用前拦截（没有 ACL 错误码） | `RuntimeError: xxx_npu only supported Float, but get ...`（纯 C++ `TORCH_CHECK` 异常文本，不含 `AclNN_Parameter_Error`/`error code is 161002`） | 必须先顺调用链找到真正的 CANN 算子查它自己的文档，不能直接采信这行文案，见"源码里的硬编码限制 ≠ 文档依据"一节 |

## 日志截断陷阱

提取"报错信息"时如果用了"找到某个标志字符串（如 `\nTo execute this test`）就截断"这种简化逻辑，要小心：至少两种常见的异常包装格式会把真正的 CANN 报错行放在这类标志字符串**之后**：

- `Exception: Original OpInfo is broken`（`test_meta.py` 的 meta/dispatch 交叉验证测试）
- `Exception: The Inner error is reported as above...current working operator name is X`（NPU 异步算子调用失败的包装）

这两种格式下，`\nTo execute this test` 标志字符串出现得很早（紧跟在简短的外层异常摘要之后），真正的 `AclNN_Parameter_Error`/`Execution_Error` 等底层报错反而在 pytest 回溯的 `E   ` 前缀行段落里，位于标志字符串**之后**。一次实测中，986 条用例因为这个截断逻辑被误判为"无 dtype 信号"，重新对完整日志全文搜索后，发现其中 391 条其实带有完整的 dtype 支持列表。

**规则：永远对完整原始日志全文做关键词搜索，不要依赖"找第一个标志行就截断"的简化逻辑。** 如果一次正则没匹配上，不要立即判定"无信号"，换一个更宽的关键词网（`dtype`、`DT_`、`数据类型`、`not support`、`not implement`、`supported list`、`promot`、`Complex`/`COMPLEX`、`Data Type:`）重新扫一遍全文，排除掉 `SampleInput(...)`/`Tensor[size=...]` 这类纯数据回显行的噪音后再看剩下的是不是真的没有信号。
