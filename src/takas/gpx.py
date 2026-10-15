from __future__ import annotations

from xml.sax.saxutils import escape


def write_gpx(name: str, coords, elev=None, desc: str = "", walk: dict | None = None) -> str:
    """GPX 1.1 with a single <trk>; coords are [lat, lon] pairs. A walk is prepended (it ends where coords start)."""
    if walk:
        coords = [*walk["coords"], *coords[1:]]
        elev = [*walk["elev"], *elev[1:]] if elev is not None else None
    pts = []
    for i, (la, lo) in enumerate(coords):
        ele = f"<ele>{elev[i]:.1f}</ele>" if elev is not None else ""
        pts.append(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}">{ele}</trkpt>')
    d = f"<desc>{escape(desc)}</desc>" if desc else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="takas" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"<metadata><name>{escape(name)}</name></metadata>\n"
        f"<trk><name>{escape(name)}</name>{d}<trkseg>\n" + "\n".join(pts) + "\n</trkseg></trk>\n</gpx>\n"
    )
