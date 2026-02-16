/**
 * Google Apps Script for Lower Merion trash/reminder signup.
 * - Resolves Street Address -> Zone using the Township API (runs from Google, avoids Render 403).
 * - Posts {street_address, phone_number, consent, zone} to your webhook.
 * - Updates Sheet with zone and collection_day from webhook response.
 * - Installs exactly ONE "On form submit" trigger for onFormSubmit(e).
 */

const WEBHOOK_URL = 'https://trash-reminder-bot-1.onrender.com/'; // <-- your webhook URL

// ----- Township endpoints -----
const TOKEN_URL        = 'https://www.lowermerion.org/Home/GetToken';
const SEARCH_URL       = 'https://flex.visioninternet.com/api/FeFlexComponent/Get';
const SUGGEST_URL      = 'https://www.lowermerion.org/Home/AddressSearch?term=';
const COMPONENT_GUID   = 'f05e2a62-e807-4f30-b450-c2c48770ba5c';
const LIST_UNIQUE_NAME = 'VHWQOE27X21B7R8';

// ----- EDIT THESE to match your Google Form question titles exactly -----
const Q_ADDRESS = 'Street Address';                      // e.g. "Street Address"
const Q_PHONE   = 'Phone Number';                        // e.g. "Phone Number"
const Q_ZONE    = 'Zone';
const Q_CONSENT = 'Consent to Receive Messages'; // e.g. checkbox text

// Optional: which consent strings count as "agree" on the backend
const CONSENT_OK = ['agree','yes','true','1'];

