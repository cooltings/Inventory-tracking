# Zaf Inventory

A standalone desktop app for managing eBay phone stock, faulty and repair devices, parts, sales and profit.
It runs on Windows (the main target), macOS and Linux. Everything is stored on your own computer.

## Download

Ready-made apps for **Windows, macOS (Apple Silicon and Intel) and Linux** are on the
[Releases page](../../releases/latest). Unzip and run, no install needed. See the release
notes for first-launch tips, since the builds aren't code-signed.

New releases are built automatically by GitHub Actions whenever a version tag is pushed:

```bash
git tag v1.0.1 && git push origin v1.0.1
```

## Android tablet (Samsung DeX)

`ZafInventory-Android-arm64.apk` on the Releases page is the same app for Android tablets and phones.
- **Installing:** download the APK on the tablet and open it. Android will ask you to allow installs from your browser or file manager the first time.
- **DeX:** in Samsung DeX it opens in its own window, which you can resize and maximise like the Windows app. It works with a keyboard and mouse, and the keyboard shortcuts work too.
- **Where data is kept:** in the app's private storage, which is removed if the app is uninstalled. Sync with the PC, or use **File → Backup & Restore → Save a Copy As...**, to keep a copy elsewhere.
- **Files:** exports, PDF reports and backup copies are saved wherever you choose in the Android file picker (Downloads, Drive and so on).

## Syncing the PC and the tablet

Sync works two ways over your Wi-Fi. You can edit on either device and sync whenever both are on the same network. No internet or cloud account is involved.

1. **On the Windows PC:** go to **File → Sync Settings...** and tick *Allow other devices to sync with this one*. Note the address and 6-digit **pairing code** it shows. If Windows asks about the firewall, allow access on **private** networks.
2. **On the tablet:** go to **File → Sync Settings...** and press **Find PC**, or type the address. Enter the pairing code and press **Sync Now**.
3. **After that:** press **Sync** in the toolbar (Ctrl+Y) on the tablet whenever you want to sync.

**Manual or automatic:** sync is **manual only** by default, so it only happens when you press Sync. On the Sync screen you can switch to *Automatic* to also sync when the app opens and every few minutes. If the PC isn't reachable then, the automatic sync is skipped quietly.

**What happens when the same thing changes on both devices**
- **Edits:** if the same item was edited on both, the newest edit wins.
- **Deletes:** something deleted on one device is deleted on the other.
- **Edited after a delete:** an item edited on one device *after* it was deleted on the other comes back.
- **Photos:** photos are copied whichever way they're missing.

**What syncs:** stock, parts, sales, notes, photos, test results, sheets, the activity log and the shared lists and fee settings all sync. Theme, backup settings and sync settings stay separate on each device.

**SKUs:** items added on the tablet get SKUs like `WP-T00012`, so they never clash with SKUs from the PC.

**Setting up the tablet:** set it up by syncing to an empty install, rather than by restoring a backup. A backup from before version 1.2, restored on both devices, would show duplicates on the first sync.

## Building it yourself on Windows

1. Install **Python 3.10 or newer** from python.org. During setup, tick **"Add python.exe to PATH"**.
2. Double-click **`build_windows.bat`**. It takes a few minutes the first time.
3. The finished app is at **`dist\ZafInventory\ZafInventory.exe`**.
   Copy the whole `dist\ZafInventory` folder anywhere you like, or zip it up.
   Python isn't needed on the PC that runs it.

To try it straight from the source code without building, double-click `run_windows.bat`.

**Portable mode:** put an empty file called `portable.txt` next to `ZafInventory.exe`.
The app will then keep all its data in a `data` folder beside the exe, which suits a USB stick.
Without that file, data is kept in `%APPDATA%\ZafInventory`.

On macOS or Linux, run `./build_mac_linux.sh`, or run from source with `pip install -r requirements.txt` and then `python main.py`.

## Features

| Tab | What it does |
|---|---|
| **Dashboard** | Shows stock counts and stock value, and profit for this month, this year and all time. Also shows the average days to sell, a 12-month revenue vs profit chart, parts that are low on stock, aged stock, and your most profitable brands. Double-click an item to open it. |
| **Working Phones** | Holds brand, model, storage, colour, IMEI, serial number, quantity, network, grade, battery %, faults, status, location, purchase price, repair costs, list price and estimated profit. |
| **Faulty / Repairs** | Has the same fields with a tick-list of faults. **Use Part** fits a part from stock: the part's quantity goes down and its cost is added to the device's repair cost. **Move to Working** is for once the device is fixed. |
| **Parts** | Holds part type, compatible models, IMEI and serial (for boards), quantity, a low-stock level, unit cost and stock value. |
| **Sold** | Every sale with fees, postage, item cost, profit, margin and a **running profit** total. You can filter by period, platform or status. **Return/Refund** can put the item back into stock. |
| **Sheets** | Spreadsheet-style ledgers, as many as you want (cash, expenses and so on). Each has Money In and Money Out columns, a **running total** and an opening balance. Ctrl+C and Ctrl+V copy and paste to and from Excel or Google Sheets. |
| **Fee Calculator** | Takes sale price, P&P, item cost, postage and other costs, plus fee %, fixed fee and promoted %. It works out profit or loss, margin, ROI, the break-even price and the price you'd need for a target profit. |
| **Activity Log** | Records every add, edit, sale, move, return and restore. |

