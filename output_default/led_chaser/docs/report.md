# led_chaser 设计报告

## 参数

| 字段 | 值 |
|------|----|
| project | led_chaser |
| clock.freq_mhz | 50 |
| clock.reset | async_active_low |
| led_count | 4 |
| direction | left_to_right |
| interval_ms | 500 |
| divider (cycles) | 25000000 |
| wrap | true |
| enable_port | true |
| enable_polarity | active_high |

## 仿真

**通过**：所有断言（复位初始态 / 使能保持 / 方向 / 间隔 / wrap / 无 X-Z）均满足。

## 综合

**通过**（可综合），无锁存器推断。
