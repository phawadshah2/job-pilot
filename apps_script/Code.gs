/**
 * job-pilot -> Google Sheet receiver.
 *
 * Paste into Extensions > Apps Script of your tracking sheet, set TOKEN under
 * Project Settings > Script properties, then Deploy > New deployment > Web app
 * (Execute as: Me, Who has access: Anyone). Full steps in README.md.
 * After editing this file: Deploy > Manage deployments > (pencil) > Version: New
 * version > Deploy. That keeps the same /exec URL.
 *
 * "Anyone" only means anyone holding the secret URL *and* the TOKEN can use it:
 * write rows, and read back only the Key / Status / Submitted date / Follow-up
 * date columns. Without the token every request is rejected.
 *
 * action "pull" returns Key/Status/dates so status changes made in the sheet reach the local DB.
 * action "reset" clears every row (used by `jobpilot sheet-reset`, which asks for confirmation).
 * The header gets filter buttons on every column (Location, Work mode, Source, Status, ...).
 *
 * Rows are upserted by the Key column. For rows that already exist, only
 * `update_columns` are refreshed, and Status never moves backwards, so a row
 * you marked Submitted stays Submitted.
 */
const SHEET_NAME = 'Jobs';
const STATUSES = ['New', 'Tailoring', 'Ready', 'Approved', 'Pending', 'Failed', 'Manual action required',
                  'Submitted', 'Interview', 'Offer', 'Rejected', 'Skipped'];
const STATUS_RANK = { New: 0, Tailoring: 1, Ready: 2, Approved: 3, Pending: 4, Failed: 4, 'Manual action required': 4,
                      Submitted: 5, Interview: 6, Offer: 7, Rejected: 7, Skipped: 7, Applied: 5 };

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const body = JSON.parse(e.postData.contents);
    const expected = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!expected || body.token !== expected) return json({ ok: false, error: 'bad token' });

    if (body.action === 'pull') return json({ ok: true, rows: readRows() });
    if (body.action === 'reset') return json(resetSheet(body.columns));

    const sheet = getSheet(body.columns);
    const header = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    const keyIdx = header.indexOf('Key');
    const statusIdx = header.indexOf('Status');

    // Key -> sheet row number
    const rowOf = {};
    if (sheet.getLastRow() > 1) {
      sheet.getRange(2, keyIdx + 1, sheet.getLastRow() - 1, 1).getValues()
        .forEach((r, i) => { rowOf[String(r[0])] = i + 2; });
    }

    const updateCols = (body.update_columns || []).filter(c => header.indexOf(c) >= 0);
    const fresh = [];
    let updated = 0;

    body.rows.forEach(values => {
      const rec = {};
      body.columns.forEach((c, i) => { rec[c] = values[i]; });
      const existingRow = rowOf[String(rec.Key)];

      if (!existingRow) {
        fresh.push(header.map(c => (c in rec ? rec[c] : '')));
        return;
      }
      if (!updateCols.length) return;
      const range = sheet.getRange(existingRow, 1, 1, header.length);
      const current = range.getValues()[0];
      let changed = false;
      updateCols.forEach(c => {
        const i = header.indexOf(c);
        // Status only moves forward (equal ranks may switch, e.g. Failed -> Pending).
        if (c === 'Status' && (STATUS_RANK[current[i]] || 0) > (STATUS_RANK[rec[c]] || 0)) return;
        if (rec[c] !== '' && rec[c] !== current[i]) { current[i] = rec[c]; changed = true; }
      });
      if (changed) { range.setValues([current]); updated++; }
    });

    if (fresh.length) {
      sheet.getRange(sheet.getLastRow() + 1, 1, fresh.length, header.length).setValues(fresh);
    }
    return json({ ok: true, added: fresh.length, updated: updated });
  } catch (err) {
    return json({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// For `jobpilot pull`: what you changed in the sheet (Status, dates) flows back to the local DB.
function readRows() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet || sheet.getLastRow() < 2) return [];
  const values = sheet.getDataRange().getValues();
  const header = values[0];
  const want = ['Key', 'Status', 'Submitted date', 'Follow-up date'];
  const idx = want.map(c => header.indexOf(c));
  const fmt = v => (v instanceof Date) ? Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd') : String(v);
  return values.slice(1).filter(r => r[idx[0]]).map(r => {
    const o = {};
    want.forEach((c, i) => { o[c] = idx[i] >= 0 ? fmt(r[idx[i]]) : ''; });
    return o;
  });
}

// `jobpilot sheet-reset`: wipe every row (header is rebuilt with filters and dropdowns).
function resetSheet(columns) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (sheet) {
    if (sheet.getFilter()) sheet.getFilter().remove();
    sheet.getRange(1, 1, sheet.getMaxRows(), sheet.getMaxColumns()).clearDataValidations();
    sheet.clear();
  }
  getSheet(columns);
  return { ok: true, reset: true };
}

// Filter buttons on every header cell (Location, Work mode, Source, Status...). Created once over
// whole columns so new rows are included and your active filter choices survive later updates.
function ensureFilter(sheet, width) {
  if (sheet.getFilter()) return;
  sheet.getRange(1, 1, sheet.getMaxRows(), width).createFilter();
}

function getSheet(columns) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(columns);
    sheet.getRange(1, 1, 1, columns.length).setFontWeight('bold').setBackground('#e8eaf6');
    sheet.setFrozenRows(1);
    const widths = { Company: 180, Role: 260, Location: 180, 'Work mode': 120, Salary: 150, Source: 140, URL: 220,
                     'Match explanation': 320, 'Missing requirements': 240, CV: 200, 'Cover letter': 200,
                     Status: 170, Notes: 260 };
    columns.forEach((c, i) => { if (widths[c]) sheet.setColumnWidth(i + 1, widths[c]); });
  }
  ensureFilter(sheet, sheet.getLastColumn());
  // (Re)apply the Status dropdown so new statuses show up after an update.
  const statusCol = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].indexOf('Status') + 1;
  if (statusCol > 0) {
    const rule = SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).build();
    sheet.getRange(2, statusCol, 1000, 1).setDataValidation(rule);
  }
  return sheet;
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
