const mapDiv = document.getElementById("map");
const stadiaKey = mapDiv.dataset.stadiaKey || "";

const map = L.map("map").setView([39.8, -98.6], 4); // continental US default

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
    row.href = `/wrapped/${s.id}`;
    row.innerHTML = `
      <span class="station-row__name">${s.name}${s.state ? ", " + s.state : ""}</span>
      <span class="station-row__meta">${s.distance_km ? s.distance_km + " km · " : ""}${s.record_years} yrs (${s.first_year}–${s.last_year})</span>
    `;
    rows.appendChild(row);

    const marker = L.marker([s.lat, s.lon]).addTo(map).bindPopup(s.name);
    marker.on("click", () => { window.location.href = `/wrapped/${s.id}`; });
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
      row.href = `/wrapped/${s.id}`;
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
