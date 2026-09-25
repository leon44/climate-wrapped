const mapDiv = document.getElementById("map");
const stadiaKey = mapDiv.dataset.stadiaKey || "";

function wrappedUrl(stationId) {
  return `/climate/wrapped/${stationId}`;
}

// Station lookups (id -> station object) so the delegated click handler
// below can find the name/record_years for a clicked link without having
// to smuggle them through HTML attributes.
const stationCache = new Map();

function cacheStations(list) {
  list.forEach((s) => stationCache.set(s.id, s));
  return list;
}

const loadingOverlay = document.getElementById("wrapped-loading");
const loadingMessage = document.getElementById("loading-message");

// /wrapped/<id> does real work server-side (fetching + crunching a
// station's full daily record) before it can render anything, so a plain
// click-to-navigate would leave the browser looking unresponsive for a
// few seconds. Show a loading overlay first, then navigate -- the browser
// keeps the current page (overlay included) on screen until the new page
// is ready to paint, so the spinner covers the whole wait.
function goToWrapped(station) {
  loadingMessage.textContent =
    `Crunching, loading and wrapping ${station.record_years} years of data from ${station.name}…`;
  loadingOverlay.hidden = false;
  window.location.href = wrappedUrl(station.id);
}

document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-wrapped-link]");
  if (!link) return;
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const station = stationCache.get(link.dataset.stationId);
  if (!station) return; // unknown station -- let the normal href navigate
  e.preventDefault();
  goToWrapped(station);
});

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

// No barometer emoji exists in Unicode, so this is a small hand-drawn dial
// (cream face, dark rim/ticks, red needle) in the same inline-SVG style as
// the "View wrapped" arrow icon above.
const BAROMETER_SVG = `
  <svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <circle cx="24" cy="24" r="20" fill="#f5efe0" stroke="#3a2a1a" stroke-width="3"/>
    <g stroke="#3a2a1a" stroke-width="2" stroke-linecap="round">
      <line x1="24" y1="6" x2="24" y2="10"/>
      <line x1="42" y1="24" x2="38" y2="24"/>
      <line x1="6" y1="24" x2="10" y2="24"/>
      <line x1="24" y1="42" x2="24" y2="38"/>
    </g>
    <line x1="24" y1="24" x2="32" y2="15" stroke="#c81e3a" stroke-width="3" stroke-linecap="round"/>
    <circle cx="24" cy="24" r="3" fill="#3a2a1a"/>
  </svg>
`;

function barometerIcon(variant) {
  return L.divIcon({
    className: `station-barometer-icon station-barometer-icon--${variant}`,
    html: BAROMETER_SVG,
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -26],
  });
}

const overviewIcon = barometerIcon("overview");
const highlightIcon = barometerIcon("highlight");

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
  const resp = await fetch("/climate/api/stations/all");
  return resp.ok ? cacheStations(await resp.json()) : [];
}

async function loadStationOverview() {
  const all = await fetchAllStations();
  const pins = all.map((s) => {
    const marker = L.marker([s.lat, s.lon], { icon: overviewIcon });
    marker.bindPopup(
      `<strong>${s.name}${s.state ? ", " + s.state : ""}</strong><br>` +
      `${s.record_years} yrs (${s.first_year}–${s.last_year})<br>` +
      `<a class="wrapped-btn" data-wrapped-link data-station-id="${s.id}" href="${wrappedUrl(s.id)}">` +
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
    row.dataset.wrappedLink = "";
    row.dataset.stationId = s.id;
    row.innerHTML = `
      <span class="station-row__name">${s.name}${s.state ? ", " + s.state : ""}</span>
      <span class="station-row__meta">${s.distance_km ? s.distance_km + " km · " : ""}${s.record_years} yrs (${s.first_year}–${s.last_year})</span>
    `;
    rows.appendChild(row);

    const marker = L.marker([s.lat, s.lon], { icon: highlightIcon }).addTo(map).bindPopup(s.name);
    marker.on("click", () => goToWrapped(s));
    markers.push(marker);
  });

  shortlist.style.display = "block";
  shortlist.scrollIntoView({ behavior: "smooth" });
}

async function fetchNearest(lat, lon) {
  const resp = await fetch(`/climate/api/stations/nearest?lat=${lat}&lon=${lon}`);
  return resp.ok ? cacheStations(await resp.json()) : [];
}

async function fetchSearch(q) {
  const resp = await fetch(`/climate/api/stations/search?q=${encodeURIComponent(q)}`);
  return resp.ok ? cacheStations(await resp.json()) : [];
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
      row.dataset.wrappedLink = "";
      row.dataset.stationId = s.id;
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
