# Bot report reply sheets (all of Daniel's bots)

## Purpose
Every daily and weekly report also gets a Google Sheet "reply sheet". Daniel writes replies in it and the bot acts on them. The chat report must include the sheet's link.

## Naming
- Daily: `<Bot name> replies YYYY-MM-DD` (example: `Radar replies 2026-10-10`)
- Weekly: `<Bot name> weekly YYYY-MM-DD`
- One tab, named after the bot (example: `Radar`).

## Drive tree
`Bot Reports` (<FOLDER_ID_BOT_REPORTS>)
- `Active/<Bot>`: current sheets
- `History/<Bot>/<YYYY>`: finished or superseded sheets

| Folder | ID |
|---|---|
| Active | <FOLDER_ID_ACTIVE> |
| Active/Radar | <FOLDER_ID_ACTIVE_RADAR> |
| Active/Chief of Staff | <FOLDER_ID_ACTIVE_CHIEF_OF_STAFF> |
| Active/Zero | <FOLDER_ID_ACTIVE_ZERO> |
| Active/One-offs | <FOLDER_ID_ACTIVE_ONE_OFFS> |
| Active/Friends | <FOLDER_ID_ACTIVE_FRIENDS> |
| Active/Chess | <FOLDER_ID_ACTIVE_CHESS> |
| Active/Business | <FOLDER_ID_ACTIVE_BUSINESS> |
| Active/Pika | <FOLDER_ID_ACTIVE_PIKA> |
| Active/Money | <FOLDER_ID_ACTIVE_MONEY> |
| Active/Family | <FOLDER_ID_ACTIVE_FAMILY> |
| Active/Techno Euphora | <FOLDER_ID_ACTIVE_TECHNO_EUPHORA> |
| History | <FOLDER_ID_HISTORY> |
| History/Radar/2026 | <FOLDER_ID_HISTORY_RADAR_2026> |
| History/Chief of Staff/2026 | <FOLDER_ID_HISTORY_CHIEF_OF_STAFF_2026> |
| History/Zero/2026 | <FOLDER_ID_HISTORY_ZERO_2026> |
| History/One-offs/2026 | <FOLDER_ID_HISTORY_ONE_OFFS_2026> |
| History/Friends/2026 | <FOLDER_ID_HISTORY_FRIENDS_2026> |
| History/Chess/2026 | <FOLDER_ID_HISTORY_CHESS_2026> |
| History/Business/2026 | <FOLDER_ID_HISTORY_BUSINESS_2026> |
| History/Pika/2026 | <FOLDER_ID_HISTORY_PIKA_2026> |
| History/Money/2026 | <FOLDER_ID_HISTORY_MONEY_2026> |
| History/Family/2026 | <FOLDER_ID_HISTORY_FAMILY_2026> |
| History/Techno Euphora | <FOLDER_ID_HISTORY_TECHNO_EUPHORA> |
| History/Techno Euphora/2026 | <FOLDER_ID_HISTORY_TECHNO_EUPHORA_2026> |

- New year: create `History/<Bot>/<YYYY>` (user-Google-drive `create_file`, mimeType `application/vnd.google-apps.folder`, parentId = `History/<Bot>` folder), then add it to this table.
- Ignore the ghost: there is an empty duplicate `Radar` folder under Active (`<FOLDER_ID_GHOST>`) that the APIs can't remove. Don't use it.

## Layout rules
- Columns: `ID | Item | Status / details | Your reply | Links`
- Column widths (px): A=50, B=280, C=668, D=380, E=300
- IDs are hierarchical (1, 1.1, 1.2a), with one row per item and per sub-item. Store IDs as text (stringValue) so "2.10" doesn't turn into the number 2.1.
- Software items start with `SoftwareName:`.
- Every link must be clickable:
  - one link: `=HYPERLINK(url, "short label")`
  - several links: rich text in one cell, one label per line
  - labels look like `<ORG>/<REPO>#123`, `owner/repo@abc1234` (commits), `Gmail thread`, `Jira PROJ-123`.
