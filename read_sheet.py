# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import config
import gspread
from google.oauth2.service_account import Credentials

# 連接 Google Sheets
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(config.GA_KEY_FILE, scopes=scopes)
gc = gspread.authorize(creds)

# 打開主設定表
sheet = gc.open_by_key(config.MASTER_SHEET_ID)
ws = sheet.worksheet(config.MASTER_WORKSHEET)

# 取得所有資料
rows = ws.get_all_values()
print(f'總資料列數（含標題）: {len(rows)}')
print(f'網站數量: {len(rows) - 1}')
print()
print('網站清單:')
for i in range(1, min(20, len(rows))):
    sheet_col = rows[i][0] if len(rows[i]) > 0 else ''
    name = rows[i][1] if len(rows[i]) > 1 else ''
    url = rows[i][2] if len(rows[i]) > 2 else ''
    print(f'{i-1:2d}. {sheet_col:20s} | {name:30s} | {url}')
