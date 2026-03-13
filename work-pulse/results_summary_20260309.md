# Work Pulse — 全模块测试结果汇总
生成日期：2026-03-09

最大分值：Ingestion/121 · Risk/104 · Priority/97 · Drift/104 · 合计/426

---

## 综合对比表

> 每单元格：`得分/满分（通过数P 失败数F）`
> ⚠️ Large hybrid ingestion 因 LLM 调用失败导致 dedup 缓存了空任务列表，ING-04/05/06 级联失败，结果不可靠。

| 方法 | Ingestion /121 | Risk /104 | Priority /97 | Drift /104 | **合计 /426** |
|------|---------------|-----------|--------------|------------|--------------|
| Traditional | 93 (12P 3F) | 76 (10P 3F) | 72 (9P 0F) | 12 (2P 0F)¹ | **253** |
| Local hybrid | **121** (15P 0F) | **104** (13P 0F) | 91 (11P 1F) | 82 (10P 3F) | **398** |
| Local full | 120 (14P 1F) | — | — | — | — |
| Medium hybrid | 118 (14P 1F) | 100 (12P 1F) | **94** (11P 1F) | 78 (9P 4F) | **390** |
| Medium full | **121** (15P 0F) | — | — | — | — |
| Large hybrid | 118 (14P 1F) | **104** (13P 0F) | 82 (10P 2F) | 82 (10P 3F) | **386** |
| Large full | 119 (14P 1F) | — | — | — | — |

¹ Drift traditional 仅运行了 2 个有 traditional 基线的测试（其余为 llm_only）

---

## 各模块详细结果

### Ingestion（/121，共 15 个测试）

| 方法 | 得分 | 通过 | 失败 | 关键失败测试 | 最佳运行时间戳 |
|------|------|------|------|-------------|--------------|
| Traditional | 93 | 12 | 3 | ING-04 ING-05 ING-10 | 20260308_180822 |
| Local hybrid | **121** | 15 | 0 | — | 20260309_090848 |
| Local full | 120 | 14 | 1 | ING-04 (partial 9.1/10) | 20260309_092935 |
| Medium hybrid | 118 | 14 | 1 | ING-04 (partial 7.1/10) | 20260309_093223 |
| Medium full | **121** | 15 | 0 | — | 20260309_093223 |
| Large hybrid | 118 | 14 | 1 | ING-04 (partial 7.1/10) | 20260309_101940 |
| Large full | 119 | 14 | 1 | ING-04 (partial 8.3/10) | 20260309_093346 |

**ING-04 是所有方法的共同瓶颈**：action item 提取 F1 阈值 0.80，模型普遍提取比 ground truth（5条）更多的 items，压低 precision。Medium full 是唯一精确提取 5 条的，F1=0.80 刚好过关。

---

### Risk（/104，共 13 个测试）

| 方法 | 得分 | 通过 | 失败 | 关键失败测试 | 最佳运行时间戳 |
|------|------|------|------|-------------|--------------|
| Traditional | 76 | 10 | 3 | RSK-01 RSK-02 RSK-06 | 20260308_181658 |
| Local (hybrid) | **104** | 13 | 0 | — | 20260309_082858 |
| Medium (hybrid) | 100 | 12 | 1 | RSK-05 (hallucination) | 20260309_072359 |
| Large (hybrid) | **104** | 13 | 0 | — | 20260309_072009 |

> Risk 未运行 full 模式（只有 ingestion 新增了 full 路径）。
> Traditional 固有失败：RSK-01/02 描述模板化，SequenceMatcher ratio ~0.34，低于 0.8 阈值。

---

### Priority（/97，共 12 个测试，Traditional 仅跑 9 个）

