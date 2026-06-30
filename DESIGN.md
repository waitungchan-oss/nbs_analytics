# NBS Analytics DESIGN.md

> Purpose: This file is the UI/UX design contract for Stitch and other design agents. It describes how the NBS Analytics interface should look, feel, and behave without changing Python logic, SQLite data, cleaning rules, forecasting models, backtests, or exports.

## 1. Product Context

NBS Analytics is an internal enterprise analytics and AI decision dashboard for Hong Kong China Travel Service sales and marketing operations.

The system helps management, operations, sales teams, and analysts monitor net revenue, product mix, branch performance, sales-channel contribution, AI forecasts, data quality, model governance, lead signals, driver explanations, and exportable audit reports.

The official revenue scope is:

`不含掛賬核銷與TT退款轉團款`

This scope must remain visible in the UI wherever revenue, forecast, backtest, or export data is shown.

The redesigned UI should feel like an enterprise BI cockpit rather than a marketing landing page. It should be dense, calm, precise, and easy to scan during repeated business reviews.

## 2. Design Direction

Design voice:

- Professional, stable, and operational.
- Business-facing, not developer-facing.
- Designed for scanning numbers, spotting risk, comparing dimensions, and exporting reports.
- Inspired by enterprise NBS / CTS platform language: dark navy navigation, light working canvas, restrained blue accents, clear table hierarchy.
- Avoid decorative hero sections, large marketing cards, gradient orbs, playful illustration, and oversized storytelling layouts.

Primary experience:

- The dashboard itself is the first screen.
- The control center, KPI summary, ranking tables, drill-down charts, AI forecast, and exports should be immediately accessible.
- UI density is acceptable, but hierarchy must be clearer than the current prototype.

## 3. Design Principles

1. Separate management views clearly.
   - Daily Forecast = single-day volatility and short-term risk.
   - 7-Day Macro = future 7-day total, not calendar week.
   - Month-End Macro = MTD actual plus remaining-month forecast, not 30-day total.

2. Keep business scope visible.
   - Revenue cards, forecast sections, backtest sections, and exports should repeat or inherit the official revenue scope label.

3. Make filtering deliberate.
   - Filters should feel like a control center.
   - Year, month, date range, branch, and sales group should be grouped and visually separated from output sections.
   - Applied-filter state should be obvious.

4. Prioritize operational readability.
   - Tables should support dense data.
   - Numeric columns should align right.
   - Percentages, HKD values, WAPE, and health labels should use consistent formatting.

5. Treat charts, tables, and downloads as equal citizens.
   - Important analytics views should not rely on charts alone.
   - Each major forecast or backtest view should have a chart, a readable table, and a clear export path when applicable.

6. Label operational status honestly.
   - `正式` means production-facing official dashboard or forecast.
   - `診斷` means analysis-only, not a replacement for official results.
- `實驗` means not ready for official decisioning.
- `只讀` means no SQLite writeback and no business-rule mutation.
- `GMV 排除訂單看板` is a separate session-only view and must not visually override the official revenue cockpit.

## 4. Visual Tokens

Use these tokens as the default visual system. Stitch may refine the values, but the final design should stay within this enterprise BI direction.

```yaml
brand:
  name: "NBS Analytics"
  tone: "enterprise-bi-cockpit"
  scope_label: "不含掛賬核銷與TT退款轉團款"

colors:
  primary_navy: "#0B1F3A"
  primary_blue: "#118DFF"
  cts_blue: "#1B5DBF"
  active_blue: "#2F80ED"
  background_page: "#F4F7FB"
  background_surface: "#FFFFFF"
  background_subtle: "#EEF4FA"
  border: "#D8E0EA"
  divider: "#E5EAF0"
  text_primary: "#1F2937"
  text_secondary: "#52616F"
  text_muted: "#7A8694"
  success: "#1F9D55"
  warning: "#D97706"
  danger: "#C2410C"
  critical: "#B91C1C"
  info: "#2563EB"
  diagnostic: "#64748B"
  experimental: "#7C3AED"

chart_colors:
  actual: "#323130"
  arima: "#118DFF"
  prophet: "#E66C37"
  lightgbm: "#6B007B"
  consensus: "#2AA7B8"
  lower_upper_fill: "#CDEFF5"
  grid: "#E5E5E5"

typography:
  ui_font: ["PingFang TC", "Microsoft JhengHei", "Noto Sans TC", "Arial", "sans-serif"]
  mono_font: ["SFMono-Regular", "Consolas", "Liberation Mono", "monospace"]
  page_title: "28-34px / 700"
  section_title: "18-22px / 700"
  card_label: "12-13px / 600"
  card_value: "24-32px / 700"
  body: "14-16px / 400"
  table: "12-14px / 400"

spacing:
  page_padding: "24px"
  section_gap: "24px"
  card_gap: "12px"
  compact_gap: "8px"

radii:
  panel: "8px"
  card: "8px"
  control: "6px"
  badge: "999px"

shadows:
  panel: "0 1px 2px rgba(15, 23, 42, 0.08)"
  elevated: "0 8px 24px rgba(15, 23, 42, 0.10)"
```

