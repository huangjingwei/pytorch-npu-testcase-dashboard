# 追踪真正失败的组件（测试表面现象 ≠ 真正失败的东西）

> 用于步骤 2。候选用例里，报错提到的 dtype/算子，经常不是真正的根因所在——torch-api 列/测试名只是"名义上在测什么"，不是"真正在算什么"。查文档前必须先确认这一点，否则查错对象，结论站不住。

## 核心原理：报错信息是反着推出"真实组件"的

用户/测试写的是 `torch.xxx(...)`，但报错信息里出现的从来不是这个名字——CANN/NPU 运行时只认识自己这一层的实现细节（aclnn 调用名、op-plugin 的 kernel 函数名、CANN optype 名）。完整链路：

```
torch.xxx(...)                         ← 测试调用的表面 API（可能不是真正失败的东西）
    ↓
aten::xxx                              ← PyTorch 内部抽象算子（backend 无关）
    ↓ dispatcher 按 tensor 所在 device 分发
op-plugin/op_plugin/ops/opapi/XxxKernelNpuOpApi.cpp   ← NPU 侧具体实现
    ↓
aclnnXxx(...)                          ← CANN 底层库函数（报错信息里能看到的东西）
```

## 判断信号：出现任一个就要往下深挖

- `torch-api`/测试名暗示的功能，和报错栈里出现的函数名/CANN 算子名对不上。
- 测试名明显是在测"样本构造"/"类型转换"类辅助逻辑（如包含 `make_tensor`、`.to(`、随机数初始化），而不是在测核心计算逻辑本身。
- 同一个 CANN 算子报错，却出现在一大批**语义完全不相关**的上层 API 测试里（说明这些 API 没有专属 NPU kernel，走的是某个通用组合/分解实现）。
- 报错文本里的函数名/异常类型名和测试名字面意思对不上（如异常类型里出现"prod"，但测的是 cumprod）。

## 反查真正组件的权威静态材料（按优先级）

不需要执行任何代码，全部是查文件：

1. **`op-plugin/op_plugin/config/op_plugin_functions.yaml`**：`gen_opapi.exec` 字段给出 aclnn 调用名与上层函数的权威映射，没有手写实现时优先查这个，不要自己猜测命名转换规则。
2. **`op-plugin/op_plugin/ops/opapi/*.cpp`**：yaml 查不到时（纯手写、非 codegen 的算子），在对应 `XxxKernelNpuOpApi.cpp` 里找 `EXEC_NPU_CMD(aclnnXxx, ...)` 调用，或者反过来 grep kernel 文件名反推算子。
3. 实在没有对照表才退化成 camelCase→snake_case 启发式——**已知会出错**（如 `IsPosInf` 猜成 `is_pos_inf`，真实是 `isposinf`），仅作最后兜底，且结果要人工抽查，不能直接采信。

## 源码检查 ≠ 已经找到真正组件的终点

查到一行 torch_npu 源码里的 `TORCH_CHECK`/硬编码判断，**不代表已经找到了权威依据**，只是找到了"调用链上的一环"。必须确认这一环是不是真正执行计算的地方：

- 报错带 `AclNN_Parameter_Error(EZxxx)` + `NPU function error: call aclnnXxx failed, error code is 161002` 这种 ACL 原生格式 → 已经到底了，是 CANN 自己拒绝的，这一环就是终点。
- 报错是纯 `TORCH_CHECK` 风格的 C++ 异常文本，**没有**上述 ACL 错误码格式 → 这一环只是 torch_npu 自己的前置检查，**不是终点**，必须继续往下查这个检查保护的、真正会被调用的 CANN 算子（按上面"反查真正组件"小节的方法），再查**那个算子自己**的文档。torch_npu 这道检查完全可能是错的/过严的。

反面教材：`svd_npu` 系列源码里 `TORCH_CHECK(A.dtype()==at::kFloat, "svd_npu only supported Float...")` 看起来像权威依据，但顺着 `op_plugin_functions.yaml` 查到真正调用的 `aclnnSvd` 后，文档显示其实支持 FLOAT/DOUBLE/COMPLEX64/COMPLEX128——这道检查本身是错的，不能当文档用。详见 `dtype-doc-verification.md`"源码里的硬编码限制≠文档依据"一节。

## 存在性校验：避免把内部实现细节误当成公开 API

批量产出"对应算子"这一列时，如果中间步骤曾经反查出一个"torch.xxx"形式的名字，过一遍内部命名黑名单，避免把 op-plugin 内部函数名拼了 `torch.` 前缀后误当成真实公开 API：

