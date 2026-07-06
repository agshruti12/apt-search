// Apt Search — Google Apps Script Web App
// Deploy as: Execute as "Me", Access "Anyone"
// Paste this into Extensions → Apps Script in your Google Sheet

const SHEET_NAME = "Apartment Listings";
const ID_COL = 1;       // Column A
const STATUS_COL = 18;  // Column R
const CONTACT_NAME_COL = 19; // Column S
const CONTACT_EMAIL_COL = 20; // Column T
const CONTACT_PHONE_COL = 21; // Column U

function doPost(e) {
  const data = JSON.parse(e.postData.contents);
  const action = data.action;
  const id = parseInt(data.id);

  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  const rowNum = _findRowById(sheet, id);

  if (!rowNum) {
    return _json({ ok: false, error: "Listing not found: " + id });
  }

  if (action === "delete") {
    sheet.deleteRow(rowNum);
    return _json({ ok: true });
  }

  if (action === "favorite") {
    const current = sheet.getRange(rowNum, STATUS_COL).getValue();
    const newStatus = current === "liked" ? "new" : "liked";
    sheet.getRange(rowNum, STATUS_COL).setValue(newStatus);
    return _json({ ok: true, status: newStatus });
  }

  if (action === "update_contact") {
    if (data.contact_name !== undefined)
      sheet.getRange(rowNum, CONTACT_NAME_COL).setValue(data.contact_name);
    if (data.contact_email !== undefined)
      sheet.getRange(rowNum, CONTACT_EMAIL_COL).setValue(data.contact_email);
    if (data.contact_phone !== undefined)
      sheet.getRange(rowNum, CONTACT_PHONE_COL).setValue(data.contact_phone);
    return _json({ ok: true });
  }

  return _json({ ok: false, error: "Unknown action: " + action });
}

function doGet(e) {
  // Simple health check
  return _json({ ok: true, message: "Apt Search API is running." });
}

function _findRowById(sheet, id) {
  const data = sheet.getRange(2, ID_COL, sheet.getLastRow() - 1, 1).getValues();
  for (let i = 0; i < data.length; i++) {
    if (parseInt(data[i][0]) === id) return i + 2; // +2: 1-based + header row
  }
  return null;
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
