# akshare MCP 全球市场监控接口需求文档

> 版本: v1.0 | 日期: 2026-09-04
> 需求方: 光明 (Hermes投研工作流) | 目标服务: 192.168.3.10:18881 (akshare MCP, 当前v4.0.2)
> 背景: A股与日韩美股共振加强（2026-08-26全球科技同跌、09-03全球债市风暴传导），当前MCP纯A股视野，缺隔夜输入变量。用户持仓结构（光模块=美股AI链、利基存储=韩日存储链）天然是全球共振结构。

---

## 一、设计原则

1. **聚合优先**: 2个聚合工具优于15个细粒度工具，一次调用返回一组关联数据，减少MCP往返
2. **盘前场景优先**: 核心消费场景是A股开盘前(8:00-9:15)拉一次全球快照，判断当日输入性风险
3. **降级容错**: 单一数据源失败不阻塞整组返回，失败的组返回 `null` + `error` 字段
4. **沿用现有风格**: 返回结构与现有工具一致——`{"success": bool, "data": {...}}`，中文工具描述，金额单位亿元

---

## 二、需求清单

### P0 —— 每个交易日盘前必看（直接驱动持仓）

#### 工具1: `get_global_markets_tool` (聚合快照)

**用途**: 盘前一键拉取全球市场状态，判断当日A股输入性风险

**参数**:
```json
{
  "groups": ["美股", "美债", "汇率", "亚太", "商品", "恐慌"]  // 可选, 默认全部
}
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "as_of": "2026-09-04T06:00:00+08:00",  // 数据截止时间
    "美股": {
      "note": "9/3收盘",
      "indexes": [
        {"name": "纳斯达克", "code": ".IXIC", "close": 26402.42, "chg_pct": -0.52},
        {"name": "标普500", "code": ".INX", "close": 7711.76, "chg_pct": -0.25},
        {"name": "道琼斯", "code": ".DJI", "close": 53559.99, "chg_pct": -0.02},
        {"name": "费城半导体", "code": ".SOX", "close": null, "chg_pct": null, "error": "source timeout"}
      ]
    },
    "美债": {
      "us10y": 4.80, "us10y_chg_bp": 8,      // %, 基点变动
      "us30y": 5.28, "us30y_chg_bp": 5,
      "japan10y": 3.01, "japan10y_chg_bp": 3  // 日本10Y(全球长债领先指标)
    },
    "汇率": {
      "dxy": 99.17, "dxy_chg_pct": 0.25,
      "usdcnh": 7.12, "usdcnh_chg_pct": 0.05
    },
    "亚太": {
      "note": "9/4盘中或收盘",
      "indexes": [
        {"name": "日经225", "close": null, "chg_pct": null},
        {"name": "KOSPI", "close": null, "chg_pct": null},
        {"name": "恒生科技", "close": null, "chg_pct": null}
      ]
    },
    "商品": {
      "brent": 92.3, "brent_chg_pct": 1.2,
      "wti": 88.9, "wti_chg_pct": 1.1,
      "gold": 4380.0, "gold_chg_pct": -0.8
    },
    "恐慌": {
      "vix": 24.5, "vix_chg_pct": 5.2
    }
  }
}
```

**数据源建议** (akshare原生函数优先):
| 字段 | akshare函数 | 备注 |
|---|---|---|
| 美股三大指数 | `index_us_stock_sina(symbol=".IXIC")` | 新浪源,实时性好 |
| 费城半导体 | `index_us_stock_sina(symbol=".SOX")` 或东财 `index_global` | AI算力链β最直接的映射 |
| 美债收益率 | `bond_zh_us_rate()` (含美债) 或东财全球债券 | 10Y/30Y |
| 日债收益率 | `bond_global_...` / 东财 | 日本是全球长债风暴领先指标 |
| DXY | `index_global` 或 fx相关 | |
| USDCNH | `fx_spot_quote` / 腾讯 | |
| 日经/KOSPI/恒科 | `index_global` / `stock_hk_spot_em` | |
| 布伦特/WTI | `futures_global_...` / `energy_oil_hist` | 美伊冲突主线监控 |
| 黄金 | `spot_hist_sge` / `futures_global_...` | COMEX或伦敦金 |
| VIX | `index_vix_...` / 新浪 `index_global` | >25=A股算力链开盘折价 |

**兜底数据源**(akshare函数失败时): 腾讯 `qt.gtimg.cn/q=usIXIC,usINX,usDJI,usSOX,usIXIC` — 已实测可用, GBK编码需转码, 字段: 现价[3]/昨收[4]/涨跌%[32]。日经=`jpNI225`, KOSPI=`krKOSPI`。**建议在MCP层内置该兜底**。

---

#### 工具2: `get_stock_global_snapshot_tool` (全球个股快照)

**用途**: 拉取持仓映射的海外对标组股价——光模块链(NVDA/MRVL/COHR)、存储链(海力士/三星/铠侠)

**参数**:
```json
{
  "symbols": ["NVDA", "MRVL", "COHR", "000660.KS", "005930.KS"],  // 支持: 美股ticker/带后缀的亚股代码
  "preset": "ai_chain"  // 可选快捷组: "ai_chain"=AI算力链对标, "storage"=存储链对标, "all"=全部; 与symbols二选一
}
```

