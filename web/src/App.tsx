import { useEffect, useState, useMemo, useCallback } from "react";
import type { Listing, Filters, SortKey, SortDir } from "./types";

const SHEET_ID = "1y4cL7bWCwhZNKtLow0iukMXkhYok0vNdFo8U3ptre74";
const API_KEY = "AIzaSyAadRdZA09ux8AcAjQUktChZLjQK1QWK9M";
const SHEET_NAME = "Apartment Listings";
const API_BASE = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values`;

const ALL_SOURCES = ["renthop", "streeteasy", "apartments_com"];
const ALL_STATUSES = ["new", "liked", "contacted", "touring"];

const DEFAULT_FILTERS: Filters = {
  search: "",
  sources: [],
  beds: "",
  maxPrice: "",
  statuses: [],
  laundry: "any",
  brokerFee: "any",
};

// Map sheet column headers → Listing fields
const COL_MAP: Record<string, keyof Listing> = {
  "ID": "id",
  "Source": "source",
  "URL": "url",
  "Address": "address",
  "Neighborhood": "neighborhood",
  "Beds": "beds",
  "Baths": "baths",
  "Price": "price",
  "Broker Fee (months)": "broker_fee",
  "Broker Fee Source": "broker_fee_source",
  "Laundry": "laundry_label",
  "Building Amenities": "building_amenities",
  "Nearest Subway": "nearest_subway",
  "Has Flex Room": "has_flex_raw",
  "Has Photos": "has_photos_raw",
  "Move-in Date": "move_in_date",
  "Listed Date": "listed_date",
  "Status": "status",
  "Contact Name": "contact_name",
  "Contact Email": "contact_email",
  "Contact Phone": "contact_phone",
  "Pre-Tour Score": "pre_tour_score",
  "Post-Tour Score": "post_tour_score",
  "Notes": "notes",
};

function parseRow(headers: string[], row: string[]): Listing {
  const raw: Record<string, string> = {};
  headers.forEach((h, i) => { raw[h] = row[i] ?? ""; });

  const laundry = raw["Laundry"] || "";
  return {
    id: parseInt(raw["ID"]) || 0,
    source: raw["Source"] || "",
    url: raw["URL"] || "",
    address: raw["Address"] || "",
    neighborhood: raw["Neighborhood"] || "",
    beds: raw["Beds"] !== "" ? parseFloat(raw["Beds"]) : null,
    baths: raw["Baths"] !== "" ? parseFloat(raw["Baths"]) : null,
    price: raw["Price"] !== "" ? parseFloat(raw["Price"]) : null,
    broker_fee: raw["Broker Fee (months)"] !== "" ? parseFloat(raw["Broker Fee (months)"]) : null,
    broker_fee_source: raw["Broker Fee Source"] || "",
    laundry_in_unit: laundry === "In Unit",
    laundry_in_building: laundry === "In Unit" || laundry === "In Building",
    building_amenities: raw["Building Amenities"] || "",
    nearest_subway: raw["Nearest Subway"] || "",
    has_flex: raw["Has Flex Room"] === "Yes" ? true : raw["Has Flex Room"] === "No" ? false : null,
    has_photos: raw["Has Photos"] === "Yes" ? true : raw["Has Photos"] === "No" ? false : null,
    move_in_date: raw["Move-in Date"] || "",
    listed_date: raw["Listed Date"] || "",
    status: raw["Status"] || "new",
    contact_name: raw["Contact Name"] || "",
    contact_email: raw["Contact Email"] || "",
    contact_phone: raw["Contact Phone"] || "",
    pre_tour_score: raw["Pre-Tour Score"] !== "" ? parseFloat(raw["Pre-Tour Score"]) : null,
    post_tour_score: raw["Post-Tour Score"] !== "" ? parseFloat(raw["Post-Tour Score"]) : null,
    notes: raw["Notes"] || "",
  };
}

function laundryLabel(l: Listing) {
  if (l.laundry_in_unit) return <span className="laundry-unit">In Unit</span>;
  if (l.laundry_in_building) return <span className="laundry-bldg">In Building</span>;
  return <span className="laundry-none">None</span>;
}

function brokerFeeLabel(l: Listing) {
  if (l.broker_fee === 0) return <span className="fee-none">No Fee</span>;
  if (l.broker_fee !== null && l.broker_fee > 0)
    return <span className="fee-has">{l.broker_fee} mo</span>;
  return <span className="fee-unk">—</span>;
}

function scoreClass(s: number | null) {
  if (s === null) return "";
  if (s >= 70) return "score score-high";
  if (s >= 40) return "score score-mid";
  return "score score-low";
}

function fmt(n: number | null) {
  if (n === null) return "—";
  return "$" + n.toLocaleString();
}

type Col = { key: SortKey; label: string; width?: number };

const COLS: Col[] = [
  { key: "pre_tour_score", label: "Score", width: 58 },
  { key: "status", label: "Status", width: 86 },
  { key: "source", label: "Source", width: 90 },
  { key: "address", label: "Address" },
  { key: "neighborhood", label: "Neighborhood", width: 120 },
  { key: "beds", label: "Beds", width: 50 },
  { key: "baths", label: "Baths", width: 52 },
  { key: "price", label: "Price", width: 86 },
  { key: "broker_fee", label: "Broker Fee", width: 80 },
  { key: "laundry_in_unit", label: "Laundry", width: 90 },
  { key: "building_amenities", label: "Amenities", width: 160 },
  { key: "nearest_subway", label: "Subway", width: 140 },
  { key: "move_in_date", label: "Move-In", width: 90 },
  { key: "contact_phone", label: "Contact", width: 110 },
  { key: "notes", label: "Notes", width: 150 },
];

function applyFilters(listings: Listing[], f: Filters): Listing[] {
  return listings.filter(l => {
    if (l.status === "passed") return false;
    if (f.search) {
      const q = f.search.toLowerCase();
      if (
        !l.address.toLowerCase().includes(q) &&
        !l.neighborhood.toLowerCase().includes(q) &&
        !(l.notes || "").toLowerCase().includes(q) &&
        !l.contact_name.toLowerCase().includes(q)
      ) return false;
    }
    if (f.sources.length && !f.sources.includes(l.source)) return false;
    if (f.beds && String(l.beds) !== f.beds) return false;
    if (f.maxPrice && l.price !== null && l.price > Number(f.maxPrice)) return false;
    if (f.statuses.length && !f.statuses.includes(l.status)) return false;
    if (f.laundry === "unit" && !l.laundry_in_unit) return false;
    if (f.laundry === "building" && !l.laundry_in_unit && !l.laundry_in_building) return false;
    if (f.brokerFee === "no-fee" && l.broker_fee !== 0) return false;
    if (f.brokerFee === "has-fee" && l.broker_fee === 0) return false;
    return true;
  });
}

function applySort(listings: Listing[], key: SortKey, dir: SortDir): Listing[] {
  return [...listings].sort((a, b) => {
    const av = a[key] ?? (dir === "asc" ? Infinity : -Infinity);
    const bv = b[key] ?? (dir === "asc" ? Infinity : -Infinity);
    if (av < bv) return dir === "asc" ? -1 : 1;
    if (av > bv) return dir === "asc" ? 1 : -1;
    return 0;
  });
}

function MultiCheck({ label, options, value, onChange }: {
  label: string; options: string[]; value: string[]; onChange: (v: string[]) => void;
}) {
  const toggle = (opt: string) =>
    onChange(value.includes(opt) ? value.filter(x => x !== opt) : [...value, opt]);
  return (
    <div className="filter-group">
      <span className="filter-label">{label}</span>
      <div className="multi-check">
        {options.map(opt => (
          <label key={opt}>
            <input type="checkbox" checked={value.includes(opt)} onChange={() => toggle(opt)} />
            {opt === "apartments_com" ? "Apts.com" : opt}
          </label>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [allListings, setAllListings] = useState<Listing[]>([]);
  const [rowIndex, setRowIndex] = useState<Map<number, number>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<SortKey>("pre_tour_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [hiding, setHiding] = useState<Set<number>>(new Set());

  const fetchSheet = useCallback(() => {
    const url = `${API_BASE}/${encodeURIComponent(SHEET_NAME)}?key=${API_KEY}`;
    setLoading(true);
    fetch(url)
      .then(r => r.json())
      .then(data => {
        const rows: string[][] = data.values || [];
        if (rows.length < 2) { setAllListings([]); setLoading(false); return; }
        const headers = rows[0];
        const idxMap = new Map<number, number>();
        const listings = rows.slice(1).map((row, i) => {
          const l = parseRow(headers, row);
          idxMap.set(l.id, i + 2); // 1-based sheet row (row 1 = header)
          return l;
        });
        setAllListings(listings);
        setRowIndex(idxMap);
        setLoading(false);
      })
      .catch(() => { setError("Could not load listings. Make sure the sheet is shared publicly."); setLoading(false); });
  }, []);

  useEffect(() => { fetchSheet(); }, [fetchSheet]);

  const setFilter = useCallback(<K extends keyof Filters>(key: K, val: Filters[K]) => {
    setFilters(prev => ({ ...prev, [key]: val }));
  }, []);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  };

  const hideListing = async (l: Listing) => {
    const sheetRow = rowIndex.get(l.id);
    if (!sheetRow) return;

    // Status is column R (18th column, index 17, letter R)
    const range = `${encodeURIComponent(SHEET_NAME)}!R${sheetRow}`;
    setHiding(prev => new Set(prev).add(l.id));

    try {
      await fetch(
        `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/${range}?valueInputOption=RAW&key=${API_KEY}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values: [["passed"]] }),
        }
      );
      setAllListings(prev => prev.map(x => x.id === l.id ? { ...x, status: "passed" } : x));
    } catch {
      alert("Could not hide listing. The sheet may need to be editable.");
    } finally {
      setHiding(prev => { const s = new Set(prev); s.delete(l.id); return s; });
    }
  };

  const visible = useMemo(
    () => applySort(applyFilters(allListings, filters), sortKey, sortDir),
    [allListings, filters, sortKey, sortDir]
  );

  const arrow = (key: SortKey) =>
    <span className="sort-arrow">{sortKey === key ? (sortDir === "asc" ? "▲" : "▼") : "⇅"}</span>;

  if (loading) return <div className="empty">Loading…</div>;
  if (error) return <div className="empty" style={{ color: "#c00" }}>{error}</div>;

  return (
    <div className="app">
      <div className="topbar">
        <h1>🏙 Apt Search</h1>
        <input
          type="text"
          placeholder="Search address, neighborhood, notes…"
          value={filters.search}
          onChange={e => setFilter("search", e.target.value)}
          style={{ flex: 1, maxWidth: 340, borderRadius: 8, border: "none", padding: "6px 12px", fontSize: 13, outline: "none" }}
        />
        <span className="count">{visible.length} / {allListings.filter(l => l.status !== "passed").length} listings</span>
        <button className="btn-reset" style={{ marginLeft: 8 }} onClick={fetchSheet}>↻ Refresh</button>
      </div>

      <div className="filters">
        <MultiCheck label="Source" options={ALL_SOURCES} value={filters.sources} onChange={v => setFilter("sources", v)} />
        <div className="filter-sep" />
        <div className="filter-group">
          <span className="filter-label">Beds</span>
          <select value={filters.beds} onChange={e => setFilter("beds", e.target.value)}>
            <option value="">Any</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
          </select>
        </div>
        <div className="filter-group">
          <span className="filter-label">Max Price</span>
          <input type="number" placeholder="e.g. 8000" value={filters.maxPrice}
            onChange={e => setFilter("maxPrice", e.target.value)} style={{ width: 110 }} />
        </div>
        <div className="filter-sep" />
        <MultiCheck label="Status" options={ALL_STATUSES} value={filters.statuses} onChange={v => setFilter("statuses", v)} />
        <div className="filter-sep" />
        <div className="filter-group">
          <span className="filter-label">Laundry</span>
          <select value={filters.laundry} onChange={e => setFilter("laundry", e.target.value as Filters["laundry"])}>
            <option value="any">Any</option>
            <option value="unit">In Unit</option>
            <option value="building">In Unit or Building</option>
          </select>
        </div>
        <div className="filter-group">
          <span className="filter-label">Broker Fee</span>
          <select value={filters.brokerFee} onChange={e => setFilter("brokerFee", e.target.value as Filters["brokerFee"])}>
            <option value="any">Any</option>
            <option value="no-fee">No Fee Only</option>
            <option value="has-fee">Has Fee</option>
          </select>
        </div>
        <button className="btn-reset" onClick={() => setFilters(DEFAULT_FILTERS)}>Reset</button>
      </div>

      <div className="table-wrap">
        {visible.length === 0 ? (
          <div className="empty">No listings match your filters.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 60 }}></th>
                {COLS.map(c => (
                  <th key={c.key} className={sortKey === c.key ? "sorted" : ""}
                    style={c.width ? { width: c.width, minWidth: c.width } : {}}
                    onClick={() => handleSort(c.key)}>
                    {c.label}{arrow(c.key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map(l => (
                <tr key={l.id}>
                  <td>
                    <button
                      className="btn-hide"
                      title="Hide listing"
                      disabled={hiding.has(l.id)}
                      onClick={() => hideListing(l)}
                    >✕</button>
                  </td>
                  <td>
                    {l.pre_tour_score !== null
                      ? <span className={scoreClass(l.pre_tour_score)}>{Number(l.pre_tour_score).toFixed(0)}</span>
                      : <span className="fee-unk">—</span>}
                  </td>
                  <td><span className={`badge badge-status-${l.status}`}>{l.status}</span></td>
                  <td><span className={`badge badge-source-${l.source}`}>{l.source === "apartments_com" ? "Apts.com" : l.source}</span></td>
                  <td>{l.url ? <a className="addr-link" href={l.url} target="_blank" rel="noreferrer">{l.address}</a> : l.address}</td>
                  <td>{l.neighborhood || "—"}</td>
                  <td>{l.beds ?? "—"}</td>
                  <td>{l.baths ?? "—"}</td>
                  <td><span className="price">{fmt(l.price)}</span></td>
                  <td>{brokerFeeLabel(l)}</td>
                  <td>{laundryLabel(l)}</td>
                  <td><span className="amenities">{l.building_amenities || "—"}</span></td>
                  <td><span className="subway">{l.nearest_subway || "—"}</span></td>
                  <td>{l.move_in_date || "—"}</td>
                  <td>
                    <div className="contact">
                      {l.contact_name && <div>{l.contact_name}</div>}
                      {l.contact_phone && <div>{l.contact_phone}</div>}
                      {l.contact_email && <div>{l.contact_email}</div>}
                      {!l.contact_name && !l.contact_phone && !l.contact_email && "—"}
                    </div>
                  </td>
                  <td><span className="notes-cell">{l.notes || "—"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
