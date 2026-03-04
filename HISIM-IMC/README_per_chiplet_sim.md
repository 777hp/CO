# HISIM 仿真使用说明（Per-Chiplet Capacity + Floorplan + 2.5D Scheme2）

本文档总结最近对 HISIM 的关键增强，并给出可直接复现的仿真使用方法。

---

## 1. 本次增强点总结

### 1.1 Per-chiplet 核心容量（`n_core`）
- 支持在 floorplan 里按 `stack_id` 指定每个 chiplet 的核心数（capacity）。
- 2.5D (`H2_5D` 且 `N_tier=1`) 下，mapping 会使用 `n_core_by_stack` 做容量约束，而不再假设所有 chiplet 的 `N_tile` 相同。
- 若未提供 floorplan，保持原有 `N_tile` 全局统一行为（向后兼容）。

### 1.2 Floorplan + RapidChiplet 方案二（2.5D）
- 链路长度由 floorplan 几何和端口位置经 `rapidchiplet.compute_link_lengths()` 计算。
- 线延迟按 floorplan `packaging.link_latency`（constant/function）直接算浮点 ns。
- AIB 仍保持原有 Tx/Rx + `N_tr` 串行口径，只把 `L_wire` 替换为每条链路的 `wire_latency_ns`。

### 1.3 Per-chiplet 功耗报表
- 新增 `Results/PPA_per_chiplet.csv`，输出每个 chiplet 的：
  - `stack_id, n_core, E_dyn(pJ), E_leak(pJ), P_dyn(W), P_leak(W), P_total(W)`

### 1.4 面积口径与可行性检查
- 新增硬件常量口径的单核心面积估算（`area_per_core`），不依赖 AI layer。
- 在 2.5D floorplan 下执行可行性检查：
  - `A_defined = w*h`
  - `A_required = n_core * area_single_core_mm2`
  - 约束：`A_required <= A_defined * packing_efficiency`
- 处理策略：
  - `area_violation_policy="error"`（默认）：直接报错停止
  - `area_violation_policy="clip"`：把 `n_core` 裁剪到可容纳上限，再进行 mapping
- 输出中同时保留两类面积：
  - `chiplet_defined_area_mm2`
  - `core_required_area_mm2`

---

## 2. 环境准备

在 `HISIM-IMC` 目录执行：

```bash
pip install -r requirements.txt
```

---

## 3. floorplan JSON 模板（含 per-chiplet n_core）

新建 `chiplet_floorplan.json`（单位 mm）：

```json
{
  "link_routing": "manhattan",
  "packaging": {
    "link_latency_type": "function",
    "link_latency": "lambda L: 0.01*L + 0.0"
  },
  "chiplets": [
    {
      "stack_id": 0,
      "x": 0.0,
      "y": 0.0,
      "w": 10.0,
      "h": 10.0,
      "rotation": 0,
      "n_core": 50
    },
    {
      "stack_id": 1,
      "x": 40.0,
      "y": 0.0,
      "w": 20.0,
      "h": 20.0,
      "rotation": 0,
      "n_core": 200
    }
  ]
}
```

> `n_core` 为必填（在你启用 2.5D per-chiplet capacity 的场景下）。

---

## 4. Python 调用示例

```python
from hisim_model import HiSimModel

model = HiSimModel(
    chip_architect="H2_5D",
    N_tier=1,
    N_stack=2,
    N_tile=100,  # 兼容字段；2.5D+floorplan时会被每chiplet n_core覆盖用于mapping
    xbar_size=512,
    N_pe=16,
    N_crossbar=1,
    ai_model="vit",
    thermal=False,

    chiplet_floorplan_path="./chiplet_floorplan.json",
    use_rapidchiplet_scheme2=True,
    rapidchiplet_module_path="..",  # 视你的仓库结构调整

    packing_efficiency=0.85,
    area_violation_policy="error"  # 或 "clip"
)

result = model.run_model()
print(result)
```

---

## 5. 输出文件说明

运行后重点看：

1. `Results/PPA.csv` / `Results/PPA_new.csv`
   - 包含整体 PPA 指标。
   - 新增面积口径字段：
     - `chiplet_defined_area_mm2`
     - `core_required_area_mm2`

2. `Results/PPA_per_chiplet.csv`
   - 每个 chiplet 的动态/泄漏/总功耗与能耗明细。

3. `Debug/to_interconnect_analy/layer_inform.csv`
   - 每层映射到哪个 stack/tier，用于排查 per-chiplet capacity 下的分配行为。

---

## 6. 常见问题

### Q1: 只改 floorplan 的 `w/h` 会自动改变 mapping 吗？
不会。mapping 的核心约束来自 `n_core`（或兼容模式下的 `N_tile`）。

### Q2: 为什么会报面积不可行？
因为 `n_core * area_single_core_mm2` 超过了 `w*h*packing_efficiency`。可改：
- 增大 `w/h`；或
- 减小 `n_core`；或
- 调整 `packing_efficiency`；或
- 使用 `area_violation_policy="clip"` 自动裁剪。

### Q3: clip 模式会做什么？
会将超限 chiplet 的 `n_core` 裁剪到最大可容纳值，并使用裁剪后的容量重新执行 mapping 流程。

---

## 7. 最小验证建议

你可以优先跑以下测试脚本：

```bash
python tests/test_area_feasibility_policy.py
python tests/test_per_chiplet_capacity_mapping.py
python tests/test_per_chiplet_power_report.py
python tests/test_floorplan_scheme2.py
```

验证点：
- 面积不可行时 `error/clip` 策略是否按预期；
- `n_core=[50,200]` 时 mapping 是否更早切到大芯粒；
- per-chiplet 泄漏功耗是否随 `n_core` 增大；
- 远距离链路是否有更长 wire_len / 更大 wire_latency。

