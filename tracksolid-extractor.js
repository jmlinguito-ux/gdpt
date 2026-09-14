/* =======================================================================
   TrackSolid Pro  ->  single merged Excel (.xlsx) extractor
   -----------------------------------------------------------------------
   HOW TO USE
   1. Log in to https://www.tracksolidpro.com  (any report page is fine).
   2. Open DevTools:  press F12  ->  click the "Console" tab.
   3. Paste this ENTIRE file into the console and press Enter.
   4. Enter the start and end dates when prompted (format: YYYY-MM-DD).
   5. A file "TrackSolid_Report_<start>_to_<end>.xlsx" downloads automatically.

   It pulls EVERY device in the account and merges them into one workbook:
     - "Mileage Totals"  : one row per device (total km over the range)
     - "Daily Activity"  : one row per device per day (trips, km, time, speed)
     - "Trips Detail"    : one row per individual trip (times, km, speed, GPS)
     - "Overspeed"       : one row per speeding event (speed, time, address, GPS)
     - "Parking"         : one row per stop with engine OFF (duration, address)
     - "Idling"          : one row per stop with engine ON  (duration, address)
     - "Ignition"        : one row per ACC on/off event (duration)
   (No "Fuel" sheet: these devices have no fuel sensor - the API returns 0 rows.)

   No password or token to copy - it reuses your active login session.
   The session token expires after a while; if you get a login/401 error,
   just refresh the page, log in again, and re-run.
   ======================================================================= */