**预设组定义**(服务端内置,可后续扩展):
- `ai_chain` (新易盛/中际旭创映射): NVDA, AVGO, MRVL, COHR, LITE, AMphenol(APH), ANET
- `storage` (东芯映射): SK海力士(000660.KS), 三星电子(005930.KS), 铠侠(285A.T), 美光(MU), 闪迪(SNDK)
- `japan_rate_watch` (日债风暴监控): 不适用个股,走工具1

**返回结构**:
```json
{
  "success": true,
  "data": {
    "as_of": "2026-09-04T05:00:00+08:00",
    "quotes": [
      {"symbol": "NVDA", "name": "英伟达", "close": 1080.5, "chg_pct": -1.2, "after_hours_pct": 0.3, "market": "US"},
      {"symbol": "000660.KS", "name": "SK海力士", "close": 215000, "chg_pct": -3.8, "market": "KR"}
    ]
  }
}
```

**数据源建议**: `stock_us_spot_em()`(美股全量快照后过滤), `stock_hk_spot_em()`部分覆盖; 日韩股建议东财全球行情接口 `quote.eastmoney.com` (center接口, secid: 100.000660 等) 或雅虎财经兜底(需代理)。**日韩个股是本工具的实现难点, 优先级可以低于美股**。

---

### P1 —— 每周/事件日看(传导链验证)

#### 工具3: `get_fed_watch_tool` (美联储预期)

**用途**: 议息会议前(下次9/16)跟踪加息概率

**返回**:
```json
{
  "success": true,
  "data": {
    "next_meeting": "2026-09-16",
    "hike_25bp_prob": 0.68,
    "hold_prob": 0.32,
    "source": "CME FedWatch(转引) 或 利率期货隐含(推算)",
    "as_of": "2026-09-03"
  }
}
```

**实现说明**: CME FedWatch无免费API, 可用**2Y美债收益率变动反推**或tavily搜索转引(标注置信度)。简单版: 只返回10Y-2Y利差 + 2Y周变动, 让agent自行解读。

#### 工具4: `get_global_linkage_review_tool` (共振复盘)

**用途**: 周度或事件日复盘"隔夜全球→当日A股"传导是否成立

**逻辑**: 输入日期, 并行拉取: 前一交易日美股/美债/VIX + 当日A股(现有工具: market_index + market_breadth + fund_flow电子/通信板块), 输出对照表

**返回**:
```json
{
  "success": true,
  "data": {
    "date": "2026-09-03",
    "overnight": {"nasdaq_pct": -1.05, "sox_pct": -1.8, "us10y_chg_bp": 8, "vix": 24.5},
    "a_share": {"sse_pct": -0.19, "chiNext_pct": -0.68, "up_down_ratio": "4337/1126→次日修复", "elec_fund_flow_yi": -108.93, "comm_fund_flow_yi": -45.2},
    "linkage_verdict": "强共振: 美债+VIX双升触发, 电子板块资金流出108亿, 次日A股独立修复(9/4 4337涨)"
  }
}
```

**说明**: linkage_verdict可由服务端规则模板生成, 也可只给数据让agent判读(推荐后者, 服务端保持薄逻辑)。

---

### P2 —— 增强项(可选)

| # | 需求 | 说明 | 优先级理由 |
|---|---|---|---|
| 1 | 美股盘前涨跌(NVDA/光模块龙头) | 判断A股开盘缺口方向 | 腾讯接口有pre-market字段, 增量小 |
| 2 | 台积电/日月光快照 | AI硬件上游验证 | 并入工具2的preset即可 |
| 3 | 港股通南向资金 | `stock_hsgt_fund_flow_summary_em` 类 | akshare原生,封装成本低 |
| 4 | 美股ETF(词元通缩叙事监控): AIQ/SMH/ BOTZ | AI情绪代理指标 | 并入工具1或2 |

---

## 三、验收标准

1. **工具1**: 盘前8:30调用, 10秒内返回, 任一美股指数失败不阻塞其他组
2. **工具2**: 传preset="ai_chain"返回7只美股报价; 日韩股允许返回error占位
3. **工具3**: 至少返回2Y收益率+利差结构化数据
4. **工具4**: 输入2026-09-03能复现"美债风暴→电子-108亿"的传导对照
5. 全部工具: 中文描述, 返回结构与v4.0.2现有工具一致(`success/data/error`)
6. **文档同步**: 落地后在 `akshare-tavily-stock-workflow` skill的工具分配表追加新工具行

## 四、实现优先级建议

**第一批(1-2天)**: 工具1最小版——美股三大指数+费城半导体+美债10Y/30Y+VIX+DXY(全部有akshare原生函数或腾讯兜底)
**第二批(3-5天)**: 工具2美股部分(preset=ai_chain/storage的美股腿) + 工具3简版
**第三批**: 工具2日韩腿 + 工具4

---

*需求方使用场景示例:*
*"9/4盘前: get_global_markets_tool() → 美股隔夜跌0.5%、SOX-1.8%、10Y美债4.8%、VIX 24 → 判断: 输入性压力中等, A股算力链低开1-2%概率大 → 结合新易盛390警戒线制定当日计划"*