```
split_and_exec_npu_cmd, exec_npu_cmd, npu_cmd
_out_npu_no_check, _out_npu_nocheck, _nocheck_opapi, _implement_npu
Kernel, KernelNpu, NpuOpApi, StructKernel, OpApi（后缀）
DynamicGRUV2, DynamicRNNV2（CANN optype，不是 torch 层名字）
```

有 NPU 服务器可用时，比维护黑名单更可靠的做法是直接做存在性校验：

```python
import torch
def exists(dotted):
    obj = torch
    for part in dotted.split('.')[1:]:
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True
```

黑名单只能防住"已经见过的"内部命名模式，`hasattr` 能防住所有不存在的名字，包括从没见过的新模式。有服务器时这一步不是可选项。

## 四个实测案例（按追踪难度递增）

| 表面现象 | 真正失败的组件 | 怎么发现的 |
|---|---|---|
| 34 个 `torch.special.*` 特殊数学函数（贝塞尔/切比雪夫/勒让德多项式等）报 UInt16/32/64 不支持 | 测试**样本构造阶段**调用 `make_tensor`→`torch.randint`→NPU 底层 `aclnnInplaceRandom`，随机数生成算子不支持这三种整数类型，跟被测函数本身无关 | 报错发生在用例执行最早期（样本还没构造完），跟被测函数签名完全不符；live 复现单独跑 `make_tensor(dtype=torch.uint16, ...)` 确认报错逐字符一致 |
| `fft_fftshift` 报 "Autograd not support dtype: Long"，看起来跟 complex64 毫无关系 | 真正执行计算的是 `roll`（torch_npu 内部把 ComplexFloat 位重解释成 Long 再调用 `roll`），`roll` 的底层 `aclnnRoll` 不支持 complex64 | 顺着调用栈往下查，发现 fftshift 没有专属 CANN 算子，是用 roll 组合实现的；"Long"这个诡异的 dtype 名就是位重解释的痕迹 |
| `torch.index_select` 某些 FLOAT8 测试报错 | 测试样本构造阶段调用了 `.to(dtype)`，真正失败的是 `torch.Tensor.to` 对 FLOAT8 的转换，跟 index_select 本身无关 | 报错原文自带"实际是 torch.Tensor.to 的问题"这类说明文字；核对发现同一个文档 URL 已经在另一条 `torch.Tensor.to` 的记录里出现过——**同一份文档 URL 被多条记录引用时，其中措辞干净、能直接提取出名字的那些记录，可以反推出措辞含糊那些记录的真实身份**，这是批量处理时最实用的兜底手段，`scripts/generate_output_table.py` 已经把这个逻辑实现成脚本的二级解析策略 |
| 测 `_refs.cumprod` 的用例报错栈显示调用的是 `ProdKernelNpuOpApi.cpp`/`aclnnProdDim` | `_refs.cumprod` 在 NPU 上没有专属 kernel，实际走的是跟 `torch.prod` 共用的底层代码路径 | 报错 first_line 里的异常类型名（"prod:..."）和报错 op 字段（`aclnnProdDim`）直接暴露了真实调用对象，跟测试名字面意思（cumprod）不符 |

## 批量处理时的常见误抓模式

| 现象 | 原因 | 处理 |
|---|---|---|
| 同一个"真正组件"值连续出现在大量明显不相关的用例里 | 报错文本里的某个通用词/测试框架自身调用被误抓（`torch.allclose`、`torch.randint`、`torch.autograd.gradcheck` 是最高发的误抓来源） | 抓到"连续重复+跨类别"这个模式，直接怀疑，人工抽查 |
| 反查出的名字格式合法但读起来像 C++/内部命名风格 | 内部函数名被自动拼了 `torch.` 前缀 | 过黑名单，有服务器时做 `hasattr` 校验 |
| 同一个底层 kernel/aclnn 名对应好几个不同 nodeid 的测试意图 | 多个公开 API 共享同一段底层实现（`index_reduce` 和 `index_fill_` 共用 `aclnnInplaceIndexFill`，LSTM 内部调用 `cat`，`CrossEntropyLoss` 内部调用 `nll_loss`） | 以 nodeid 体现的测试意图为准；有服务器时以 traceback frame 顺序为准（报错点往上第一个属于 `torch/`或`torch_npu/` 目录、且是公开命名空间的 frame） |