/** Normalize "229 Ardleigh Rd, Penn Valley, PA 19072" -> "229 Ardleigh Rd" (strip unit/city/ZIP). */
function normalizeStreetOnly_(addr) {
  if (!addr) return '';
  let a = addr.trim();
  // drop everything after first comma
  a = a.split(',')[0];
  // drop units
  a = a.replace(/\b(apt|apartment|unit|ste|suite|#|fl|floor|bldg|building)\b.*$/i, '').trim();
  // collapse spaces
  a = a.replace(/\s{2,}/g, ' ').trim();
  return a;
}

/** Try the site's address autocomplete to get the canonical string the UI uses. */
function addressSuggest_(q) {
  if (!q) return null;
  const url = 'https://www.lowermerion.org/Home/AddressSearch?term=' + encodeURIComponent(q);
  const r = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
  const txt = r.getContentText();

  // The endpoint often returns JSON array like ["229 ARDLEIGH RD, PENN VALLEY, PA 19072", ...]
  try {
    const arr = JSON.parse(txt);
    if (Array.isArray(arr) && arr.length) return String(arr[0]).trim();
  } catch (e) {
    // Some builds return HTML with <li>…; extract the first <li> text
    const m = txt.match(/<li[^>]*>(.*?)<\/li>/i);
    if (m) return m[1].replace(/<[^>]+>/g, '').trim();
  }
  return null;
}

/** Smart zone lookup: try component (raw), component (normalized), then suggest+component. */
function lookupZoneSmart_(addr) {
  if (!addr) return null;
  const token = fetchLmToken_();
  if (!token) return null;

  // 1) raw
  let z = lookupZone_(addr, token);
  if (z) return z;

  // 2) normalized street-only
  const norm = normalizeStreetOnly_(addr);
  if (norm && norm !== addr) {
    z = lookupZone_(norm, token);
    if (z) return z;
  }

  // 3) suggestions -> canonical -> component
  const sug = addressSuggest_(addr) || addressSuggest_(norm);
  if (sug) {
    z = lookupZone_(sug, token);
    if (z) return z;
  }

  return null;
}


/** Robust token fetch: JSON or quoted string → clean bearer token */
function fetchLmToken_() {
  const r = UrlFetchApp.fetch(TOKEN_URL, { muteHttpExceptions: true, followRedirects: true });
  let t = (r.getContentText() || '').trim();
  try { const js = JSON.parse(t); t = (js.access_token || js.token || '').trim(); } catch(e) {}
  t = t.replace(/^"+|"+$/g, '').replace(/\r?\n/g, '').trim();
  Logger.log('LM token length: ' + t.length);
  return t;
}

/** Address → Zone using the LM component API. Returns "Zone X" or null. */
function lookupZone_(addr, token) {
  if (!addr || !token) return null;

  const headers = {
    'Authorization': 'Bearer ' + token,
    'Origin': 'https://www.lowermerion.org',
    'Content-Type': 'application/json;charset=UTF-8'
  };

  const body = {
    pageSize: 10,
    pageNumber: 1,
    sortOptions: [],
    searchText: addr,
    searchFields: ['Address'],
    searchOperator: 'OR',
    searchSeparator: ',',
    filterOptions: [],
    Data: { componentGuid: COMPONENT_GUID, listUniqueName: LIST_UNIQUE_NAME }
  };

  const resp = UrlFetchApp.fetch(SEARCH_URL, {
    method: 'post',
    headers: headers,
    payload: JSON.stringify(body),
    followRedirects: true,
    muteHttpExceptions: true
  });

  const txt = resp.getContentText();
  let data;
  try { data = JSON.parse(txt); } catch (e) { data = {}; }

  let rows = (data.items || data.Items || data.Data || []);
  if (rows && rows.Items) rows = rows.Items;

  for (const row of (rows || [])) {
    for (const k in row) {
      const v = row[k];
      if (typeof v === 'string') {
        const m = v.match(/zone\s*([1-4])/i);
        if (m) return 'Zone ' + m[1];
      }
    }
  }
  return null;
}

function componentZoneLookup_(addr, token) {
  if (!addr || !token) return null;
  const headers = {
    'Authorization': 'Bearer ' + token,
    'Origin': 'https://www.lowermerion.org',
    'Content-Type': 'application/json;charset=UTF-8'
  };
  const body = {
    pageSize: 10, pageNumber: 1, sortOptions: [],
    searchText: addr, searchFields: ['Address'],
    searchOperator: 'OR', searchSeparator: ',',
    filterOptions: [],
    Data: { componentGuid: COMPONENT_GUID, listUniqueName: LIST_UNIQUE_NAME }
  };
  const resp = UrlFetchApp.fetch(SEARCH_URL, {
    method: 'post', headers, payload: JSON.stringify(body),
    muteHttpExceptions: true, followRedirects: true
  });
  let data; try { data = JSON.parse(resp.getContentText()); } catch(e) { data = {}; }
  let rows = (data.items || data.Items || data.Data || []);
  if (rows && rows.Items) rows = rows.Items;
  for (const row of (rows || [])) {
    for (const k in row) {
      const v = row[k];
      if (typeof v === 'string') {
        const m = v.match(/zone\s*([1-4])/i);
        if (m) return 'Zone ' + m[1];
      }
    }
  }
  return null;
}

function streetOnly_(addr) {
  if (!addr) return '';
  let a = addr.split(',')[0];
  a = a.replace(/\b(apt|apartment|unit|ste|suite|#|fl|floor|bldg|building)\b.*$/i, '').trim();
  return a.replace(/\s{2,}/g, ' ');
}

function lmAddressSuggest_(q) {
  if (!q) return null;
  const headers = {
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://www.lowermerion.org/',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': 'Mozilla/5.0 (AppsScript)'
  };
  const r = UrlFetchApp.fetch(SUGGEST_URL + encodeURIComponent(q), {
    muteHttpExceptions: true, followRedirects: true, headers
  });
  const txt = r.getContentText();
  // Popup often returns simple JSON array of suggestions
  try {
    const arr = JSON.parse(txt);
    if (Array.isArray(arr) && arr.length) return String(arr[0]).trim();
  } catch(e) {
    // Some builds render <li>… suggestions
    const m = txt.match(/<li[^>]*>(.*?)<\/li>/i);
    if (m) return m[1].replace(/<[^>]+>/g, '').trim();
  }
  return null;
}

// Smart resolver WITHOUT Maps
function resolveZoneSmart_(addr) {
  const token = fetchLmToken_();
  if (!token) return null;

  // 1) Raw
  let z = componentZoneLookup_(addr, token);
  if (z) return z;

  // 2) Street-only
  const sOnly = streetOnly_(addr);
  if (sOnly && sOnly !== addr) {
    z = componentZoneLookup_(sOnly, token);
    if (z) return z;
  }

  // 3) Suggest (raw or street-only) -> component
  const sug = lmAddressSuggest_(addr) || lmAddressSuggest_(sOnly);
  if (sug) {
    z = componentZoneLookup_(sug, token) ||
        componentZoneLookup_(streetOnly_(sug), token);
    if (z) return z;
  }

  // No luck
  return null;
}

function resolveZoneStreetOnlyFirst_(addr) {
  const token = fetchLmToken_();
  if (!token) return null;

  const s = streetOnly_(addr);
  if (!s) return null;

  // 1) component on street-only (sometimes works immediately)
  let z = componentZoneLookup_(s, token);
  if (z) { Logger.log('Zone via component(street): ' + z + ' for "' + s + '"'); return z; }

  // 2) suggest(street-only) → component(suggest) → component(streetOnly(suggest))
  const sug = lmAddressSuggest_(s);
  if (sug) {
    z = componentZoneLookup_(sug, token);
    if (z) { Logger.log('Zone via suggest: ' + z + ' for "' + sug + '"'); return z; }
    const sugStreet = streetOnly_(sug);
    if (sugStreet && sugStreet !== sug) {
      z = componentZoneLookup_(sugStreet, token);
      if (z) { Logger.log('Zone via suggest(street): ' + z + ' for "' + sugStreet + '"'); return z; }
    }
  }

  // 3) last tries: uppercase street-only variants (site sometimes uppercases)
  const sUp = s.toUpperCase();
  if (sUp !== s) {
    z = componentZoneLookup_(sUp, token);
    if (z) { Logger.log('Zone via component(UPPER): ' + z + ' for "' + sUp + '"'); return z; }
    const sugUp = lmAddressSuggest_(sUp);
    if (sugUp) {
      z = componentZoneLookup_(sugUp, token) || componentZoneLookup_(streetOnly_(sugUp), token);
      if (z) { Logger.log('Zone via suggest(UPPER): ' + z + ' for "' + sugUp + '"'); return z; }
    }
  }

  Logger.log('Zone not found (street-only flow).');
  return null;
}

/** Main trigger: runs when a new Form response is appended to the Sheet. */
function onFormSubmit(e) {
  if (!e || !e.namedValues) {
    Logger.log('No event payload; is the trigger installed as "From spreadsheet → On form submit"?');
    return;
  }

  const nv = e.namedValues;
  const streetAddress = (nv[Q_ADDRESS] || [''])[0].trim();
  const phoneNumber   = (nv[Q_PHONE]   || [''])[0].trim();
  const consentRaw    = (nv[Q_CONSENT] || [''])[0].trim();
  const consentLc     = consentRaw.toLowerCase();
  const zoneRaw = (nv[Q_ZONE] || [''])[0].trim();     // "Zone 1"…"Zone 4" or ""
  let zone = null;

  // If user selected a zone, trust it:
  if (/^zone\s*[1-4]$/i.test(zoneRaw)) {
    zone = zoneRaw.replace(/\s+/g, ' ').replace(/z/i, 'Z'); // normalize "Zone X"
  } else {
    // (optional) fallback to your existing resolver while RTK is pending
    try {
      zone = resolveZoneStreetOnlyFirst_(streetAddress)    // or resolveZoneSmart_ / resolveZoneByPolygon_
    } catch (err) {
      Logger.log('Zone lookup error: ' + err);
    }
  }
  Logger.log('Resolved zone (form/fallback): ' + zone + ' for address: ' + streetAddress);

  const payload = {
    street_address: streetAddress,
    phone_number:   phoneNumber,
    consent:        consentRaw,
    zone:           zone // may be null; backend can backfill or prompt later
  };

  const res = UrlFetchApp.fetch(WEBHOOK_URL, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  Logger.log('POST ' + res.getResponseCode() + ' ' + res.getContentText());

  // ===== NEW: Parse webhook response and update Sheet with zone + collection_day =====
  try {
    const responseData = JSON.parse(res.getContentText());

    if (responseData.status === "ok") {
      const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
      const lastRow = sheet.getLastRow();

      // Find or create zone and collection_day columns
      const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];

      let zoneCol = headers.indexOf("zone") + 1;
      let collectionDayCol = headers.indexOf("collection_day") + 1;

      // Add column headers if they don't exist
      if (zoneCol === 0) {
        zoneCol = headers.length + 1;
        sheet.getRange(1, zoneCol).setValue("zone");
      }

      if (collectionDayCol === 0) {
        collectionDayCol = headers.length + (zoneCol === headers.length + 1 ? 2 : 1);
        sheet.getRange(1, collectionDayCol).setValue("collection_day");
      }

      // Write zone and collection_day to the latest row
      if (responseData.zone) {
        sheet.getRange(lastRow, zoneCol).setValue(responseData.zone);
        Logger.log('Updated row ' + lastRow + ' zone column with: ' + responseData.zone);
      }

      if (responseData.collection_day) {
        sheet.getRange(lastRow, collectionDayCol).setValue(responseData.collection_day);
        Logger.log('Updated row ' + lastRow + ' collection_day column with: ' + responseData.collection_day);
      }
    }
  } catch (error) {
    Logger.log('Error updating sheet with webhook response: ' + error.toString());
  }
}

/** One-click installer to ensure exactly one "On form submit" trigger is set. */
function installTrigger() {
  const ss = SpreadsheetApp.getActive();
  for (const t of ScriptApp.getProjectTriggers()) {
    // remove old duplicates
    if (t.getHandlerFunction && t.getHandlerFunction() === 'onFormSubmit') {
      ScriptApp.deleteTrigger(t);
    }
  }
  ScriptApp.newTrigger('onFormSubmit')
    .forSpreadsheet(ss)
    .onFormSubmit()
    .create();
  Logger.log('✅ Installed onFormSubmit trigger for this spreadsheet.');
}

/** Local test without submitting the real form. */
function testPost() {
  const fake = {
    namedValues: {
      [Q_ADDRESS]: ['230 Ardleigh Rd, Penn Valley, PA 19072'],
      [Q_PHONE]:   ['+1YOURMOBILE'],
      [Q_CONSENT]: ['I agree to receive WhatsApp reminders.']
    }
  };
  onFormSubmit(fake);
}