**Device testing and PDF test reports:** select a phone and press **Ctrl+T** (or **Test** in the toolbar).
- The checklist has 93 tests in 10 sections: power and charging, display and touch, cameras, audio, network and connectivity, sensors and haptics, buttons and ports, software and security, cosmetic and physical, and accessories.
- Each test is **Pass**, **Fail** or **N/A**, with a note. Click the buttons, or select a row and press **P** / **F** / **N**. Failing a test takes you straight to its note.
- **Auto N/A** marks features the phone can't have (for example the iCloud lock on an Android phone, or the FRP lock on an iPhone). You can also add your own tests with **Add Custom Test**, and filter to show only what's still untested or failed.
- The **Device details** tab holds brand, model, model number, colour, storage, RAM, IMEI, serial number, network, OS version, battery health and cycles, cosmetic condition and cosmetic notes, plus who tested it and when.
- **Generate PDF Report** (or **Ctrl+R** from the phone list) creates a printable report with all of that, an overall verdict, the failed items and their notes up front, then every test colour-coded with its notes.
- Everything saves as you go. The **Tests** column in the phone list shows progress and any failures. Set your business name for the report header in Settings.

**Days to sell:** measured from when a phone is first marked **Listed** until it sells. The app records the **Listed On** date automatically, and you can correct it on the item form. For phones listed before this was recorded, it uses the first "Set Status → Listed" entry in the Activity Log. If there isn't one, it falls back to the bought / added date, and the dashboard says how many sales used the fallback. The Sold tab has a **Days to Sell** column, and the stock tabs have **Days Listed**.

**Past sales / profit adjustments:** on the Sold tab press **Add**, or use **Add Past Sales / Profit Adjustment...** on the Dashboard. Enter earlier sales as one total: how many phones, the revenue and the profit (negative for a loss). Give it a date to put it in that month, or leave it undated so it only counts towards all-time. It's included in every total and the running profit, it syncs, and you can edit or delete it like any sale.

**Write-offs:** right-click an item and choose **Write Off...** (or set its status to *Written Off*).
It stays on record with your reason as a note, greyed out, but is no longer counted in stock totals or
stock value. The dashboard shows the total written off, and you can set it back to *In Stock* at any time.

**On every item**
- Notes with timestamps (a full history, not a single box) and photos (you can drag and drop them in).
- An IMEI checksum check and a warning if the same IMEI is already in stock.
- **Save & Add Another**, which keeps brand, model and price so you can enter a batch of the same phone quickly.
- **Copy Listing Title**, which builds an eBay-style title such as `Apple iPhone 12 128GB Blue Unlocked - Grade A`.
- If the quantity is above 1, you can sell some of the units and keep the rest in stock.

**Searching**
- On each tab, type any words in the search box (for example `iphone 12 blue`). There are also drop-down filters for brand, model, grade, fault, status, location and more.
- **Ctrl+K** searches everything at once, including IMEIs, serials, order numbers, buyers and the text of notes.
- Right-click a column heading to show or hide columns. Columns can be dragged to reorder and resized, and the layout is remembered.

**Backups**
- Each backup is a single `.zip` containing the database and all photos. It's taken safely while the app is running.
- By default, backups run automatically every 24 hours and when you close the app. The newest 30 automatic backups are kept, and manual backups are never deleted.
- **Extra copy to:** in Settings, point this at a OneDrive, Google Drive or USB folder to get an off-site copy.
- **File → Backup & Restore** lets you back up, verify a backup, or restore one. Before restoring, the app always saves your current data first, so a restore can be undone.
- **File → Check & Compact Database** runs an integrity check.

**Other**
- Import and export CSV (Ctrl+I and Ctrl+E). Exports open cleanly in Excel. To see the column format for imports, export a tab first.
- Settings cover currency, fee rates, the dark or light theme, and editable lists (brands, faults, part types, locations, platforms).

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+N / Enter / Del | Add / edit / delete |
| Ctrl+M | Notes and photos |
| Ctrl+S | Mark as sold |
| Ctrl+U | Use part |
| Ctrl+D | Duplicate |
| Ctrl+F | Search this tab |
| Ctrl+K | Search everything |
| Ctrl+Y | Sync |
| Ctrl+B | Back up now |
| Ctrl+1 … Ctrl+8 | Switch tabs |
| F5 | Refresh |

## Project layout

```
main.py                     entry point
zaf_inventory/fields.py     every field, drop-down list and default (edit here to add fields)
zaf_inventory/db.py         SQLite storage, sales and stock logic, dashboard stats
zaf_inventory/backup.py     backup / verify / restore
zaf_inventory/views.py      inventory tabs, filters, CSV
zaf_inventory/sheets.py     running-total sheets
zaf_inventory/calculator.py fee calculator
zaf_inventory/dialogs.py    item, sale, part, settings, backup and search dialogs
```

To add a new field, add one line to `fields.py`. The database upgrades itself the next time the app starts, and the field then appears in the forms, the tables and CSV import and export.