## 5. Layout System

The target UI should use a stable enterprise dashboard shell.

### App Shell

- Left sidebar navigation:
  - Dark navy background.
  - Compact CTS / NBS identity at the top.
  - The sidebar is split into two explicit zones: `Navigation` and `Control Center`.
  - `Navigation` uses grouped submenu sections:
    - Overview
    - Data Quality
    - AI Forecast
    - Governance
    - Advanced Analytics
    - Exports
  - Navigation links should be page anchors, not data filters.
  - Clicking a menu item should jump to the target section without refreshing or rerunning the whole Streamlit app.
  - Active item uses blue highlight and strong contrast.
  - Each item may show a compact status badge, such as `正式`, `只讀`, `稽核`, `人工確認`, `宏觀`, `回測`, `治理`, `診斷`, `解釋型`, or `匯出`.
  - Collapsible behavior should remain available through the Streamlit-native sidebar collapse control.
  - The collapse control should visually align with the NBS Analytics identity area, not float far away from the menu.

- Top header:
  - White or very light background.
  - Shows system name, current data scope, last updated state, and optional user/system status.
  - Should not become a marketing hero.

- Main canvas:
  - Light grey-blue background.
  - Sections use white panels or unframed full-width bands.
  - Avoid nested cards inside cards.

### Primary Page Order

1. Control Center
2. Current Analysis Context
3. Executive KPI Summary
4. Annual Overview
5. Branch Performance Ranking
6. Product Drill-down
7. Sales Group / Specialist Channel View
8. Data Quality and Entity Resolution
9. AI-assisted Data Cleaning Suggestions
10. AI Forecast: Daily / 7-Day / Month-End
11. Forecast Governance and Feature Store / Lead Signal
12. Causal Analytics and Model Backtest Diagnostics
13. Export and Audit Logs

## 6. Core Components

### Control Center

Purpose: let users apply scope intentionally without accidentally clearing the dashboard.

The Control Center is not the navigation menu. It only controls analytical scope and UI theme.

Required controls:

- Light / dark interface theme selector
- Year selector
- Month selector
- Date range selector
- Branch selector
- Sales group selector
- Apply filter button
- Reset button

Design requirements:

- Group controls below the sidebar navigation in the left sidebar.
- Make applied filters visible as compact chips or summary text.
- Use primary blue only for the apply action.
- Navigation clicks must not change any Control Center value.
- Control Center form submission may rerun Streamlit; navigation anchor clicks should not.
- Reset should be secondary and lower emphasis.

### KPI Cards

Use compact cards for high-level metrics:

- Net Revenue
- Tour Revenue
- Cruise Revenue
- Ticket Revenue
- Visible Branches / Specialists

Card design:

- Small label at top.
- Large HKD value.
- Short context note.
- Optional accent line or icon.
- Avoid oversized decorative cards.

### Ranking and Drill-down Tables

Tables must be business-readable:

- Right-align numeric values.
- Use thousands separators.
- Keep HKD and percentage formatting consistent.
- Freeze or visually emphasize header row.
- Use subtle row dividers.
- Highlight top-ranked rows with restrained emphasis, not bright backgrounds.

### Chart Panels

Chart panels should include:

- Clear title.
- Short description only when needed.
- Chart.
- Optional small summary table.

Do not use charts as decoration. Each chart should answer a business question.

### Health Badges

Health labels:

- `優秀`
- `可接受`
- `可參考`
- `需謹慎`

WAPE thresholds:

- WAPE < 10% = 優秀
- 10%-20% = 可接受
- 20%-30% = 可參考
- > 30% = 需謹慎

Badge colors:

- 優秀 = success green
- 可接受 = info blue
- 可參考 = warning amber
- 需謹慎 = danger red-orange

Health badges must always be paired with the actual WAPE value.

### Status Badges

Use status badges to prevent business meaning from being confused:

- `正式`: official dashboard / official forecast view.
- `診斷`: read-only diagnostic insight.
- `實驗`: experimental model or selector.
- `只讀`: derived view with no SQLite writeback.
- `人工確認`: suggestion requires user confirmation before becoming a rule.

Badges should be compact but must not truncate Chinese labels.

## 7. AI Forecast Design Rules

The AI section must be visually split into three independent views. Do not merge Daily, 7-Day, and Month-End into one table or one chart.

