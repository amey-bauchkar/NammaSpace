# Room sketch template and coordinate-frame instructions

Draw this on paper (one A4 page), photograph it, and put the photo in `measurements/`.

```
            (looking DOWN at the floor from above)

   +-------------------------------------------------+   <- wall: draw every wall, with its corner ids P01, P02, ...
   |                                                  |
   |   [M4]           +--------+                      |   furniture: rectangle, with corner ids (P10..P13)
   |                  | table  |        [M5]          |
   |                  +--------+                      |
   |      [M2]                                        D   <- doorway: mark both side points (P20, P21)
   |                              [M3]                |
   |                                                  |
   |  [M0] ----------------------------> [M1]         |   M0 = origin;  +X = towards M1
   |    |                                             |
   |    v  +Z  (90 deg CLOCKWISE from +X, seen from above; on your RIGHT when you stand on M0 facing M1)
   +-------------------------------------------------+
```

**On the sketch, mark:**
- every marker (M0–M5) and every measured point (P..);
- each doorway, with where it leads;
- glass, mirrors and shiny floor patches (hatch them);
- obstacles below 1.9 m that hang from walls or the ceiling (shelves, AC units, signs);
- "NOT CAPTURED / NOT MEASURED" areas.

**Why +Z is fixed:** the site frame is right-handed with +Y up, so +Z = +X × +Y. Viewed from above, that puts +Z 90° clockwise from +X. Getting this wrong mirrors the map, so the converter derives +Z from the "side" column. You never choose it.
