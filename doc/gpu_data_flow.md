# GPU Data Flow: From NVML to LCD Overlay

> 从 GPU 传感器数据采集到 LCD 显示端的完整数据链路追踪。

## 1. Bug 根因与修复

**Activity 侧边栏中多 GPU 占用率（Usage）始终显示 0 的原因。**

### 1.1 问题描述

Linux 平台上，`uc_activity_sidebar.py` 的 GPU Usage 行（GPU0 Usage ~ GPU7 Usage）始终显示 `--%` 或 `0.0%`。

### 1.2 根因：Key 名称不匹配

**Linux `_poll_nvidia()`** 将 GPU 占用率写入 key 为 `nvidia:{i}:gpu_util`：

```python
# linux_platform.py:885-886
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
readings[f"{prefix}:gpu_util"] = float(util.gpu)
```

**`_GPU_NVMETRIC_SUFFIX`** 映射表中只包含 `gpu_busy`：

```python
# system.py:295-300 (original)
_GPU_NVMETRIC_SUFFIX = (
    ('temp', 'temp'),
    ('gpu_busy', 'usage'),   # <-- 期望 'gpu_busy'
    ('clock', 'clock'),
    ('power', 'power'),
)
```

**`_populate_gpu_indexed()`** 比较 `'gpu_busy' == 'gpu_util'` → **False**，因此 `gpu_X_usage` 字段从未被填充，保持默认值 `0.0`。

### 1.3 修复方案

1. 在 `_GPU_NVMETRIC_SUFFIX` 中添加 `'gpu_util'` 映射
2. 在 Linux `_poll_nvidia()` 中同时写入 `gpu_busy` key（兼容其他平台）
3. 在 Linux `_discover_nvidia()` 中同时注册 `gpu_busy` sensor
4. 修复 Legacy 单卡映射 `gpu_memory` → `gpu_vram_used`（HardwareMetrics 无 `gpu_memory` 字段）
5. 添加 VRAM 指标到 Activity 菜单和 LCD 覆盖层

---

## 2. 完整数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 1: 传感器采集 (Sensor Polling)                                      │
│   src/trcc/adapters/system/linux_platform.py                            │
│   ─────────────────────────────────                                     │
│   _poll_nvidia() (L874-913):                                            │
│     pynvml.nvmlDeviceGetUtilizationRates(handle)                        │
│       → readings["nvidia:0:gpu_util"] = 42.0                            │
│     pynvml.nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)       │
│       → readings["nvidia:0:temp"] = 50.0                                │
│     pynvml.nvmlDeviceGetClockInfo(handle, NVML_CLOCK_GRAPHICS)          │
│       → readings["nvidia:0:clock"] = 1050.0                             │
│     pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0                     │
│       → readings["nvidia:0:power"] = 200.0                              │
│                                                                          │
│   _build_mapping() (L959-1010):                                         │
│     mapping["gpu_usage"] = "nvidia:0:gpu_util"   ← Legacy 单卡映射      │
│     mapping["gpu_temp"]  = "nvidia:0:temp"       ← Legacy 单卡映射      │
│     mapping["gpu_clock"] = "nvidia:0:clock"      ← Legacy 单卡映射      │
│     mapping["gpu_power"] = "nvidia:0:power"      ← Legacy 单卡映射      │
└────────────────────────┬────────────────────────────────────────────────┘
                         │ readings = {"nvidia:0:gpu_util": 42.0, ...}
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 2: 服务层聚合 (SystemService)                                       │
│   src/trcc/services/system.py                                           │
│   ─────────────────────────────────                                     │
│   all_metrics (property, L201-264):                                     │
│     ┌─────────────────────────────────────────────────────────────┐     │
│     │ Phase 1: Legacy 映射填充 (L229-237)                          │     │
│     │   m.gpu_usage = readings["nvidia:0:gpu_util"] = 42.0        │     │
│     │   → 正常 ✓                                                   │     │
│     └─────────────────────────────────────────────────────────────┘     │
│     ┌─────────────────────────────────────────────────────────────┐     │
│     │ Phase 2: 多卡索引填充 (L239-242, _populate_gpu_indexed)      │     │
│     │   扫描 readings 中的 "nvidia:0:*" key                        │     │
│     │   nv_metric = "gpu_util"                                     │     │
│     │   _GPU_NVMETRIC_SUFFIX 匹配:                                  │     │
│     │     "gpu_busy" == "gpu_util" → False ✗                       │     │
│     │   → gpu_0_usage 保持默认 0.0  ← BUG                          │     │
│     └─────────────────────────────────────────────────────────────┘     │
│     ┌─────────────────────────────────────────────────────────────┐     │
│     │ Phase 3: Fallback (L244-254)                                 │     │
│     │   对未填充的 metric 尝试备用数据源                             │     │
│     └─────────────────────────────────────────────────────────────┘     │
│                                                                          │
│   返回 HardwareMetrics DTO:                                              │
│     .gpu_usage = 42.0        ← Legacy 路径正常                           │
│     .gpu_0_usage = 0.0       ← 索引路径被 Bug 阻断                       │
│     .gpu_0_temp = 50.0     ← 正常 (temp key 匹配)                        │
│     .gpu_0_clock = 1050.0  ← 正常 (clock key 匹配)                       │
│     .gpu_0_power = 200.0   ← 正常 (power key 匹配)                       │
└────────────────────────┬────────────────────────────────────────────────┘
                         │ HardwareMetrics DTO
                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Step 3: 中介分发 (MetricsMediator)                                       │
