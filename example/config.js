/*
 * Example per-kiosk config.
 *
 * kioskage sticks append "?key=<key>" to the content URL (the "kiosk key" set
 * in the setup portal), so one page can serve many displays with different
 * content. Here we switch on ?key= in the browser to demonstrate the idea; a
 * real deployment would fetch the config for <key> from your server instead.
 */
(function () {
  "use strict";
  var key = new URLSearchParams(location.search).get("key") || "default";

  var PROFILES = {
    default: { title: "Kioskage Demo", country: "US" },
    us:      { title: "United States",  country: "US" },
    uk:      { title: "United Kingdom", country: "GB" },
    de:      { title: "Deutschland",    country: "DE" },
    clock:   { title: "Clock",          country: "" }   // date + time only
  };

  var cfg = PROFILES[key] || PROFILES.default;
  cfg.key = key;
  cfg.showHolidays = cfg.country !== "";

  // Upcoming public holidays come from Nager.Date — a free, no-key,
  // CORS-enabled public-holidays API, so it can be fetched straight from the
  // browser. Swap for any source that sends Access-Control-Allow-Origin.
  // (Note: Google's public holiday .ics feeds do NOT send CORS headers, so a
  // browser can't fetch them cross-origin — hence a CORS-friendly JSON API.)
  cfg.holidaysUrl = cfg.country
    ? "https://date.nager.at/api/v3/NextPublicHolidays/" + cfg.country
    : "";

  window.KIOSK_CONFIG = cfg;
})();
