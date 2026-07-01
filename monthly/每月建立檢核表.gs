/**
 * 資訊局網站檢核表 — 每月自動建立（Google Apps Script）
 *
 * 以「你本人」身分執行，複製上月的原生 Google Sheet → 產生本月新檔，
 * 放進對應年度資料夾、改檔名、更新各分頁填表日期。
 * 服務帳戶(Python)隨後會把檢測結果與GA流量寫進這份新檔。
 *
 * === 首次設定（做一次）===
 * 1. 開 https://script.google.com → 新增專案 → 貼上本檔全部內容
 * 2. 若資料夾內還沒有「原生 Google Sheet」格式的檢核表（目前都是上傳的 .xlsx）：
 *    先執行一次 seedFromLatestXlsx（選單選此函式 → 執行）
 *    ⚠ 需先在左側「服務」加入「Drive API」(進階服務)
 *    或手動：在 Drive 把最新 .xlsx 用 Google 試算表開啟 → 檔案 → 另存為 Google 試算表
 * 3. 執行一次 installMonthlyTrigger → 安裝「每月1號自動執行」觸發器
 * 4. 之後每月1號 Google 雲端自動建立本月檢核表，電腦不用開
 *
 * 手動測試：直接執行 createMonthlyChecklist 即可立刻產生本月檔
 */

// ===== 設定 =====
const ROOT_FOLDER_ID = 'YOUR_DRIVE_FOLDER_ID'; // 改成存放檢核表的 Drive 資料夾 ID(網址 /folders/ 後那串)
const FILE_KEYWORD = '資訊局網站檢核表';                      // 檔名關鍵字
const RUN_HOUR = 1;                                          // 觸發器執行時段(凌晨1點)

// ===== 主程式：建立本月檢核表 =====
function createMonthlyChecklist() {
  const now = new Date();
  const filingY = now.getFullYear() - 1911;   // 民國年
  const filingM = now.getMonth() + 1;          // 填表月
  const targetTag = '' + filingY + pad2_(filingM);   // 如 11506

  // 數據範圍 = 前一個月
  let dY = filingY, dM = filingM - 1;
  if (dM === 0) { dM = 12; dY = filingY - 1; }
  const lastDay = new Date((dY + 1911), dM, 0).getDate();
  const dStart = '' + dY + pad2_(dM) + '01';
  const dEnd = '' + dY + pad2_(dM) + pad2_(lastDay);
  const targetName = targetTag + FILE_KEYWORD + '（數據範圍' + dStart + '~' + dEnd + '）';

  // 已存在就不重複建立
  const existing = findFileByName_(targetName);
  if (existing) {
    Logger.log('本月檔案已存在，略過：' + targetName);
    return existing.getId();
  }

  // 找最近一份原生 Sheet 檢核表當範本（通常是上月）
  const src = findLatestNativeChecklist_(targetTag);
  if (!src) {
    throw new Error('找不到任何原生 Google Sheet 格式的檢核表，請先執行 seedFromLatestXlsx 或手動轉一份');
  }
  Logger.log('複製範本：' + src.getName());

  // 複製 → 放進年度資料夾
  const yearFolder = getOrCreateYearFolder_(filingY);
  const copy = src.makeCopy(targetName, yearFolder);

  // 更新各分頁的填表日期(E2)
  updateFilingDates_(copy.getId(), filingY, filingM);

  Logger.log('已建立：' + targetName + ' → 資料夾 ' + filingY + '年');
  return copy.getId();
}

// ===== 安裝每月觸發器（執行一次）=====
function installMonthlyTrigger() {
  // 先移除同名舊觸發器，避免重複
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'createMonthlyChecklist') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('createMonthlyChecklist')
    .timeBased().onMonthDay(1).atHour(RUN_HOUR).create();
  Logger.log('已安裝觸發器：每月1號 ' + RUN_HOUR + ' 點自動建立檢核表');
}

// ===== 首次種子：把最新 .xlsx 轉成原生 Sheet（執行一次；需啟用 Drive 進階服務）=====
function seedFromLatestXlsx() {
  const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
  let latest = null, latestTag = '';
  const it = filesUnder_(root);
  while (it.length) {
    const f = it.pop();
    const name = f.getName();
    if (f.getMimeType() === MimeType.MICROSOFT_EXCEL && name.indexOf(FILE_KEYWORD) >= 0) {
      const tag = rocTag_(name);
      if (tag && tag > latestTag) { latestTag = tag; latest = f; }
    }
  }
  if (!latest) throw new Error('資料夾內找不到 .xlsx 檢核表可轉換');
  const newName = latest.getName().replace(/\.xlsx$/i, '') + '（原生）';
  // 需在「服務」啟用 Drive API 進階服務
  const resource = {
    title: newName,
    mimeType: MimeType.GOOGLE_SHEETS,
    parents: [{ id: getOrCreateYearFolder_(parseInt(latestTag.substring(0, 3), 10)).getId() }]
  };
  const created = Drive.Files.insert(resource, latest.getBlob());
  Logger.log('已轉成原生 Sheet：' + newName + '（ID ' + created.id + '）');
}

// ===== 工具函式 =====
function pad2_(n) { return (n < 10 ? '0' : '') + n; }

function rocTag_(name) {
  const m = name.match(/^(\d{5})/);   // 開頭5碼民國年月，如 11506
  return m ? m[1] : '';
}

function findLatestNativeChecklist_(beforeTag) {
  const q = "title contains '" + FILE_KEYWORD +
    "' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false";
  const it = DriveApp.searchFiles(q);
  let best = null, bestTag = '';
  while (it.hasNext()) {
    const f = it.next();
    const tag = rocTag_(f.getName());
    if (tag && tag < beforeTag && tag > bestTag) { bestTag = tag; best = f; }
  }
  // 若沒有比目標月更早的，就取整體最新一份
  if (!best) {
    const it2 = DriveApp.searchFiles(q);
    while (it2.hasNext()) {
      const f = it2.next();
      const tag = rocTag_(f.getName());
      if (tag && tag > bestTag) { bestTag = tag; best = f; }
    }
  }
  return best;
}

function getOrCreateYearFolder_(rocYear) {
  const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
  const name = rocYear + '年';
  const it = root.getFoldersByName(name);
  return it.hasNext() ? it.next() : root.createFolder(name);
}

function findFileByName_(name) {
  const it = DriveApp.searchFiles("title = '" + name.replace(/'/g, "\\'") + "' and trashed = false");
  return it.hasNext() ? it.next() : null;
}

function filesUnder_(folder) {
  // 回傳該資料夾(含子資料夾)的所有檔案陣列
  const out = [];
  const fit = folder.getFiles();
  while (fit.hasNext()) out.push(fit.next());
  const dit = folder.getFolders();
  while (dit.hasNext()) filesUnder_(dit.next()).forEach(function (f) { out.push(f); });
  return out;
}

function updateFilingDates_(sheetId, filingY, filingM) {
  const ss = SpreadsheetApp.openById(sheetId);
  const dateStr = '填表日期：' + filingY + ' 年 ' + filingM + ' 月  日';
  ss.getSheets().forEach(function (sh) {
    try {
      const e2 = sh.getRange('E2').getValue();
      if (typeof e2 === 'string' && e2.indexOf('填表日期') >= 0) {
        sh.getRange('E2').setValue(dateStr);
      }
    } catch (e) { /* 略過無此格的分頁 */ }
  });
}