If the AI / backtest runtime cache was intentionally deferred during upload, the section should surface a visible, explicit action labeled `補算 AI`. That action is the only trigger for a full AI / backtest recompute; ordinary reruns or navigation changes should not perform the recomputation automatically.

### Daily Forecast

Business meaning:

- Single-day volatility forecast.
- Used for short-term operational risk.
- Daily WAPE may be higher than macro WAPE.

Required display:

- Chart title: `Daily Forecast：逐日波動預測`
- Actual line
- ARIMA reference line
- Prophet reference line
- LightGBM reference line
- Consensus line
- Lower / Upper interval
- Future 7-day table
- Full 30-day expandable table

### 7-Day Macro Forecast

Business meaning:

- Rolling future 7-day total.
- Not a natural calendar week.

Required display:

- Chart title: `7-Day Macro Forecast：未來 7 日總額（不是自然週）`
- KPI cards:
  - 7-day Consensus
  - Lower / Upper
  - 7-day macro health
  - window start / end
- Chart showing daily composition and 7-day total context.
- Summary table.

### Month-End Macro Forecast

Business meaning:

- Month-to-date actual plus remaining-month forecast.
- Not a simple 30-day forecast sum.

Required display:

- Chart title: `Month-End Macro Forecast：MTD + 本月剩餘預測`
- KPI cards:
  - MTD Actual
  - Remaining Days Forecast
  - Month-End Consensus
  - Month-End Macro Health
- Pacing chart with actual-to-date and projected month-end.
- Summary table.

## 8. Backtest and Diagnostic Design Rules

Backtest content should be clearly separated from forecast content.

Required tables:

- Daily Current Models
- Daily Robust WAPE
- Daily Extreme Impact
- Daily Normal-Day Diagnostic Best Model
- Daily Two-Lane Selector Result
- 7-Day Macro Backtest
- Month-End Macro Backtest
- Suggested Weights
- Strategy Comparison

Design requirements:

- Make official WAPE and diagnostic WAPE visually distinct.
- Do not present trimmed or normal-day WAPE as the official accuracy.
- Explain whether a model is production forecast, diagnostic-only, or experimental.
- Experimental results should use neutral styling and should not look like the default forecast.

### Data Quality and Entity Resolution

Required display:

- `Data Quality Scorecard：資料品質健康檢查`
- `Entity Resolution Audit：單號匹配稽核`
- KPI cards for total score, latest data date, missing-date count, match health, and official-scope impact.
- Expandable detail tables for date coverage, field completeness, amount health, entity matching, and unmatched IDs.

Design requirements:

- Mark these sections as `只讀` and `診斷`.
- Do not make quality scores look like forecast accuracy.
- Tables should be compact and audit-friendly.

### AI-assisted Data Cleaning

Required display:

- Suggestion inbox table with type, candidate value, evidence, impact rows, confidence, risk, and suggested action.
- Clear `人工確認` affordance before applying selected suggestions.
- Preview of rules that would be added to `rules_config.json`.

Design requirements:

- Never imply suggestions are automatically applied.
- Low-confidence items should look observational, not actionable.
- Keep risk labels visible beside each suggestion.

### Forecast Governance

Required display:

- Overall Forecast Governance.
- Daily Official Health.
- 7-Day Macro Health.
- Month-End Macro Health.
- Model Health Matrix and Action Recommendations.

Design requirements:

- Explain that governance score is not a new accuracy metric.
- Separate `正式`, `診斷`, and `實驗` model rows.
- Bias and stability warnings should be visible, not hidden behind WAPE.

### Feature Store / Lead Signal

Required display:

- Feature Catalog.
- Daily Lead Signal Snapshot.
- Lead Signal Health.
- Model Readiness Matrix.
- `NoFutureLeak` status.

Design requirements:

- `NoFutureLeak` must be visible as a governance badge.
- Feature readiness should not look like a model recommendation.
- Use compact tables because this section is for analysis and model preparation.

### Causal Analytics

Required display:

- `Causal Analytics：營收變動解釋`
- Change Summary.
- Top Driver Contribution.
- Event Window Explanation.
- Reconciliation.

Design requirements:

- Always state: this is explanatory driver analytics, not strict causal proof.
- Driver tables should show Current, Baseline, Delta, Contribution, and Direction.
- Reconciliation should make clear that each dimension reconciles independently and dimensions should not be added together.

## 9. Export and Audit Area

The export area should feel reliable and low-friction.

Required export actions:

- Full dimension fact table
- Full dimension fact table excluding 掛賬核銷
- Full dimension fact table excluding 掛賬核銷 and TT 退款轉團款
- AI forecast workbook
- Model backtest workbook
- Data Quality Scorecard workbook
- Entity Resolution Audit workbook
- AI-assisted Data Cleaning suggestion workbook
- Forecast Governance workbook
- Feature Store / Lead Signal workbook
- Causal Analytics driver explanation workbook
- Cleaning anomaly log when available

