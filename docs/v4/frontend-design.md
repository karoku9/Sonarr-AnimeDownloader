# AniDown V4 frontend design authority

Owner: V4 frontend maintainers. Scope: new frontend-v4 only; API authority remains frontend-api-contract.md. Drift check: frontend unit/runtime checks and UI review on any token/layout/interaction change. This document supersedes only historical unimplemented UI status in ui-spec.md; it does not change matcher policy.

## Direction reviewed before implementation
Local media association software, designed for reading episode plans and making accountable decisions. Spend visual emphasis on the release-to-episode track and on the review's concrete unresolved question. No posters or anime-themed decoration, metric-card grid, gradients or normal raw URLs.

Palette: canvas #181b20, navigation #15181c, surface #20242a, raised #282d35, text #eef0f3, muted #aeb7c4. Borders #424b58. Accent #a9c5ec with dark text #172333; warning #e9c180, success #9ecab1, danger #efaaa4. Normal text contrast is measured on actual surfaces.

Typography: Segoe UI Variable / Segoe UI, locally available Windows application type, system fallbacks; 14px body, 13px metadata, 20px section, 30px page. No external fonts, uppercase eyebrows or monospace outside Advanced. Numeric counts use tabular figures.

Layout: 216px persistent navigation, shared left-aligned main gutter 40px and content max1120px. Library is a ruled expandable list; Review has compact queue and one detailed decision. Dashboard uses a status sentence and two task rows. Mobile navigation becomes a compact wrapped menu, all content stays in viewport. Spacing 4/8/12/16/24/32/40/48px; radii4/8px, one primary button hierarchy.

Library sample:
```
Library                       [Search] [State]
Black Lagoon     Season 1     Proposed     24 episodes     DUB
  Season 1 / 24 episodes
  Black Lagoon                episodes 1–12
  The Second Barrage          episodes 13–24
  [Review proposal]            > Advanced (closed)
```
Review sample:
```
Needs Review
Queue                         Sailor Moon Crystal / Seasons 1+2
                              26 source episodes fit the combined total,
                              but the 14/12 boundary is not verified.
                              Plan / key evidence / supported alternatives
                              [Approve] [Choose alternative] [Reject]
                              > Secondary actions / Advanced (closed)
```
These plans were critiqued against the brief: remove repeated metric panels, unneeded decorative icons and technical coordinates; keep the destination episode ranges that explain composite plans. Review concrete first two screens before completing remaining pages.

## Quality gates
Frontend Design: intentional local software typography, quiet chrome, episode track as identity, concise copy. UI/UX Pro Max: relevant flat utility style and keyboard-focus web guidelines; off-topic marketing output not adopted. Web Interface Guidelines (vercel skill v1.0.0, fresh source URL): semantic navigation/forms, focus, live async updates, native dialog, deep-link filters/details, narrow viewport, reduced motion, safe text rendering.