| 方法 | 得分 | 通过 | 失败 | 关键失败测试 | 最佳运行时间戳 |
|------|------|------|------|-------------|--------------|
| Traditional | 72 | 9 | 0 | — (仅跑有基线的 9 项) | 20260308_181523 |
| Local (hybrid) | 91 | 11 | 1 | PRI-05 (hallucinations=3) | 20260309_083738 |
| Medium (hybrid) | **94** | 11 | 1 | PRI-05 | 20260309_061856 |
| Large (hybrid) | 82 | 10 | 2 | PRI-05 PRI-10 | 20260309_061856 |

> PRI-05 是所有 LLM 方法的共同失败点（hallucination 检测）。
> Medium 意外优于 Large（Large 在 PRI-10 一致性测试上额外失败）。

---

### Drift（/104，共 13 个测试，Traditional 仅跑 2 个）

| 方法 | 得分 | 通过 | 失败 | 关键失败测试 | 最佳运行时间戳 |
|------|------|------|------|-------------|--------------|
| Traditional | 12 | 2 | 0 | — (仅跑 2 个有基线的测试) | 20260308_180822 |
| Local (hybrid) | 82 | 10 | 3 | DFT-03 DFT-05 DFT-06 | 20260309_084635 |
| Medium (hybrid) | 78 | 9 | 4 | DFT-03 DFT-05 DFT-06 DFT-XX | 20260309_061856 |
| Large (hybrid) | 82 | 10 | 3 | DFT-03 DFT-05 DFT-06 | 20260309_061856 |

> DFT-03/05/06 是共同失败点（scope classification 边界情况）。

---

## 延迟对比（Ingestion 模块，单次 LLM 调用平均延迟）

| 方法 | 平均单次调用 | 整批运行（15 tests × 2 modes） |
|------|------------|-------------------------------|
| Traditional | — | — |
| Local hybrid | 12,080 ms | 162.5s（含 full） |
| Local full | 9,644 ms | ↑ |
| Medium hybrid | 3,644 ms | 76.0s（含 full） |
| Medium full | 3,680 ms | ↑ |
| Large hybrid | 6,397 ms | 116.2s（含 full） |
| Large full | 7,629 ms | ↑ |

---

## 推荐方案

| 场景 | 推荐方法 | 理由 |
|------|---------|------|
| 生产最优质量 | **Medium full** (ingestion) + **Local/Large** (risk) | Medium full ingestion 满分；risk local/large 均满分 |
| 零成本本地运行 | **Local hybrid** | Ingestion 满分，Risk 满分，综合 398/426 |
| 快速在线处理 | **Medium hybrid** | 最低延迟（3.6s/call），综合 390/426，仅次于 Local |
| 避免使用 | Large hybrid (ingestion) | LLM 失败时级联缓存损坏，导致多测试归零 |

---

## 已知问题

| 问题 | 影响 | 状态 |
|------|------|------|
| Large hybrid ingestion LLM 失败级联 | ING-04/05/06 全 0，结果不可靠 | 未修复（建议加 fallback） |
| ING-04 所有方法 precision 偏低 | 仅 medium full 过关 | prompt 可继续优化 |
| PRI-05 hallucination 检测 | 所有 LLM 方法失败 | 未优化 |
| DFT-03/05/06 scope 分类边界 | 所有方法失败 | 未优化 |

---

## 原始结果文件索引

| 模块 | 方法 | JSON 文件 |
|------|------|----------|
| Ingestion (traditional) | traditional | test_results_20260308_180822.json |
| Risk (all tiers) | trad/local/medium/large | test_results_20260309_082858.json (local best) |
| Priority (all tiers) | trad/local/medium/large | test_results_20260309_083738.json (local), 20260309_061856.json (med/large) |
| Drift (all tiers) | trad/local/medium/large | test_results_20260309_084635.json (local), 20260309_061856.json (med/large) |
| Ingestion local hybrid+full | llm_local, llm_local_full | test_results_20260309_092935.json |
| Ingestion medium hybrid+full | llm_medium, llm_medium_full | test_results_20260309_093223.json |
| Ingestion large hybrid (rerun) | llm_large | test_results_20260309_101940.json |
| Ingestion large full | llm_large_full | test_results_20260309_093346.json |
