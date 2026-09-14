# Ground Data Processing Tool — desktop app

An internet-capable Windows desktop app that reproduces the CSV → productivity workflow
from the Power Apps project (`apps/csv-productivity-builder`) without Dataverse,
Node, or a browser tab. The data-processing workflow also works offline. Same engine, but interactive: upload, review, calculate,
generate, edit, and export to CSV/Excel from a native window.

- **Release installer:** `Releases/*Setup.exe` — per-user install with automatic updates
- **Development build:** `dist/Ground-Data-Processing-Tool/Ground-Data-Processing-Tool.exe`
- **Engine:** `productivity_tool.py` (pure-Python port of the app's logic)
- **UI:** `app.py` + `web/` (pywebview + WebView2 front-end)
- **Templates:** `templates/`

## Workflow (mirrors the referenced app)

The window is laid out as a **top bar** (brand + connection pill + Dashboard), a
**workflow strip** with the four numbered stages — **1 Load · 2 Review ·
3 Generate · 4 Publish** — and a light **left rail** listing the five reference
tables plus **Settings**. The strip shows each stage's status and the primary
action for the stage you are on.

1. **Mode** — Auto / Negotiation / Land Sourcing (auto-detects from the upload's columns).
2. **Load Tables**
   - *Load all from Dataverse* — one click pulls every reference table live (see below); **or**
   - *Per-source file loaders* — a card for each reference table (Team Composition,
     Municipality Code, Mapping Status, Build Table, Existing Records). Each card has
     **Load file** (its own CSV/Excel), **Template** (save a correctly-headed template),
     and **View** (open the loaded rows). Existing Records feeds the ID base and
     `INDEXED PROPERTY UPDATE` detection.
   - *Upload CSV / Excel* — the rows to process; shown in the CSV Workspace.
     In the CSV Workspace, **Save to file** writes the current rows (your in-place
     edits plus the calculated columns once you've hit Calculate) back into the
     **same file you uploaded**, preserving its `.csv`/`.xlsx` format. Handy when
     you want the transaction data file on disk to reflect what you fixed in the app.
     The CSV Workspace and the Productivity Workspace are also **spreadsheet-like**:
     highlight a block of cells (click, **Shift+click**, or **click-drag**), then
     **Ctrl+C** to copy the block and **Ctrl+V** to paste a tab-separated block back
     (starting at the top-left of the selection). Pasting writes each cell through the
     normal cell-update path, so derived columns recompute as usual.

   **Remembered files:** once you load a reference table from a file, its path is
   saved to `app_settings.json` (in the app data folder for installed builds). The next time you open the app it
   **auto-loads those files** and restores the load type. A card shows “· remembered”;
   press **Remove** on the card (or in the table viewer) to forget it. If a remembered
   file was moved or deleted, the app notes it on startup and just skips it.

   **Recent-files dropdown:** every reference card and the upload row also keep a
   short **Recent files** list (last 6, most-recent-first) of files you've loaded
   before. Pick one from the dropdown to reload it instantly — no file browser, no
   re-selecting the same file every session. Uploaded data files remember which mode
   (Negotiation / Land Sourcing) they were loaded as, and switch the app to that mode
   automatically when picked. Removing a card's active file also drops it from that
   list; other recent entries stay available.
3. **Review & Calculate** — *Calculate* fills the derived columns
   (`AREA-INDEX`, `MUNICODE`, `MAPPING STATUS`, `BUILD`, `DATA USABILITY`, `YEAR`,
   `LOT AREA (HA)`).
4. **Generate Productivity** — negotiator splitting, `CHECKER`, `UNIQUE ID`,
   `POINTS` (1 ÷ rows sharing the UNIQUE ID), `WORK WEEK` (Wednesday-to-Tuesday cycle:
   starts Wednesday 00:00:00, ends Tuesday 23:59:59), `NEGO DISTINCTION`,
   and the LO OCCURRENCE / POINTS / COUNT by day and work-week. You can change the
   **Matched Negotiator** per row — `CORRECT TEAM/GROUP` and the MATCH badges
   recompute instantly.
5. **Filter & Export** — per-column filters, then *Export CSV* or *Export Excel*.

The Publish-to-Dataverse step is intentionally replaced by file export.

## Connect to Dataverse (live)

The app can pull Team Composition, Municipality Code, Mapping Status, Batangas
Build and existing records straight from your environment
(`https://YOUR_ORG.crm.dynamics.com`) instead of from files.

**Targeted & Fast Queries (No heavy 200k table scans):**
- **Max ID:** Instantly fetched via an OData top=1 sort query for sequential numbering.
- **Date Range / Work Week Filtering:** When a file is loaded, Dataverse records
  are queried strictly within the Wednesday-to-Tuesday work week window(s) of the file.
- **Targeted AREA-INDEX Checks:** The app queries Dataverse specifically for the
  distinct `AREA-INDEX`es in the uploaded batch for accurate `INDEXED PROPERTY UPDATE`
  detection across all historical data without downloading unrelated rows.
 Column logical
names are discovered at runtime from Dataverse metadata, so it keeps working even
if schema names differ; choice columns (Mapping Status, Data Usability) come back
as their display labels.

Sign-in is interactive (MSAL device-code) — **no secrets are stored**; a token is
cached locally so you don't sign in every launch.

### One-time setup (you or your Azure AD admin)

1. **Azure Portal → App registrations → New registration.**
   - Name it e.g. `Ground Data Processing Tool`.
   - Supported account types: *Accounts in this organizational directory only*.
   - No redirect URI needed.
2. **Authentication → Advanced settings → Allow public client flows → Yes.**
3. **API permissions → Add a permission → Dynamics CRM → Delegated →
   `user_impersonation` → Add.** Grant admin consent if your org requires it.
4. Copy the **Application (client) ID** and **Directory (tenant) ID** from the
   app's Overview page.
5. Enter these values on the app's **Settings** screen. Installed builds save
   **`dataverse_config.json`** under `%LOCALAPPDATA%\GroundDataProcessingTool`:
   ```json
   {
     "environment_url": "https://YOUR_ORG.crm.dynamics.com",
     "tenant_id": "<Directory (tenant) ID>",
     "client_id": "<Application (client) ID>"
   }
   ```
   For source development, copy `dataverse_config.example.json` to
   `dataverse_config.json` and fill in the same values. The real configuration
   file is intentionally excluded from Git.
   Official release installers receive the shared connection defaults from the
   encrypted `DATAVERSE_CONFIG_B64` GitHub Actions repository secret. This
   pre-fills the Settings screen on first installation while keeping the JSON
   out of source control. Values bundled into a distributed desktop installer
   must be treated as public configuration, never as passwords or client secrets.
6. Your Microsoft account must be a Dataverse user in that environment with read
   access to those tables (a role such as Basic User with read on the `cr63f_`
   tables).

### Using it

1. Click **Connect** → a code and `microsoft.com/devicelogin` link appear → sign
   in with your Microsoft account. The badge turns green when connected.
2. Click **Load from Dataverse** → all reference tables load; existing records
   reload automatically to match the mode of whatever file you upload.
3. Continue with Calculate → Generate → Export as usual.

File loading (below) still works as an offline fallback or for ad-hoc data.

## Reference templates (per source)

Each reference table is a separate one-sheet file. Use the **Template** button on
each card (or the ready-made files in `templates/`) and fill in the rows:

| Table | Template file | Columns |
|---|---|---|
| Team Composition | `team-composition-template.csv` | Employee Name, Team, Group, Position |
| Municipality Code | `municipality-code-template.csv` | Municipality, MuniCode, Province |
| Mapping Status | `mapping-status-template.csv` | Description, Mapping Status, Data Usability |
| Build Table | `build-table-template.csv` | Area Index, Build |
| Existing Records | `existing-records-template.csv` | ID, AREA-INDEX |

Each card's **Load file** accepts that table as `.csv` or `.xlsx`. (The older
combined `reference-template.xlsx` — one sheet per table — is still accepted by the
command-line mode.)

If a table is left unloaded the app falls back to the same defaults the original
used (team → `NO MATCH`, build → `BALANCE`, etc.).

Input files must match the column templates in `templates/`
(`negotiation-input-template.csv`, `land-sourcing-input-template.csv`), also
savable from the **Upload data file** card. If a required column is missing, it is
**added automatically as a blank column** and the app confirms which column(s) were
added — so the upload still proceeds instead of being rejected.

## Seeing UI changes

`web/` (index.html / style.css / app.js / shell.js) is **bundled into the exe at
build time**, so editing it does not change an exe that was already built. To see
edits: run `py app.py` during development, and rebuild the exe (below) to ship
them. Asset URLs carry a `?v=` cache key because the WebView2 profile in
`.webview/` is persistent and would otherwise serve the previous CSS/JS; bump that
key when you edit those files.

## Building an updater-enabled release

Velopack packages the PyInstaller `--onedir` output, generates a per-user Windows
installer, and creates full and delta update packages. Install the prerequisites once:

### Recommended: build and publish on GitHub

The repository includes `.github/workflows/release.yml`. To publish a release:

1. Open the repository's **Actions** tab.
2. Select **Release Windows installer**.
3. Choose **Run workflow**.
4. Enter a new version such as `1.0.0` and optional release notes.

The workflow validates the version, runs the tests, downloads the previous package
when available, generates full and delta update packages, and publishes a GitHub
Release. It uses GitHub's short-lived workflow token; no personal access token or
repository secret is required.

### Optional: build locally

```powershell
py -m pip install -r requirements-build.txt
dotnet tool install -g vpk --version 1.2.0
```

Create a release for a public GitHub repository:

```powershell
.\build-release.ps1 `
  -Version 1.0.0 `
  -ReleaseNotes .\release-notes.md
```

Or use a static HTTPS directory (Azure Blob Storage, S3, or a normal web server):

```powershell
.\build-release.ps1 `
  -Version 1.0.0 `
  -Source http `
  -FeedUrl https://downloads.example.com/ground-tool
```

The output appears in `Releases/`. Keep the older `.nupkg` files in that folder
when building a new version so Velopack can generate small delta updates.

For GitHub, upload a draft release using a token held in the shell environment:

```powershell
$env:GH_TOKEN = '<fine-grained token with repository Contents write access>'
.\publish-github-release.ps1
```

Review the draft on GitHub and publish it. Add `-Publish` to publish immediately.
Never put the token in `update_config.json` or source code. Public GitHub releases
need no token in the installed app.

For an HTTP feed, upload **every file** generated in `Releases/`, including
`releases.win.json`, to the feed URL. Clients discover the release from that JSON file.

### User update experience

- The installed app checks in the background shortly after startup.
- A newer release opens an **Update available** prompt with its release notes.
- The user can download immediately or choose **Later**.
- Settings → **Software updates** provides manual checking, progress, and
  **Update and restart**.
- Network/check failures do not interrupt startup.
- Settings, Dataverse configuration, token cache, and the WebView2 profile live in
  `%LOCALAPPDATA%\GroundDataProcessingTool`, outside the directory replaced by updates.

Users of the old single-file portable EXE must run the generated Setup executable
once. Subsequent releases update from inside the app. The app requires WebView2
(built into Windows 11; available as a free Microsoft runtime on older Windows).

The default update source is the public
`https://github.com/jmlinguito-ux/gdpt` repository. The release script generates
and embeds the requested version without modifying `update_config.json`.

## Command-line mode (optional)

`productivity_tool.py` still runs headless for batch/automation:

```
py productivity_tool.py --input rows.xlsx --reference reference.xlsx --output out.xlsx
```

## Notes / differences from the app

- The app's 50-row upload cap is removed (batch-friendly).
- Only `.csv` and `.xlsx` are supported — save legacy `.xls` as `.xlsx` first.
- Date and string-comparison quirks in `WORK WEEK` / `NEGO DISTINCTION` are
  reproduced exactly for output parity with the original.