Design requirements:

- Group exports by business purpose.
- Include one-line descriptions.
- Use download icons or familiar download affordances.
- Show empty state when no anomaly log is available.

## 10. Empty, Loading, and Error States

Loading states:

- Show which stage is loading: SQLite, cleaning, dashboard metrics, AI forecast cache, or model backtest.
- Avoid generic indefinite messages when possible.

Empty states:

- Explain whether no data exists because of filters, date range, or missing upload.
- Provide a clear reset or next action.

Error states:

- State the failing subsystem.
- Show a short business-readable message.
- Keep technical details expandable.

## 11. Responsive Behavior

Desktop is the primary target.

Desktop:

- Sidebar plus main content.
- Sidebar defaults to open on desktop.
- Sidebar navigation remains above Control Center.
- Sidebar collapse button remains available and should align with the NBS Analytics brand block.
- KPI cards in 4-5 columns when space allows.
- Tables may use horizontal scrolling.

Tablet:

- Sidebar may collapse.
- KPI cards reduce to 2 columns.
- Control Center remains accessible without pushing charts too far down.

Mobile:

- Mobile support is secondary.
- Stack cards and charts vertically.
- Avoid clipping long Chinese labels.
- Use horizontal scroll for dense tables.

## 12. Accessibility

Minimum requirements:

- Text contrast should meet WCAG AA where practical.
- Do not rely on color alone for health states.
- Health badges must include text labels.
- Tables need clear header hierarchy.
- Chart legends must be readable and not crowded.
- Avoid tiny labels below 12px for core business values.

## 13. Stitch Generation Instructions

When using this file with Stitch:

- Generate a dashboard UI concept for an enterprise BI system.
- Preserve the information architecture and business meaning described here.
- Do not redesign this as a landing page.
- Do not remove the Daily / 7-Day / Month-End separation.
- Do not simplify tables into decorative cards when the table carries operational value.
- Use the visual token system as the starting point.
- Prefer production-ready enterprise layout over experimental visuals.
- The output should be easy to translate into Streamlit layout, CSS, and Matplotlib chart styling later.

Suggested prompt to use with Stitch:

```text
Using this DESIGN.md, create a redesigned enterprise BI dashboard UI for NBS Analytics. Keep the UI dense, professional, and operational. Preserve the Control Center, KPI summary, rankings, drill-down analysis, AI Forecast sections, backtest diagnostics, and export area. Clearly separate Daily Forecast, 7-Day Macro Forecast, and Month-End Macro Forecast. Use the NBS / CTS enterprise cockpit direction with navy navigation, light canvas, blue accents, readable tables, and restrained health badges. Do not change business logic or data meaning.
```

## 14. Implementation Guardrails

This file does not authorize code or data changes.

Do not change:

- SQLite schema
- SQLite data
- Excel ingest logic
- cleaning and exclusion rules
- official revenue scope
- branch or sales group mapping
- ARIMA / Prophet / LightGBM logic
- Fusion logic
- Daily / macro WAPE calculation
- Excel export schema
- cache version
- read-only diagnostic boundaries
- production / diagnostic / experimental labels

Future UI implementation should happen only after reviewing Stitch output and mapping the selected design back to Streamlit safely.

## 15. Current Implementation Anchors

The current Streamlit implementation uses:

- `app.py` for page layout, filters, KPI cards, forecast sections, backtest tables, and downloads.
- `SIDEBAR_NAV_GROUPS` in `app.py` for side menu groups, anchors, labels, badges, and semantic badge kinds.
- `_render_sidebar_navigation()` for the hash-anchor page navigation menu.
- `_sidebar_control_center()` for the separate theme and filter controls.
- `visuals.py` for Matplotlib charts.
- CSS classes such as `nbs-*`, legacy-compatible `pbi-*`, `main-title`, and `section-title`.
- CSS active navigation state should use anchor target behavior rather than a Streamlit query param rerun.
- Existing chart labels:
  - `Daily Forecast：逐日波動預測`
  - `7-Day Macro Forecast：未來 7 日總額（不是自然週）`
  - `Month-End Macro Forecast：MTD + 本月剩餘預測`
- Existing diagnostic section labels:
  - `Data Quality Scorecard：資料品質健康檢查`
  - `Entity Resolution Audit：單號匹配稽核`
  - `AI-assisted Data Cleaning：智能清洗建議`
  - `Forecast Governance：模型健康治理`
  - `Feature Store / Lead Signal：預測特徵與先行信號庫`
  - `Causal Analytics：營收變動解釋`

These anchors are provided so Stitch output can be translated back into the current system later. They are not instructions to edit the system now.