(async function () {
  "use strict";
  const log = (...a) => console.log("%c[TrackSolid]", "color:#2b6cff;font-weight:bold", ...a);
  const err = (...a) => console.error("%c[TrackSolid]", "color:#e02;font-weight:bold", ...a);

  try {
    // ---- 0. session ----------------------------------------------------
    const token = localStorage.getItem("token");
    if (!token) { alert("Not logged in (no token found). Log in to TrackSolid Pro first."); return; }
    const userId = (JSON.parse(localStorage.getItem("userInfo") || "{}").id) || null;
    if (!userId) { alert("Could not read your user id. Open a Report page, then re-run."); return; }
    const H = { "Content-Type": "application/json", "Authorization": token };
    const post = async (url, body) => {
      const r = await fetch(url, { method: "POST", headers: H, body: JSON.stringify(body), credentials: "include" });
      if (r.status === 401) throw new Error("Session expired (401). Refresh the page, log in again, and re-run.");
      return r.json();
    };

    // ---- 1. date range -------------------------------------------------
    const today = new Date().toISOString().slice(0, 10);
    const startDay = prompt("START date (YYYY-MM-DD):", "2026-09-01");
    if (!startDay) { log("cancelled"); return; }
    const endDay = prompt("END date (YYYY-MM-DD):", today);
    if (!endDay) { log("cancelled"); return; }
    const START = startDay.trim() + " 00:00:00";
    const END = endDay.trim() + " 23:59:59";
    log("Range:", START, "->", END);

    // ---- 2. load SheetJS (for .xlsx) ----------------------------------
    if (!window.XLSX) {
      log("Loading Excel writer...");
      await new Promise((res, rej) => {
        const s = document.createElement("script");
        s.src = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js";
        s.onload = res; s.onerror = () => rej(new Error("Could not load the Excel library (CDN blocked)."));
        document.head.appendChild(s);
      });
    }

    // ---- 3. all devices in the account --------------------------------
    log("Fetching device list...");
    const dev = await post("/v3/new/newDevice/getReportDevice",
      { userId, pageNo: 1, pageSize: 1000, containSubordinate: 1 });
    const devices = (dev.data || []).map(d => ({ imei: String(d.id), name: d.name || "", mcType: d.mcType || "" }));
    if (!devices.length) { alert("No devices found for this account."); return; }
    const imeis = devices.map(d => d.imei).join(",");
    log("Devices:", devices.length);

    // ---- 4. mileage totals (all devices, paged) -----------------------
    log("Fetching mileage totals...");
    const mileageRows = [];
    for (let pageNo = 1; pageNo <= 50; pageNo++) {
      const j = await post("/v3/new/newReportRun/getMileageList",
        { imeis, startTime: START, endTime: END, userId, containSubordinate: 1, filterMileageFlag: 1, pageNo, pageSize: 500 });
      const list = (j.data && j.data.reportRunMileageVOList) || [];
      list.forEach(v => mileageRows.push({
        "Device Name": v.deviceName, "IMEI": v.imei, "SIM": v.sim, "Model": v.mcType,
        "Total Distance (km)": num(v.distance), "Start Time": v.startTime, "End Time": v.endTime
      }));
      if (list.length < 500) break;
    }

    // ---- 5. trips + daily activity (per device) -----------------------
    const dailyRows = [], tripRows = [];
    for (let i = 0; i < devices.length; i++) {
      const d = devices[i];
      log(`Trips ${i + 1}/${devices.length}: ${d.name}`);
      let j;
      try {
        j = await post("/v3/new/newReportRun/getRouteList",
          { imeis: d.imei, startTime: START, endTime: END, type: "segment", filterMileageFlag: "0", endIndex: 100000, oilWear: 0, userId, containSubordinate: 1, associateOilSensorData: 0 });
      } catch (e) { err("skip", d.name, e.message); continue; }
      const dayList = (j.result && j.result.dayList) || [];
      dayList.forEach(el => (el.tripsData || []).forEach(td => {
        const t = td.inTotal || {};
        dailyRows.push({
          "Device Name": d.name, "IMEI": d.imei, "Date": td.Searchdate, "Trips": td.tripNum,
          "Distance (km)": num(t.totalDis), "Driving Time": t.totalTime,
          "Avg Speed (km/h)": num(t.totalAvgSpeed), "Max Speed (km/h)": num(t.totalMaxSpeed), "Fuel (L)": num(t.totalFuel)
        });
        (td.dayData || []).forEach(s => tripRows.push({
          "Device Name": d.name, "IMEI": d.imei, "Date": td.Searchdate,
          "Start Time": s.startTime, "End Time": s.endTime, "Travel Time": s.travelTime,
          "Distance (km)": num(s.totalMileage), "Avg Speed (km/h)": num(s.averageSpeed), "Max Speed (km/h)": num(s.maxSpeed),
          "Start Lat": s.startLat, "Start Lng": s.startLng, "End Lat": s.endLat, "End Lng": s.endLng,
          "Start Odometer (km)": num(s.startMileage), "End Odometer (km)": num(s.endMileage), "Fuel (L)": num(s.fuel)
        }));
      }));
    }

    // helper: page through a "list" endpoint that takes all imeis at once
    const pagedAll = async (url, extra, getList, getTotal) => {
      const rows = [];
      for (let pageNo = 1; pageNo <= 200; pageNo++) {
        const j = await post(url, Object.assign({ imeis, startTime: START, endTime: END, userId, pageNo, pageSize: 500, mapType: "osm" }, extra));
        const list = getList(j) || [];
        list.forEach(v => rows.push(v));
        const total = parseInt(getTotal(j), 10);
        if (list.length < 500) break;
        if (!isNaN(total) && rows.length >= total) break;
      }
      return rows;
    };
    const dName = it => it.deviceName || nameByImei[it.imei] || "";

    // ---- 6. overspeed -------------------------------------------------
    log("Fetching overspeed...");
    const overspeedRows = (await pagedAll("/v3/new/newReportRun/getOverSpeedList",
      { startRow: 1 }, j => j.data && j.data.result, j => j.data && j.data.dataTotalRows)).map(v => ({
        "Device Name": dName(v), "IMEI": v.imei, "SIM": v.sim, "Model": v.mcType,
        "Alert Type": v.statusName, "Speed (km/h)": num(v.speed),
        "Start Time": v.startTime, "End Time": v.endTime, "Duration (s)": num(v.duration),
        "Start Address": v.startAddr, "End Address": v.endAddr,
        "Start Lat": v.startLat, "Start Lng": v.startLng, "End Lat": v.endLat, "End Lng": v.endLng
      }));

    // ---- 7. parking (acc off) & idling (acc on) -----------------------
    const stopCols = v => ({
      "Device Name": dName(v), "IMEI": v.imei, "SIM": v.sim, "Model": v.mcType,
      "Start Time": v.startTime, "End Time": v.endTime, "Duration": v.durSecond, "Duration (s)": num(v.stopSecond),
      "Latitude": num(v.lat), "Longitude": num(v.lng), "Address": v.addr
    });
    log("Fetching parking...");
    const parkingRows = (await pagedAll("/v3/new/newReportRun/getCarStopList",
      { acc: "off", type: 0, startRow: 1 }, j => j.result, j => j.dataTotalRows)).map(stopCols);
    log("Fetching idling...");
    const idlingRows = (await pagedAll("/v3/new/newReportRun/getCarStopList",
      { acc: "on", type: 1, dataType: "realTime", startRow: 1 }, j => j.result, j => j.dataTotalRows)).map(stopCols);

    // ---- 8. ignition (ACC on/off events) ------------------------------
    log("Fetching ignition...");
    const ignitionRows = (await pagedAll("/v3/new/newReportRun/getAccList",
      { status: "all" }, j => j.result, j => j.dataTotalRows)).map(v => ({
        "Device Name": dName(v), "IMEI": v.imei, "ACC": v.acc,
        "Start Time": v.start || v.startTime, "End Time": v.end || v.endTime,
        "Duration": v.durSecond, "Duration (s)": num(v.duration)
      }));

    // ---- 9. build + download workbook ---------------------------------
    log(`Building Excel: mileage=${mileageRows.length}, daily=${dailyRows.length}, trips=${tripRows.length}, overspeed=${overspeedRows.length}, parking=${parkingRows.length}, idling=${idlingRows.length}, ignition=${ignitionRows.length}`);
    const wb = XLSX.utils.book_new();
    const add = (rows, name) => XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows.length ? rows : [{ note: "no data in range" }]), name);
    add(mileageRows, "Mileage Totals");
    add(dailyRows, "Daily Activity");
    add(tripRows, "Trips Detail");
    add(overspeedRows, "Overspeed");
    add(parkingRows, "Parking");
    add(idlingRows, "Idling");
    add(ignitionRows, "Ignition");
    const fname = `TrackSolid_Report_${startDay.trim()}_to_${endDay.trim()}.xlsx`;
    XLSX.writeFile(wb, fname);
    log("Done ->", fname);
    alert(`Done!\n\nDevices: ${devices.length}\nMileage: ${mileageRows.length}\nDaily: ${dailyRows.length}\nTrips: ${tripRows.length}\nOverspeed: ${overspeedRows.length}\nParking: ${parkingRows.length}\nIdling: ${idlingRows.length}\nIgnition: ${ignitionRows.length}\n\nDownloaded: ${fname}`);

    function num(x) { const n = parseFloat(x); return isNaN(n) ? x : n; }
  } catch (e) {
    err(e);
    alert("Extraction failed: " + e.message);
  }
})();
