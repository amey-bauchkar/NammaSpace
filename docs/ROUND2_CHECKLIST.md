# Round 2 submission checklist - POIs & Navigation (deadline 30 Nov 2026)

Official Round 2 deliverables and where each stands in this repository.

| Deliverable | Repository state | What the team still has to do |
|---|---|---|
| **Updated code repository link** (branch with POI + navigation) | POI labelling, search, jump and walking paths are implemented; setup in `README.md` / `docs/INSTALL.md` | Commit, push the branch, put the link in the master PDF |
| **Live web app link** (click POIs, search, follow walkable paths) | The published static site now plans paths **in the browser** (`web/js/routePlan.js`) from the package's `nav/route_grid.json` - no server needed | In the Studio: label places, press **Publish / export** again (re-publishing adds the route grid), upload the folder to a static host (`docs/DEPLOY.md`) |
| **Demonstration video** (3-5 min: search, POI interaction, obstacle-aware path) | Storyboard below | Record from the live link |

## What a visitor can do on the published link

1. **Walk freely**: W A S D + mouse, mini-map.
2. **Click a place marker** to reveal its name; or **search** by name / alias / category (ambiguous queries list the matches).
3. **Go there**: smooth camera transition to a standing spot with a clear view of the place.
4. **Show walking path**: A* on the conservative floor grid - stays on reconstructed floor, keeps body clearance
   (agent radius 0.25 m) from every reconstructed obstacle, no corner cutting. Drawn as a dashed line with direction
   chevrons and a destination pin, also on the mini-map.
5. **Follow it**: live distance remaining while walking, "off the path" hint, arrival notice, and **Walk me there**
   (the camera walks the path; any movement key takes control back).

Paths on published sites are always labelled **unverified**: the floor / obstacle map is automatic, so objects missing
from the reconstruction cannot be ruled out. Inside the Studio the Python planner plus the independent vector
validator are used.

## Evaluation criteria and evidence

| Criterion | Evidence |
|---|---|
| Spatial indexing & POI tagging | `web/js/search.js`, Studio "Add a place label"; POIs travel in the package (`poi/pois.json`, hash-verified) |
| Pathfinding accuracy | `src/namma/navigation/` (conservative rasterisation, planner, independent validator; pytest m05-m07); browser planner tests `web/tests/route_plan.test.js`; export test `tests/test_mesh_import.py::test_package_carries_a_route_grid_for_in_browser_paths` |
| Search UI & transition | `web/js/sceneViewer.js` (`wirePlaces`, `goToSelected`, guidance) |
| Code cleanliness & repository setup | `README.md`, `docs/INSTALL.md`, `docs/CONTRACT_CHANGES.md`, tests: `python -m pytest -q tests`, `node --test web/tests/*.test.js` |

## Video storyboard (3-5 min)

1. (20 s) Open the live link; walk a few steps.
2. (40 s) Click two markers - names appear; search a name; ambiguous search shows choices.
3. (30 s) "Go there" - transition to the place.
4. (60 s) From the far side of the space: "Show walking path" - line bends around furniture / through the doorway;
   show the mini-map; press "Walk me there"; arrival notice.
5. (30 s) Ask for a path to a place with no reachable floor - the honest "unreachable" message.
6. (30 s) Studio: add a label, re-publish.

## Not verified in this change

The browser route display, chevrons and "Walk me there" were unit-tested for logic only (no browser run on the team
laptop, by request). Open the re-published site once and walk one path before recording.

## Added on 16 Nov 2026

- **Search as you type** with category buttons (built from the categories the labels use, e.g. "furniture", "exit")
  and a list of every place when nothing is typed. The camera moves only on Enter / Search or a click.
  Tip: give every label a category in the Studio so the buttons appear.
- **Path quality**: the browser planner prefers the middle of corridors and doorways (obstacle-proximity cost,
  `web/js/routePlan.js`); the distance shown is the real path length.
- **Path title** is now "Walking path – X m"; the status is still `unverified` and the caveat stays in the detail text.
- **Repository**: README covers Round 2, tests run on every push (`.github/workflows/ci.yml`), the personal photo
  folders are no longer tracked (they remain in earlier commits until the history is rewritten).
- Checked by unit tests only (Node: planner, search parity). Not yet seen in a browser.

## Verified in a real browser on 17 Nov 2026 (team laptop, Intel integrated graphics)

Large room (1BHK), with three temporary TEST labels that were removed afterwards. Both the Studio viewer and the
**published static site** (served as plain files, no server code) were checked.

| Feature | Result |
|---|---|
| Places list, category buttons (All / exit / furniture / room) | Works |
| Search as you type; typo tolerance ("entrnce" finds "entrance") | Works |
| Choosing a place | **Changed:** a click or Enter now *selects* the place and turns the view to it without moving. "Go there" flies there; "Show walking path" starts from where the visitor stands. Before, choosing a place flew to it, so the path started at the destination. |
| Walking path across the flat (about 9.5 m) | **Was "unreachable"; fixed.** The only doorway between the two halves is scanned about 50 cm wide, which is too narrow for the 25 cm body radius after the safety margin. Paths now keep 25 cm wherever they can and retry once at an 18 cm minimum, marked "(tight passage)" with the measured gap, and never shown as validated. The independent validator found no obstacle crossed. Studio: 9.5 m; published site (in-browser planner): 9.4 m. |
| Path on the floor with arrows, mini-map line, "x m left" | Works |
| Walk me there | Works. On very slow frames it now keeps its pace (up to 0.25 s per step). |
| Place card buttons | **Fixed:** "AR Walk" was cut off at the right edge of the panel; the row now wraps. |
| AR Walk on a laptop | **Fixed:** with no motion sensors or camera (or permission refused) it no longer stops with an error. It runs as a simulation, with an explanation: Start, then Step walks the path. The banner showed "Walk straight · 4.3 m", then "Bear right · 2.4 m to …"; floor chevrons and the destination beacon appeared; Exit AR returned to the 3D view cleanly. Arrow directions were checked numerically with three.js. |
| Not checked | AR Walk on a real phone (needs https and a phone), and recording the demo video. |

### Demo flow (replaces steps 2–4 of the storyboard)

1. Search a place, or click it in the list: the view turns to it.
2. Click "Show walking path": the path is drawn from where you stand, through the doorway.
3. Click "Walk me there".
4. Round 3 preview: click "AR Walk" (simulation on a laptop; the real thing on a phone over https).
