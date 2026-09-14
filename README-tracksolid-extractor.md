# TrackSolid Pro → single merged Excel extractor

Pulls **every device** in your TrackSolid Pro account for a date range you choose and
merges them into **one `.xlsx`** with seven sheets:

| Sheet | One row per… | Columns |
|-------|--------------|---------|
| **Mileage Totals** | device | Device, IMEI, SIM, Model, Total km, Start, End |
| **Daily Activity** | device × day | Device, IMEI, Date, Trips, km, Driving time, Avg/Max speed, Fuel |
| **Trips Detail** | individual trip | Device, IMEI, Date, Start/End time, Travel time, km, Avg/Max speed, GPS start/end, Odometer start/end, Fuel |
| **Overspeed** | speeding event | Device, IMEI, SIM, Model, Alert type, Speed, Start/End, Duration, Start/End address, GPS |
| **Parking** | stop, engine OFF | Device, IMEI, SIM, Model, Start/End, Duration, Lat/Lng, Address |
| **Idling** | stop, engine ON | Device, IMEI, SIM, Model, Start/End, Duration, Lat/Lng, Address |
| **Ignition** | ACC on/off event | Device, IMEI, ACC state, Start/End, Duration |

> No **Fuel** sheet: these devices have no fuel sensor, so the fuel API returns 0 rows
> (every mileage/trip Fuel value is 0). If you later add fuel-capable devices, say so and
> a Fuel sheet can be wired in.

It reuses your active login session, so there is **no password or token to copy**.

---

## Option A — Console (simplest)

1. Log in to <https://www.tracksolidpro.com> (any Report page).
2. Press **F12** → click the **Console** tab.
3. Open `tracksolid-extractor.js`, copy **all** of it, paste into the console, press **Enter**.
4. Type the **start** and **end** dates (`YYYY-MM-DD`) when asked.
5. The file `TrackSolid_Report_<start>_to_<end>.xlsx` downloads automatically.

## Option B — Bookmarklet (one click, reusable)

1. Create a new bookmark in Chrome (bookmark any page, then edit it).
2. Set the **Name** to e.g. `TrackSolid Export`.
3. Open `tracksolid-extractor.bookmarklet.txt`, copy the whole line, and paste it into
   the bookmark's **URL** field. Save.
4. While logged in to TrackSolid Pro, click the bookmark → enter dates → file downloads.

---

## Notes
- If you see a **401 / session expired** message, refresh the page, log in again, re-run.
- "Daily Activity" and "Trips Detail" come from the same source, so their totals reconcile.
- The account currently exposes **15 devices** via the API (the report screen labels it
  "14"); the tool includes all devices the API returns. A device with no movement in the
  range simply won't appear in the Mileage sheet.
- Times in **Trips Detail** are clock times for that `Date`; **Mileage** Start/End are full
  timestamps and may fall just outside the range (they mark the first/last known fix).

## Want more report types?
Still available on the same pattern if you want them as extra sheets: Track Details
(raw GPS points), Geo Fence in/out, Temperature/Humidity, Driver Behavior, and the
OBD "Travel statistics" / "Vehicle fault" reports. Just say which.
