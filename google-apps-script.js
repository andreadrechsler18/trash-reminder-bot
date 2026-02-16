/**
 * Google Apps Script for Trash Reminder Bot
 *
 * Setup:
 * 1. Open your Google Form
 * 2. Click the 3 dots menu → "Script editor"
 * 3. Replace all code with this script
 * 4. Update FLASK_WEBHOOK_URL with your Render URL
 * 5. Click "Triggers" (clock icon) → Add Trigger
 *    - Function: onFormSubmit
 *    - Event source: From form
 *    - Event type: On form submit
 *    - Save
 */

// Replace with your Render URL
const FLASK_WEBHOOK_URL = "https://trash-reminder-bot-1.onrender.com/";

function onFormSubmit(e) {
  try {
    // Get the form response
    const itemResponses = e.response.getItemResponses();

    // Extract responses (adjust based on your form field order)
    let phone = "";
    let address = "";
    let consent = "";

    for (const itemResponse of itemResponses) {
      const question = itemResponse.getItem().getTitle().toLowerCase();
      const answer = itemResponse.getResponse();

      if (question.includes("phone") || question.includes("number")) {
        phone = answer;
      } else if (question.includes("address") || question.includes("street")) {
        address = answer;
      } else if (question.includes("consent") || question.includes("agree")) {
        consent = answer;
      }
    }

    // Build payload
    const payload = {
      phone_number: phone,
      street_address: address,
      consent: consent
    };

    // Send to Flask webhook
    const options = {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };

    const response = UrlFetchApp.fetch(FLASK_WEBHOOK_URL, options);
    const responseData = JSON.parse(response.getContentText());

    Logger.log("Flask response: " + JSON.stringify(responseData));

    // Update the Google Sheet with zone and collection_day
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
      }

      if (responseData.collection_day) {
        sheet.getRange(lastRow, collectionDayCol).setValue(responseData.collection_day);
      }

      Logger.log("Updated row " + lastRow + " with zone=" + responseData.zone +
                 ", collection_day=" + responseData.collection_day);
    }

  } catch (error) {
    Logger.log("Error in onFormSubmit: " + error.toString());

    // Send error notification email (optional)
    // MailApp.sendEmail("your@email.com", "Form Submit Error", error.toString());
  }
}

/**
 * Test function - run this manually to test the script
 */
function testWebhook() {
  const testPayload = {
    phone_number: "+13029812102",
    street_address: "229 Ardleigh Rd",
    consent: "I agree"
  };

  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(testPayload),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(FLASK_WEBHOOK_URL, options);
  Logger.log("Status: " + response.getResponseCode());
  Logger.log("Response: " + response.getContentText());
}
