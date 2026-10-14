"""POI persistence (WP-06): a JSON array of IF-09 records in one file (IF-10 `pois`).

- stable ids: `poi-<slug>`, suffixed -2, -3 ... on collision; never changed on rename;
- every write validates every record (schema + semantics) and is atomic;
- edits that change position reset verification to "unverified" and clear the
  route endpoint (it must be recomputed: poi/endpoints.py).
"""
import copy
import re
import unicodedata

from namma import CONTRACT_VERSION
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, write_json


class POIError(ValueError):
    pass


def slug(name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return s or "poi"


class POIStore:
    def __init__(self, path, site_frame_id):
        self.path = path
        self.site_frame_id = site_frame_id
        self.records = []

    # ------------------------------------------------------------------ persistence
    @classmethod
    def load(cls, path, site_frame_id):
        store = cls(path, site_frame_id)
        data = load_strict(path)
        if not isinstance(data, list):
            raise POIError("POI file must be a JSON array of IF-09 records")
        store.records = data
        store._validate_all()
        return store

    def save(self):
        self._validate_all()
        write_json(self.path, self.records)

    def _validate_all(self):
        ids = [r.get("poi_id") for r in self.records]
        if len(ids) != len(set(ids)):
            raise POIError("duplicate poi_id in store")
        for r in self.records:
            errs = errors_only(validate_document(r, "IF-09", site_frame_id=self.site_frame_id))
            if errs:
                raise POIError(f"{r.get('poi_id')}: " + "; ".join(f"{e.code}: {e.message}" for e in errs))

    # ------------------------------------------------------------------ access
    def get(self, poi_id):
        for r in self.records:
            if r["poi_id"] == poi_id:
                return r
        raise POIError(f"unknown POI {poi_id!r}")

    def by_id(self):
        return {r["poi_id"]: r for r in self.records}

    def new_id(self, display_name):
        base = f"poi-{slug(display_name)}"
        existing = {r["poi_id"] for r in self.records}
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"

    # ------------------------------------------------------------------ editing
    def create(self, display_name, kind, marker_site_m, view_position_site_m, *, author_role, aliases=(),
               category=None, room_poi_id=None, view_target_site_m=None, label_source="human", note=None):
        if not display_name.strip():
            raise POIError("display_name must not be empty")
        now = timefmt.now_iso()
        rec = {"contract": "IF-09", "contract_version": CONTRACT_VERSION, "poi_id": self.new_id(display_name),
               "display_name": display_name, "aliases": list(aliases), "kind": kind, "room_poi_id": room_poi_id,
               "marker_site_m": [float(v) for v in marker_site_m], "frame_id": self.site_frame_id,
               "route_endpoint_xz_m": None, "endpoint_reachable": None,
               "view": {"position_site_m": [float(v) for v in view_position_site_m],
                        "target_site_m": [float(v) for v in (view_target_site_m or marker_site_m)]},
               "label_source": label_source, "verification": {"status": "unverified"},
               "provenance": {"origin": "human_authored", "method": "placed with the POI tool",
                              "author_role": author_role, "created_at": now}}
        if category:
            rec["category"] = category
        if note:
            rec["provenance"]["note"] = note
        errs = errors_only(validate_document(rec, "IF-09", site_frame_id=self.site_frame_id))
        if errs:
            raise POIError("; ".join(e.message for e in errs))
        self.records.append(rec)
        return rec

    def verify(self, poi_id, by_role, *, author_role=None):
        rec = self.get(poi_id)
        creator = rec["provenance"].get("author_role")
        if creator and by_role == creator and creator != "SCRIPT":
            raise POIError("verification must be done by a second person (different role than the author)")
        rec["verification"] = {"status": "verified", "by_role": by_role, "at": timefmt.now_iso()}
        return rec

    def reject(self, poi_id, by_role):
        rec = self.get(poi_id)
        rec["verification"] = {"status": "rejected", "by_role": by_role, "at": timefmt.now_iso()}
        rec["route_endpoint_xz_m"], rec["endpoint_reachable"] = None, None
        return rec

    def update(self, poi_id, /, **changes):
        rec = self.get(poi_id)
        allowed = {"display_name", "aliases", "category", "room_poi_id", "marker_site_m", "view", "kind"}
        bad = set(changes) - allowed
        if bad:
            raise POIError(f"cannot change {sorted(bad)} (poi_id is stable; provenance is managed)")
        before = copy.deepcopy(rec)
        rec.update(copy.deepcopy(changes))
        if rec["marker_site_m"] != before["marker_site_m"] or rec["view"] != before["view"] or \
                rec["display_name"] != before["display_name"]:
            rec["verification"] = {"status": "unverified"}
        if rec["marker_site_m"] != before["marker_site_m"]:
            for k in ("endpoint_geometry_id", "endpoint_navlayer_id", "endpoint_snap_m"):
                rec.pop(k, None)
            rec["route_endpoint_xz_m"], rec["endpoint_reachable"] = None, None
        if rec["provenance"]["origin"] in ("automatic", "synthetic_fixture"):
            rec["provenance"]["note"] = (rec["provenance"].get("note", "") + " | human-corrected").strip(" |")
            rec["provenance"]["origin"] = "human_corrected"
            rec["provenance"].setdefault("author_role", "NAV")
        errs = errors_only(validate_document(rec, "IF-09", site_frame_id=self.site_frame_id))
        if errs:
            self.records[self.records.index(rec)] = before
            raise POIError("; ".join(e.message for e in errs))
        return rec
