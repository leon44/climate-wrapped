// Shared °C/°F preference, used on both the landing page and the wrapped
// story. The preference lives in a cookie (not just localStorage) because
// wrapped.html bakes temperatures into server-rendered prose -- see
// app/narrative.py -- so the server needs to read it too.
(function () {
  const COOKIE_NAME = "unit";

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function setCookie(name, value) {
    document.cookie = `${name}=${value}; path=/; max-age=31536000; samesite=lax`;
  }

  function applyToggleUI(unit) {
    document.querySelectorAll(".unit-toggle [data-unit]").forEach((btn) => {
      btn.classList.toggle("unit-toggle__option--active", btn.dataset.unit === unit);
    });
  }

  // `reload` re-fetches the current page so the server can re-render any
  // baked-in temperatures in the new unit.
  function setUnit(unit, reload) {
    setCookie(COOKIE_NAME, unit);
    applyToggleUI(unit);
    if (reload) window.location.reload();
  }

  function wireToggles(reloadOnChange) {
    document.querySelectorAll(".unit-toggle [data-unit]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.unit === getCookie(COOKIE_NAME)) return;
        setUnit(btn.dataset.unit, reloadOnChange);
      });
    });
  }

  // First-ever visit: no preference stored yet. Default to °F for US
  // visitors, °C otherwise, based on the visitor's own IP (this call is
  // made directly from the browser, so the geolocation service sees the
  // visitor's real IP, not the server's).
  function detectDefaultUnit(reloadOnDetect) {
    const existing = getCookie(COOKIE_NAME);
    if (existing) {
      applyToggleUI(existing);
      return;
    }
    applyToggleUI("C");
    fetch("https://ipapi.co/country/", { signal: AbortSignal.timeout(2500) })
      .then((r) => r.text())
      .then((country) => {
        if (getCookie(COOKIE_NAME)) return; // user already toggled while we waited
        setUnit(country.trim() === "US" ? "F" : "C", reloadOnDetect);
      })
      .catch(() => {});
  }

  window.Units = { setUnit, wireToggles, detectDefaultUnit, getCookie };
})();