- Header row: bold, grey fill (#D9D9D9), frozen.
- All cells: wrap text, top-aligned.
- Data rows alternate white and #F3F3F3 (use addBanding).
- Your reply column (D) is light yellow, alternating #FFF9DB / #FFF3C4 per row so the banding still shows. An explicit cell fill overrides banding.
- Content follows the same rules as chat reports: repeat every open item that hasn't been acted on, top priority first.

## Lifecycle
1. Create the sheet in `Active/<Bot>`.
2. Read replies with Composio `GOOGLESHEETS_BATCH_GET` (`ranges: ["<Tab>!A1:Z500"]`). A row's trailing empty cells can be missing, so a short row means no reply.
3. Act on the replies.
4. When every row is answered or handled, or the next report supersedes the sheet, move it to `History/<Bot>/<YYYY>`:
   - Composio `GOOGLEDRIVE_MOVE_FILE` `{file_id, add_parents: <dest>, remove_parents: <current parent>, supports_all_drives: true}`, or
   - user-Google-drive `update_file` `{fileId, parentId: <dest>}`.
5. Carry unanswered open items into the next sheet.
6. Before you trash or replace any sheet, check that Your reply is empty. If it isn't, copy the replies forward and report them.

## Tool recipes that worked (Radar, 2026-10-10)
Composio has no first-class tool for banding or rich-text runs. What works is the Sheets REST batchUpdate through `proxy_execute` in Composio `COMPOSIO_REMOTE_WORKBENCH`.

1. **Create** (user-Google-drive `create_file`):
   `{"title": "Radar replies 2026-10-10", "mimeType": "application/vnd.google-apps.spreadsheet", "parentId": "<Active/<Bot> id>"}`
   This makes an empty sheet directly in the folder, and the first tab has sheetId 0. This is better than a CSV upload, which turns IDs like 2.1 into numbers. A CSV upload (contentMimeType `text/csv`) also works if you need it.
2. **Everything else in one call** (in COMPOSIO_REMOTE_WORKBENCH):
```python
res, err = proxy_execute('POST',
  f'https://sheets.googleapis.com/v4/spreadsheets/{SID}:batchUpdate',
  'googlesheets', body={'requests': reqs})
```
   `reqs`, in this order:
   - `updateSheetProperties`: `{properties:{sheetId:0,title:'Radar',gridProperties:{frozenRowCount:1,columnCount:5}}, fields:'title,gridProperties.frozenRowCount,gridProperties.columnCount'}`
   - `updateCells`: `{rows:[{values:[cell,...]}...], start:{sheetId:0,rowIndex:0,columnIndex:0}, fields:'userEnteredValue,textFormatRuns'}`, where each cell is one of:
     - text: `{userEnteredValue:{stringValue:'...'}}`
     - one link: `{userEnteredValue:{formulaValue:'=HYPERLINK("url","label")'}}`
     - several links: `{userEnteredValue:{stringValue:'lab1\nlab2'}, textFormatRuns:[{startIndex:0,format:{link:{uri:u1},underline:true,foregroundColorStyle:{rgbColor:{red:.067,green:.333,blue:.8}}}},{startIndex:len(lab1),format:{}},{startIndex:len(lab1)+1,format:{link:{uri:u2},...}}]}`
   - `updateDimensionProperties`, once per column: `{range:{sheetId:0,dimension:'COLUMNS',startIndex:c,endIndex:c+1}, properties:{pixelSize:w}, fields:'pixelSize'}`
   - `repeatCell` on A1:E<N>: `{cell:{userEnteredFormat:{wrapStrategy:'WRAP',verticalAlignment:'TOP'}}, fields:'userEnteredFormat(wrapStrategy,verticalAlignment)'}`
   - `repeatCell` on the header row: `{cell:{userEnteredFormat:{textFormat:{bold:true},backgroundColorStyle:{rgbColor:#D9D9D9}}}, fields:'userEnteredFormat(textFormat.bold,backgroundColorStyle)'}`
   - `addBanding`: `{bandedRange:{range:<rows 1..N, cols 0..5>, rowProperties:{firstBandColorStyle:{rgbColor:white}, secondBandColorStyle:{rgbColor:#F3F3F3}}}}`
   - `repeatCell` per data row on column D: backgroundColorStyle #FFF9DB for odd row indexes, #FFF3C4 for even, `fields:'userEnteredFormat.backgroundColorStyle'`
   - Ranges are GridRange objects: `{sheetId:0,startRowIndex,endRowIndex,startColumnIndex,endColumnIndex}`, 0-based with exclusive ends. Colors are 0-1 floats (hex/255).
3. **Verify**:
   - `GOOGLESHEETS_BATCH_GET` for values. Link cells show their labels.
   - `GOOGLESHEETS_GET_SPREADSHEET_INFO` with `fields: "sheets(properties,bandedRanges.bandedRangeId,data.columnMetadata.pixelSize)"` for widths, frozen row and banding.
   - `GOOGLESHEETS_GET_SPREADSHEET_INFO` with `ranges` and `fields: "sheets.data.rowData.values(formattedValue,hyperlink,textFormatRuns,effectiveFormat(backgroundColor,wrapStrategy,verticalAlignment,textFormat.bold))"` for links and fills.
4. **Trash an old sheet**: user-Google-drive `trash_file` `{fileId}`.

Composio `GOOGLESHEETS_FORMAT_CELL`, `UPDATE_DIMENSION_PROPERTIES` and `UPDATE_SHEET_PROPERTIES` exist and work for simple cases, but they can't do banding or multi-link rich text. Use the proxy batchUpdate instead.

## Gotcha: the Composio workbench kernel is shared (Friends, 2026-10-11)
All bots run in the same persistent COMPOSIO_REMOTE_WORKBENCH kernel, so globals like `SID` can be overwritten by another bot between your cells (Friends' verify read Radar's sheet this way). Define the spreadsheet id inside the same cell that uses it, ideally with a bot-specific name, and never rely on a variable set in an earlier cell.

Dashboard: also publish each report to the Bot Briefs dashboard and read its inbox each run. See /home/box/agent-data/shared/bot-dashboard.md.