│   src/trcc/ui/gui/metrics_mediator.py                                   │
│   ─────────────────────────────────                                     │
│   _tick() (L114-139):                                                   │
│     定时器触发，按周期分发                                                │
│     metrics = self._metrics_fn()          ← 调用 SystemService.all_metrics│
│     HardwareMetrics.with_temp_unit()      ← °C → °F 转换                 │
│     sub.callback(metrics)                 ← 通知所有订阅者                │
│                                                                          │
│   订阅者:                                                                 │
│     - UCActivitySidebar.update_from_metrics()                            │
│     - OverlayGridPanel._on_metrics_updated()                             │
└────────────────────────┬────────────────────────────────────────────────┘
                         │ HardwareMetrics + Qt Signal
                         ▼
            ┌────────────┴────────────┐
            ▼                         ▼
┌──────────────────────┐  ┌──────────────────────────┐
│ Step 4a: Activity    │  │ Step 4b: Overlay Grid     │
│ Sidebar UI           │  │ (LCD 覆盖层)              │
│ uc_activity_sidebar  │  │ overlay_grid.py           │
└──────────────────────┘  └──────────────────────────┘

### 4a. Activity 侧边栏 ###                    ### 4b. LCD 覆盖层 ###

SensorItem.update_value()                       to_overlay_config()
(L105-116):                                     (L222-274):
  value = getattr(                                cfg.gpu_index=0:
    metrics,                                       entry["metric"] = "gpu_usage"
      self.metric_key)                            cfg.gpu_index=1:
  → getattr(                                       entry["metric"] = "gpu_1_usage"
     metrics, "gpu_0_usage")                      → 存入 OverlayElementConfig
  → metrics.gpu_0_usage = 0.0  ← BUG
  → 显示 "0.0%" 或 "--%"                         overlay_element.py:
                                                  _draw_hardware_value()
                                                  → getattr(metrics, "gpu_0_usage")
                                                  → 显示 "0%"  ← BUG
                                                  

### 5. Overlay 渲染 (LCD 屏幕) ###

services/overlay.py: _draw_text_elements() (L598-653)
  metric_name = cfg["metric"]    # "gpu_0_usage"
  value = getattr(metrics, metric_name)  # 0.0  ← BUG
  text = SystemService.format_metric(metric_name, value, ...)  # "0%"
  r.draw_text(surface, x, y, text, ...)  # 绘制到 LCD 表面
```

## 3. 关键文件清单

| 文件 | 职责 | 关键行号 |
|------|------|----------|
| `adapters/system/linux_platform.py` | Linux 传感器采集，NVML 调用 | L874-913 (_poll_nvidia), L959-1010 (_build_mapping) |
| `adapters/system/_base.py` | 基类传感器枚举，NVML 共享逻辑 | L333-371 (_poll_nvidia), L273-303 (_discover_nvidia) |
| `services/system.py` | 服务层聚合，Legacy+Indexed 双路径 | L201-264 (all_metrics), L295-329 (_GPU_NVMETRIC_SUFFIX, _populate_gpu_indexed) |
| `core/models/sensor.py` | 数据模型定义 | L21-99 (HardwareMetrics), L242-300 (SENSORS dict) |
| `ui/gui/metrics_mediator.py` | 定时器分发，温度转换 | L114-139 (_tick) |
| `ui/gui/uc_activity_sidebar.py` | Activity 侧边栏 UI | L34-126 (SensorItem), L105-116 (update_value) |
| `ui/gui/overlay_grid.py` | LCD 覆盖层面板 | L222-274 (to_overlay_config) |
| `ui/gui/overlay_element.py` | 单个覆盖元素渲染 | _draw_hardware_value() |
| `services/overlay.py` | Overlay 服务，图像合成 | L598-653 (_draw_text_elements) |
| `core/models/overlay.py` | Overlay 配置模型 | L131-149 (OverlayElementConfig) |

## 4. 其他已知问题

### 4.1 VRAM 单位不一致 (Base Class)

`_base.py` 的 `_poll_nvidia()` 中：
- `mem_used` 除以 `1024*1024*1024` → GB
- `mem_total` 除以 `1024*1024` → MB

但 SensorInfo 定义的单位都是 MB。Linux 平台的实现是正确的（都除以 `1024*1024`）。

### 4.2 平台 Key 对照表

| 平台 | GPU Util Key | 状态 |
|------|-------------|------|
| `_base.py` (基类) | `gpu_busy` | 与 `_GPU_NVMETRIC_SUFFIX` 匹配 ✓ |
| `linux_platform.py` | `gpu_util` | **不匹配** ✗ |
| `windows_platform.py` | `gpu_busy` (LHM) | 匹配 ✓ |
| `bsd_platform.py` | `gpu_busy` | 匹配 ✓ |
| `macos/sensors.py` | `iokit:gpu_busy` | 匹配 ✓ |

只有 Linux 平台的 NVML 路径使用了不同的 key 名称。
