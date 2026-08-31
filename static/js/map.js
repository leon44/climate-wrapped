const mapDiv = document.getElementById("map");
const stadiaKey = mapDiv.dataset.stadiaKey || "";

// "2025" preview toggle: lets you demo the wrapped story on a real deploy
// before this year's summer-completion gate has opened, by asking the
// server to pretend today is 2025-09-15 (see the preview param handling in
// app/routes.py) instead of requiring a permanent FAKE_TODAY env var.
const preview2025Checkbox = document.getElementById("preview-2025-checkbox");
const PREVIEW_STORAGE_KEY = "climateWrapped.preview2025";
try {
  preview2025Checkbox.checked = localStorage.getItem(PREVIEW_STORAGE_KEY) === "1";
} catch (e) {
  // localStorage unavailable (e.g. private browsing) -- default unchecked.
}
preview2025Checkbox.addEventListener("change", () => {
  try {
    localStorage.setItem(PREVIEW_STORAGE_KEY, preview2025Checkbox.checked ? "1" : "0");
  } catch (e) {
    // ignore -- preview toggle just won't persist across page loads.
  }
});

function wrappedUrl(stationId) {
  return preview2025Checkbox.checked
    ? `/wrapped/${stationId}?preview=2025`
    : `/wrapped/${stationId}`;
}

const map = L.map("map").setView([53.5511, 9.9937], 5); // Europe, centered on Hamburg

L.tileLayer(
  `https://tiles.stadiamaps.com/tiles/stamen_watercolor/{z}/{x}/{y}.jpg${stadiaKey ? "?api_key=" + stadiaKey : ""}`,
  {
    minZoom: 1,
    maxZoom: 16,
    attribution:
      'Map tiles by <a href="https://stamen.com">Stamen Design</a>, ' +
      'hosted by <a href="https://stadiamaps.com">Stadia Maps</a>, ' +
      'under <a href="https://creativecommons.org/licenses/by/4.0">CC BY 4.0</a>. ' +
      'Data by <a href="https://openstreetmap.org">OpenStreetMap</a>, under ODbL.',
  }
).addTo(map);

let markers = [];

function clearMarkers() {
  markers.forEach((m) => map.removeLayer(m));
  markers = [];
}

function thermometerIcon(variant) {
  return L.divIcon({
    className: `station-thermo-icon station-thermo-icon--${variant}`,
    html: "🌡️",
    iconSize: [52, 52],
    iconAnchor: [26, 48],
    popupAnchor: [0, -44],
  });
}

const overviewIcon = thermometerIcon("overview");
const highlightIcon = thermometerIcon("highlight");

// Overview layer: every station in the network, clustered into count
// bubbles when zoomed out and split into individual pins once zoomed in
// far enough to tell them apart. Kept separate from the "nearest station"
// shortlist markers above/below, which come and go per search/click.
const OVERVIEW_PIN_ZOOM = 9;

const stationOverview = L.markerClusterGroup({
  maxClusterRadius: 55,
  disableClusteringAtZoom: OVERVIEW_PIN_ZOOM,
  spiderfyOnMaxZoom: false,
});
map.addLayer(stationOverview);

async function fetchAllStations() {
  const resp = await fetch("/api/stations/all");
  return resp.ok ? resp.json() : [];
}

async function loadStationOverview() {
  const all = await fetchAllStations();
  const pins = all.map((s) => {
    const marker = L.marker([s.lat, s.lon], { icon: overviewIcon });
    marker.bindPopup(
      `<strong>${s.name}${s.state ? ", " + s.state : ""}</strong><br>` +
      `${s.record_years} yrs (${s.first_year}–${s.last_year})<br>` +
      `<a class="wrapped-btn" href="${wrappedUrl(s.id)}">` +
      '<svg class="wrapped-btn__icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 2l11 6-11 6V2z"/></svg>' +
      "View wrapped</a>"
    );
    return marker;
  });
  stationOverview.addLayers(pins);
}

loadStationOverview();

function renderShortlist(stationList) {
  const shortlist = document.getElementById("shortlist");
  const rows = document.getElementById("shortlist-rows");
  rows.innerHTML = "";
  clearMarkers();

  if (!stationList.length) {
    shortlist.style.display = "none";
    return;
  }

  stationList.forEach((s) => {
    const row = document.createElement("a");
    row.className = "station-row";
    row.href = wrappedUrl(s.id);
    row.innerHTML = `
      <span class="station-row__name">${s.name}${s.state ? ", " + s.state : ""}</span>
      <span class="station-row__meta">${s.distance_km ? s.distance_km + " km · " : ""}${s.record_years} yrs (${s.first_year}–${s.last_year})</span>
    `;
    rows.appendChild(row);

    const marker = L.marker([s.lat, s.lon], { icon: highlightIcon }).addTo(map).bindPopup(s.name);
    marker.on("click", () => { window.location.href = wrappedUrl(s.id); });
    markers.push(marker);
  });

  shortlist.style.display = "block";
  shortlist.scrollIntoView({ behavior: "smooth" });
}

async function fetchNearest(lat, lon) {
  const resp = await fetch(`/api/stations/nearest?lat=${lat}&lon=${lon}`);
  return resp.ok ? resp.json() : [];
}

async function fetchSearch(q) {
  const resp = await fetch(`/api/stations/search?q=${encodeURIComponent(q)}`);
  return resp.ok ? resp.json() : [];
}

map.on("click", async (e) => {
  const { lat, lng } = e.latlng;
  map.setView([lat, lng], Math.max(map.getZoom(), 7));
  const nearest = await fetchNearest(lat, lng);
  renderShortlist(nearest);
});

document.getElementById("locate-btn").addEventListener("click", () => {
  if (!navigator.geolocation) {
    alert("Geolocation isn't available in this browser.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const { latitude, longitude } = pos.coords;
      map.setView([latitude, longitude], 9);
      const nearest = await fetchNearest(latitude, longitude);
      renderShortlist(nearest);
    },
    () => alert("Couldn't get your location. Try searching instead.")
  );
});

const searchInput = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");
let searchTimer = null;

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (q.length < 2) {
    searchResults.classList.remove("open");
    return;
  }
  searchTimer = setTimeout(async () => {
    const results = await fetchSearch(q);
    searchResults.innerHTML = "";
    results.forEach((s) => {
      const row = document.createElement("a");
      row.className = "station-row";
      row.href = wrappedUrl(s.id);
      row.innerHTML = `
        <span class="station-row__name">${s.name}${s.state ? ", " + s.state : ""}</span>
        <span class="station-row__meta">${s.record_years} yrs (${s.first_year}–${s.last_year})</span>
      `;
      searchResults.appendChild(row);
    });
    searchResults.classList.toggle("open", results.length > 0);
  }, 250);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-box")) {
    searchResults.classList.remove("open");
  }
});
