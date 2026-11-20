# 需求v3 增量（N1~N4）—— 设计与处置记录

**日期**：2026-11-20
**需求来源**：`temp/需求v3.md`（增量版：N1 date 格式澄清 / N2 indices 历史契约 / N3 indices 补字段 / N4 index_code 接线）
**状态**：设计裁定 + 已实现

## 裁定与实现

| # | 裁定 | 实现 |
|---|---|---|
| N1 | schema 参数描述补格式（docstring Args 不进 fastmcp 参数 schema，须 `Annotated[..., Field(description=...)]`） | `fupan_v1_tool` / `get_daily_review_tool` 的 `date` 参数加 Annotated Field description（含 YYYY-MM-DD） |
| N2 | 无回退已满足（indices_section 严格日期校验、pools 严格快照）；`as_of` 语义修为**数据本身时点 = 被请求复盘日**（v1 契约 §7.2 的符合性修复，需求验收项亲自要求） | fupan 顶层 `as_of = str(day)`；stale 语义不变 |
| N3 | **v1 内加字段**（用户默认推荐路径）：N3 验收直接对现有 sections 要字段，增量键对字段访问型消费方零影响；N2/N3 均视为需求文档批准的契约增补，fupan_v2 冻结收紧时不再追溯 | `indices_section` 回看 14→24 天（保证前一根），items 增加 `open/high/low/prev_close`（缺值 None 不冒充 0）；daily_review 共享同段同步受益 |
| N4 | 根因：`stock_zh_index_spot_em` 的 268 个指数**不含深证成指/北证50**（参数其实接线，数据源缺标的） | `get_market_index` 重写走 `index_zh_a_hist`（fupan indices 同通道，任意指数代码；盘中含当日实时行）；信封加 `open/high/low/prev_close/name`；代码格式校验 |

## 验证

- 离线：206 passed / 17 deselected（+6 新测试：OHLC/前收、as_of=请求日、schema description、N4 三态）
- live（2026-11-20）：`399001` 深证成指 10583.2/prev 10659.41 ✓；`899050` 北证50 ✓；`indices[000001]` 全 OHLC+prev_close ✓

## 备注

- N3 的 prev_close 取序列前一根收盘（非 chg_pct 反推），消除 ±0.05% 精度损失
- get_market_index 信封为**非冻结工具**，字段增补自由；`source` 改为 `akshare_index_hist`
