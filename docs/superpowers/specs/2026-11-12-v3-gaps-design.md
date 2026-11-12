# v3 客户端实测缺口修复 —— 设计文档

**日期**：2026-11-12
**需求来源**：`temp/v3.md` §12（客户端验收实测 G1~G6）
**前置**：v1（板块行情）+ v2（fupan_v1）已上线；线上 `b51e4296`，本地 main `a6dc96a`（offset）待部署
**状态**：设计已与用户确认（会话内）

## 1. 缺口裁定

| 项 | 裁定 |
|---|---|
| G3 full 未生效 | **不成立**：远端 schema 已含 full（实测 list_tools）；客户端测的是旧部署 |
| G4 日期格式 | 无需改动（客户端已适配） |
| G1 | 半修待部署（offset/200 在 main）；真活：cap→600 + sort code/name + next_offset/has_more |
| G2 | 真活：board_name 前缀匹配 |
| G5 | 真活：冷取 17-24s（双 scope 全量分页清单所致，非成分股）→ 提速三件套 |
| G6 | 真活：get_board_members_tool（推翻 v2 §9 非目标，调用方现在需要） |

## 2. 设计

### G1 · get_boards 扩展

- `limit` 钳制 `1..600`（概念 531 一枪拉全）
- `sort` 增加 `code`（board_code 字典序**升序**）/ `name`（board_name 字典序升序）；原有三值保持降序——便于客户端稳定翻页全量拉取
- 响应新增 `has_more: bool` 与 `next_offset: int|null`（`offset+limit < total` 时为下一页起点）

### G2 · get_board_quote 前缀匹配

- 查找顺序：精确（by_code/by_name）→ 前缀扫描（`board_name.startswith(query)`，跨已加载 scope）
- 唯一前缀命中 → 正常返回 + `matched_by: "prefix"` 字段
- 多命中 → `success:true, data:null, status:"ambiguous", candidates:[{board_code,board_name,scope}]`（**新语义**，客户端据此二次选择；不误报 BOARD_NOT_FOUND）
- 零命中 → 维持 BOARD_NOT_FOUND（scope 快照失败时仍优先 unavailable）

### G5 · get_board_quote 提速三件套

- **lazy scope**：先拉行业清单，命中（精确或唯一前缀）即**不拉概念清单**（砍 6 页 ×1.5s）；digest（无参）路径不变仍拉双 scope
- **fields 轻量模式**：`fields: list[str] = []`，非空且全部 ∈ `{open, prev_close, open_pct, current}` 时走轻量路径——**仅支持 board_code 入参**（board_name 需清单做名→码，除非清单缓存已热）；轻量路径跳过清单/涨停池，直连 `fetch_board_open`；输出仅含请求字段 + 元数据；含清单派生字段（main_net_yi/up/down/limit_up/leader_*/current_pct 等）或非法名 → `INVALID_ARG`
- **`warmup_board_cache_tool(board_codes=[])`**：客户端竞价前触发——预拉双 scope 清单 + 指定板块开盘价 + 涨停池分桶；返回预热摘要
- 预期：行业票冷取 ~5s（1 页清单）；fields+board_code ~3s；预热后 0.1s

### G6 · get_board_members_tool

- `get_board_members_tool(board_code: str, limit: int = 100, offset: int = 0)`
- clist `fs=b:{BKcode}`，fields `f12,f14,f2,f3`，pz=100 分页（复用 `em_push2_get` pacing；页数上限 20 安全阀）
- 缓存 key `members:{code}:{date}` TTL 86400（成分股日内不变）
- total==0 → BOARD_NOT_FOUND；items=[{code,name,price,chg_pct}]；响应带 count/offset/limit/has_more/next_offset/truncated:false +（清单已热时顺带 up/down）

### 契约与文档

- SERVER_INSTRUCTIONS 更新（sort 取值、前缀语义、warmup、members 工具）
- 本设计 spec 回填验收实测

## 3. 任务分解

1. G1+G2（board_quotes：sort/分页扩展 + 前缀匹配）
2. G5（lazy scope + fields 轻量 + warmup 工具）
3. G6（get_board_members_tool）
4. live 验证 + SERVER_INSTRUCTIONS + build/push + 部署确认（一次重启覆盖 offset+全部新改动）
