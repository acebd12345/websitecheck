# -*- coding: utf-8 -*-
"""web_check 統一引擎(整併地基)。

整併自:
- web_check monthly/webcheck_ai.py  → 靜態抓取 + 地端 AI 判讀 + playwright 分層
- TCGweb-health-checker analyzer/    → 日期抽取(engine/dates.py)
- TCGweb-health-checker crawler/     → SPA/frameset 偵測邏輯(移植進 fetch_layered 的靜態判斷)

設計:一次靜態優先抓取 → 上層兩剖面(健康掃描 / 合規檢核)共用。
"""
