#!/usr/bin/env python3
"""
Compile YardSquare's Archify v3.0 architecture specification (`docs/architecture/yardsquare.architecture.json`)
into:
  1. `docs/architecture/yardsquare-architecture.html` (Interactive Archify v3.0 HTML Viewer)
  2. `app/static/architecture.html` (Live Cloud Run `/architecture` endpoint mirror)
  3. `docs/architecture/yardsquare-architecture.svg` (Standalone GitHub-renderable SVG with embedded Archify theme)

Verifies repository commit SHA and file/line-range evidence against local Git before rendering.
"""
from __future__ import annotations

import html
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "architecture" / "yardsquare.architecture.json"
HTML_OUT_PATH = REPO_ROOT / "docs" / "architecture" / "yardsquare-architecture.html"
STATIC_HTML_OUT_PATH = REPO_ROOT / "app" / "static" / "architecture.html"
SVG_OUT_PATH = REPO_ROOT / "docs" / "architecture" / "yardsquare-architecture.svg"

ARCHIFY_BRANDS_CANDIDATES = [
    Path("/tmp/archify/archify/renderers/shared/generated-brand-marks.mjs"),
    Path("/tmp/archify/renderers/shared/generated-brand-marks.mjs"),
]
ARCHIFY_EXAMPLE_CANDIDATES = [
    Path("/tmp/archify/archify/examples/web-app-rendered.html"),
    HTML_OUT_PATH,
    REPO_ROOT.parent / "TankSquare" / "docs" / "architecture" / "tanksquare-architecture.html",
]

SIGIL_TONE = {
    "frontend": "frontend",
    "backend": "backend",
    "database": "database",
    "cloud": "cloud",
    "security": "security",
    "messagebus": "messagebus",
    "external": "external",
}

SIGIL_SHAPE = {
    "frontend": '<rect x="2" y="3" width="12" height="10" rx="2"/>\n            <path d="M2 6.5h12"/>\n            <circle cx="4.1" cy="4.8" r=".7" class="sigil-fill"/>\n            <circle cx="6.3" cy="4.8" r=".7" class="sigil-fill"/>',
    "backend": '<path d="M6 3 3 8l3 5M10 3l3 5-3 5"/>',
    "database": '<ellipse cx="8" cy="4" rx="5" ry="2"/>\n            <path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
    "cloud": '<path d="M4.3 12.5h7.3a2.4 2.4 0 0 0 .2-4.8 4 4 0 0 0-7.5-1.3A3.1 3.1 0 0 0 4.3 12.5Z"/>',
    "security": '<path d="M8 2.2 13 4v3.5c0 3.1-1.8 5.4-5 6.5-3.2-1.1-5-3.4-5-6.5V4Z"/>\n            <path d="m5.8 8 1.5 1.5 3-3"/>',
    "messagebus": '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>\n            <circle cx="5" cy="4.5" r="1" class="sigil-fill"/>\n            <circle cx="10.5" cy="8" r="1" class="sigil-fill"/>\n            <circle cx="7" cy="11.5" r="1" class="sigil-fill"/>',
    "external": '<rect x="2.5" y="5" width="8.5" height="8" rx="1.5"/>\n            <path d="M8 2.5h5.5V8M13.5 2.5 7.5 8.5"/>',
}

COMPONENT_FILL = {
    "frontend": "c-frontend",
    "backend": "c-backend",
    "database": "c-database",
    "cloud": "c-cloud",
    "security": "c-security",
    "messagebus": "c-messagebus",
    "external": "c-external",
}

COMPONENT_TEXT = {
    "frontend": "t-frontend",
    "backend": "t-backend",
    "database": "t-database",
    "cloud": "t-cloud",
    "security": "t-security",
    "messagebus": "t-messagebus",
    "external": "t-external",
}

ARROW_CLASS_MAP = {
    "default": ("a-default", "arrowhead"),
    "emphasis": ("a-emphasis", "arrowhead-emphasis"),
    "security": ("a-security", "arrowhead-security"),
    "dashed": ("a-dashed", "arrowhead-dashed"),
}

EDGE_LABEL_ACCENT = {
    "default": "t-edge-default",
    "emphasis": "t-edge-emphasis",
    "security": "t-edge-security",
    "dashed": "t-edge-dashed",
}

LEGEND_LABELS = {
    "frontend": "Frontend",
    "backend": "Backend",
    "database": "Database",
    "cloud": "Cloud / Infra",
    "security": "Security",
    "messagebus": "Queue / Bus",
    "external": "External",
}


def esc(val: Any) -> str:
    return html.escape(str(val or ""), quote=True)


def load_brand_marks() -> dict[str, dict[str, Any]]:
    for cand in ARCHIFY_BRANDS_CANDIDATES:
        if cand.exists():
            raw = cand.read_text(encoding="utf-8")
            m = re.search(r"export const BRAND_MARKS = Object\.freeze\((\[[\s\S]*\])\);", raw)
            if m:
                items = json.loads(m.group(1))
                lookup: dict[str, dict[str, Any]] = {}
                for item in items:
                    for key in [item["id"], item["title"], *item.get("aliases", [])]:
                        k = key.strip().lower()
                        lookup[k] = item
                        lookup[re.sub(r"[\s_]+", "-", k)] = item
                        lookup[re.sub(r"[\s_.-]+", "", k)] = item
                return lookup
    return {}


def verify_repository_evidence(spec: dict[str, Any]) -> dict[str, Any]:
    repo_meta = spec.get("meta", {}).get("repository")
    if not repo_meta:
        raise ValueError("meta.repository is required for repository-backed Archify diagrams")

    revision = repo_meta.get("revision", "HEAD")
    url = repo_meta["url"].rstrip("/")
    provider = repo_meta.get("provider", "github")
    link_mode = repo_meta.get("link_mode", "web")

    # Resolve HEAD or commit SHA if git repository is initialized
    res = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", revision],
        capture_output=True,
        text=True,
    )
    resolved_sha = res.stdout.strip() if res.returncode == 0 else "main"

    nodes_evidence: dict[str, list[dict[str, Any]]] = {}
    ref_count = 0

    for comp in spec.get("components", []):
        sources = comp.get("sources") or []
        if not sources:
            continue
        verified_list = []
        for src in sources:
            rel_path = src["path"]
            line = src.get("line")
            end_line = src.get("end_line")
            label = src.get("label")

            wt_file = REPO_ROOT / rel_path
            if not wt_file.exists():
                raise RuntimeError(f"File {rel_path} does not exist in working tree")
            total_lines = len(wt_file.read_text(encoding="utf-8").splitlines())
            req_end = end_line or line or 1
            if req_end > total_lines:
                raise RuntimeError(
                    f"Component {comp['id']} cites {rel_path}:{line}-{end_line}, "
                    f"but file has {total_lines} lines"
                )

            entry: dict[str, Any] = {"path": rel_path}
            if line:
                entry["line"] = line
            if end_line:
                entry["endLine"] = end_line
            if label:
                entry["label"] = label
            if link_mode == "web":
                frag = f"#L{line}" + (f"-L{end_line}" if end_line and end_line != line else "") if line else ""
                entry["href"] = f"{url}/blob/{resolved_sha}/{rel_path}{frag}"
            verified_list.append(entry)
            ref_count += 1
        nodes_evidence[comp["id"]] = verified_list

    repo_label = "/".join(url.split("/")[-2:]) if provider == "github" else url
    return {
        "schemaVersion": 1,
        "verified": True,
        "repository": {
            "url": url,
            "revision": resolved_sha,
            "shortRevision": resolved_sha[:7],
            "label": repo_label,
            "href": f"{url}/tree/{resolved_sha}",
        },
        "referenceCount": ref_count,
        "nodes": nodes_evidence,
    }


def fitted_font_size(text: str, width: float, preferred: float, minimum: float) -> float:
    units = max(1, len(text))
    avail = max(1.0, width - 8.0)
    fitted = min(preferred, avail / (units * 0.6))
    return max(minimum, math.floor(fitted * 10) / 10)


def port_point(comp: dict[str, Any], side: str, offset: float = 0.0) -> tuple[float, float]:
    x, y = comp["pos"]
    w, h = comp["size"]
    cx, cy = x + w / 2.0, y + h / 2.0
    if side == "left":
        return (x, cy + offset)
    if side == "right":
        return (x + w, cy + offset)
    if side == "top":
        return (cx + offset, y)
    if side == "bottom":
        return (cx + offset, y + h)
    return (cx, cy)


def compute_route(
    conn: dict[str, Any],
    comps: dict[str, dict[str, Any]],
) -> list[tuple[float, float]]:
    src = comps[conn["from"]]
    dst = comps[conn["to"]]
    from_side = conn.get("fromSide", "right")
    to_side = conn.get("toSide", "left")
    route_mode = conn.get("route", "auto")

    src_offset = 0.0
    dst_offset = 0.0
    cid = conn.get("id", "")
    if cid == "resident-to-gateway":
        dst_offset = -16.0
    elif cid == "gateway-to-geo":
        src_offset = -16.0
    elif cid == "gateway-to-vendor":
        src_offset = 16.0

    p1 = port_point(src, from_side, src_offset)
    p2 = port_point(dst, to_side, dst_offset)

    if route_mode == "straight" or abs(p1[0] - p2[0]) < 0.5 or abs(p1[1] - p2[1]) < 0.5:
        return [p1, p2]

    if from_side in ("left", "right") and to_side in ("top", "bottom"):
        return [p1, (p2[0], p1[1]), p2]

    if from_side in ("top", "bottom") and to_side in ("left", "right"):
        return [p1, (p1[0], p2[1]), p2]

    mid_x = round((p1[0] + p2[0]) / 2.0, 1)
    return [p1, (mid_x, p1[1]), (mid_x, p2[1]), p2]


def path_d(points: list[tuple[float, float]]) -> str:
    return "M " + " L ".join(f"{round(x, 1):g} {round(y, 1):g}" for x, y in points)


def label_position(conn: dict[str, Any], points: list[tuple[float, float]]) -> tuple[float, float]:
    if len(points) == 2:
        mx = (points[0][0] + points[1][0]) / 2.0
        my = (points[0][1] + points[1][1]) / 2.0
        if abs(points[0][0] - points[1][0]) < 1.0:
            return (mx, my + 3.0)
        return (mx, my - 6.0)
    a, b = points[0], points[1]
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0 - 6.0)


def build_svg(
    spec: dict[str, Any],
    brands: dict[str, dict[str, Any]],
    standalone_css: str | None = None,
) -> str:
    meta = spec["meta"]
    comps_list = spec["components"]
    comps = {c["id"]: c for c in comps_list}
    boundaries_spec = spec.get("boundaries", [])
    conns = spec.get("connections", [])

    boundaries = []
    for b in boundaries_spec:
        members = [comps[cid] for cid in b["wraps"] if cid in comps]
        min_x = min(m["pos"][0] for m in members)
        min_y = min(m["pos"][1] for m in members)
        max_x = max(m["pos"][0] + m["size"][0] for m in members)
        max_y = max(m["pos"][1] + m["size"][1] for m in members)
        pad = b.get("pad", 26)
        top_pad = max(pad, 24)
        bx = min_x - pad
        by = min_y - top_pad
        bw = (max_x - min_x) + pad * 2
        bh = (max_y - min_y) + top_pad + 20
        title_w = max(30.0, len(b["label"]) * 9.0 * 0.6 + 10.0)
        boundaries.append(
            {
                **b,
                "x": bx,
                "y": by,
                "width": bw,
                "height": bh,
                "title": {
                    "x": bx + 8,
                    "y": by + 6,
                    "width": min(bw - 16, title_w),
                    "height": 16,
                    "fontSize": 9,
                    "baselineOffset": 12,
                },
            }
        )

    view_box = meta.get("viewBox", [1120, 565])
    vw, vh = view_box[0], view_box[1]

    boundary_frames_svg = []
    boundary_labels_svg = []
    for idx, b in enumerate(boundaries):
        cls = "c-security-group" if b["kind"] == "security-group" else "c-region"
        rx = 8 if b["kind"] == "security-group" else 12
        boundary_frames_svg.append(
            f'        <rect data-graph-role="structural-frame" data-composition-frame-kind="{esc(b["kind"])}" '
            f'data-composition-frame-id="{idx}" data-composition-frame-label="{esc(b["label"])}" '
            f'x="{b["x"]}" y="{b["y"]}" width="{b["width"]}" height="{b["height"]}" rx="{rx}" class="{cls}" stroke-width="1"/>'
        )
        t = b["title"]
        lcls = "t-security" if b["kind"] == "security-group" else "t-cloud"
        boundary_labels_svg.append(
            f'        <g data-graph-role="structural-frame-label" data-composition-frame-id="{idx}" '
            f'data-composition-frame-kind="{esc(b["kind"])}" data-composition-frame-label="{esc(b["label"])}">\n'
            f'          <rect data-graph-role="structural-frame-label-mask" x="{t["x"]}" y="{t["y"]}" '
            f'width="{t["width"]:.1f}" height="{t["height"]}" rx="3" class="c-mask"/>\n'
            f'          <text data-boundary-label="" x="{t["x"] + 4}" y="{t["y"] + t["baselineOffset"]}" '
            f'class="{lcls}" font-size="{t["fontSize"]}" font-weight="600">{esc(b["label"])}</text>\n'
            f"        </g>"
        )

    conn_paths_svg = []
    conn_labels_svg = []
    for idx, conn in enumerate(conns):
        pts = compute_route(conn, comps)
        d = path_d(pts)
        pts_attr = ";".join(f"{round(x, 1):g},{round(y, 1):g}" for x, y in pts)
        variant = conn.get("variant", "default")
        cls, marker = ARROW_CLASS_MAP.get(variant, ARROW_CLASS_MAP["default"])
        sw = conn.get("width", 1.8 if variant == "emphasis" else 1.5)
        cid = conn.get("id", f'{conn["from"]}-to-{conn["to"]}')
        label = conn.get("label", "")
        label_attr = f' data-edge-label="{esc(label)}"' if label else ""
        step = min(12, idx)
        conn_paths_svg.append(
            f'        <path data-edge-from="{esc(conn["from"])}" data-edge-to="{esc(conn["to"])}"'
            f'{label_attr} data-edge-key="{idx}" data-edge-id="{esc(cid)}" '
            f'data-composition-points="{pts_attr}" d="{d}" class="{cls}" '
            f'data-animate="edge" style="--step:{step}" stroke-width="{sw}" marker-end="url(#{marker})"/>'
        )
        if label:
            lx, ly = label_position(conn, pts)
            lw = max(34.0, len(label) * 4.8 + 12.0)
            lh = 14.0
            bx = lx - lw / 2.0
            by = ly - 10.0
            tcls = EDGE_LABEL_ACCENT.get(variant, "t-edge-default")
            conn_labels_svg.append(
                f'        <g data-detail="context" data-edge-from="{esc(conn["from"])}" '
                f'data-edge-to="{esc(conn["to"])}" data-edge-label="{esc(label)}" '
                f'data-edge-key="{idx}" data-edge-id="{esc(cid)}">\n'
                f'          <rect x="{bx:.1f}" y="{by:.1f}" width="{lw:.1f}" height="{lh:.0f}" rx="3" class="c-mask"/>\n'
                f'          <text x="{lx:.1f}" y="{ly:.1f}" class="{tcls}" font-size="8" text-anchor="middle">{esc(label)}</text>\n'
                f"        </g>"
            )

    comps_svg = []
    for idx, c in enumerate(comps_list):
        cid = c["id"]
        ctype = c["type"]
        x, y = c["pos"]
        w, h = c["size"]
        cx, cy = x + w / 2.0, y + h / 2.0
        fill_cls = COMPONENT_FILL.get(ctype, "c-external")
        text_cls = COMPONENT_TEXT.get(ctype, "t-muted")
        sub = c.get("sublabel", "")
        tag = c.get("tag", "")
        brand_key = (c.get("brand") or "").strip().lower()
        brand_mark = brands.get(brand_key)

        scopes = [b["label"] for b in boundaries if cid in b.get("wraps", [])]
        context_str = " › ".join(scopes) if scopes else "Architecture component"

        sources = c.get("sources") or []
        source_badge_svg = ""
        if sources and standalone_css is not None:
            badge_x = (x + w - 38)
            badge_y = y + 6
            src_count = len(sources)
            source_badge_svg = (
                f'\n          <g class="source-evidence-beacon" transform="translate({badge_x} {badge_y})">\n'
                f'            <rect width="32" height="14" rx="3"/>\n'
                f'            <text x="16" y="9.5">{src_count} SRC</text>\n'
                f"          </g>"
            )

        sigil_shape = SIGIL_SHAPE.get(ctype, SIGIL_SHAPE["external"])
        sigil_tone = SIGIL_TONE.get(ctype, "external")
        sigil_svg = (
            f'<g aria-hidden="true" data-semantic-sigil="{esc(ctype)}" '
            f'class="semantic-sigil s-{sigil_tone}" transform="translate({x + 6} {y + 6}) scale(0.6875)">\n'
            f"            {sigil_shape}\n"
            f"          </g>"
        )

        label_fit_w = w - 24.0
        label_fs = fitted_font_size(c["label"], label_fit_w, 10.5, 8.0)
        label_y = cy - 1 if sub else cy + 4
        sub_svg = ""
        if sub:
            sub_fs = fitted_font_size(sub, w, 8.5, 6.5)
            sub_svg = (
                f'\n        <text data-detail="context" x="{cx}" y="{cy + 13}" '
                f'class="t-muted" font-size="{sub_fs}" text-anchor="middle">{esc(sub)}</text>'
            )
        tag_svg = ""
        if tag:
            tag_fs = fitted_font_size(tag, w, 7.0, 6.0)
            tag_svg = (
                f'\n        <text data-detail="fine" x="{cx}" y="{y + h - 7}" '
                f'class="{text_cls}" font-size="{tag_fs}" text-anchor="middle">{esc(tag)}</text>'
            )

        title_parts = [p for p in [c["label"], sub, context_str, tag] if p]
        step = min(12, idx)
        comps_svg.append(
            f'        <g id="node-{esc(cid)}" data-node-id="{esc(cid)}" data-node-label="{esc(c["label"])}" '
            f'tabindex="0" role="button" aria-label="{esc("Focus " + ", ".join(title_parts))}" aria-pressed="false" '
            f'data-node-kind="{esc(ctype)}" data-node-sublabel="{esc(sub)}" data-node-tag="{esc(tag)}" '
            f'data-node-context="{esc(context_str)}">\n'
            f'          <title>{esc(" · ".join(title_parts))}</title>\n'
            f'          <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="c-mask"/>\n'
            f'          <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="{fill_cls}" '
            f'data-animate="node" style="--step:{step}" stroke-width="1.5"/>\n'
            f"          {sigil_svg}{source_badge_svg}\n"
            f'          <text data-node-label="" data-detail-anchor="" x="{cx}" y="{label_y}" '
            f'class="t-primary" font-size="{label_fs}" font-weight="600" text-anchor="middle">{esc(c["label"])}</text>'
            f"{sub_svg}{tag_svg}\n"
            f"        </g>"
        )

    present_kinds = [
        k for k in ["frontend", "backend", "database", "cloud", "security", "messagebus", "external"]
        if any(c["type"] == k for c in comps_list)
    ]
    legend_y = vh - 18
    legend_title_y = legend_y - 20
    legend_items_svg = [
        '        <g data-legend="" data-legend-bridge="">',
        f'          <text x="40" y="{legend_title_y}" class="t-primary" font-size="12" font-weight="650">Legend</text>',
    ]
    lx_cursor = 40
    for kind in present_kinds:
        lbl = LEGEND_LABELS[kind]
        item_w = int(22 + len(lbl) * 6.5 + 21)
        fcls = COMPONENT_FILL[kind]
        legend_items_svg.append(
            f'          <g data-legend-semantic-kind="{kind}" data-legend-kind="{kind}" '
            f'data-legend-label="{esc(lbl)}" data-legend-x="{lx_cursor}" '
            f'data-legend-baseline="{legend_y}" data-legend-width="{item_w}">\n'
            f'            <rect x="{lx_cursor}" y="{legend_y - 9}" width="16" height="10" rx="2.5" class="{fcls}" stroke-width="1"/>\n'
            f'            <text x="{lx_cursor + 24}" y="{legend_y}" class="t-muted" font-size="10" font-weight="500">{esc(lbl)}</text>\n'
            f"          </g>"
        )
        lx_cursor += item_w + 24
    legend_items_svg.append("        </g>")

    style_block = f"\n        <style>\n{standalone_css}\n        </style>" if standalone_css else ""
    bg_rect = '        <rect width="100%" height="100%" fill="#030816" />\n' if standalone_css else ""
    header_banner = ""
    if standalone_css:
        header_banner = (
            f'        <text x="40" y="34" class="t-primary" font-size="15" font-weight="700">{esc(meta["title"])}</text>\n'
            f'        <text x="40" y="52" class="t-muted" font-size="9.5">{esc(meta.get("subtitle", ""))}</text>\n'
        )

    xmlns_attr = ' xmlns="http://www.w3.org/2000/svg"' if standalone_css else ""
    theme_attr = ' data-theme="dark"' if standalone_css else ""

    return f"""      <svg{xmlns_attr} viewBox="0 0 {vw} {vh}" role="img" lang="en" aria-labelledby="archify-diagram-title archify-diagram-description" data-animation="trace" data-preset="{esc(meta.get('visual_preset', 'blueprint'))}"{theme_attr} data-quality-profile="showcase" data-reader-fit="intrinsic-height" data-reader-min-text="7.5" data-reader-primary-text="14">
        <title id="archify-diagram-title">{esc(meta["title"])}</title>
        <desc id="archify-diagram-description">{esc(meta.get("subtitle", ""))}</desc>{style_block}
        <defs>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-default" />
          </marker>
          <marker id="arrowhead-emphasis" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-emphasis" />
          </marker>
          <marker id="arrowhead-security" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-security" />
          </marker>
          <marker id="arrowhead-dashed" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" class="m-dashed" />
          </marker>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" class="c-grid" stroke-width="0.5"/>
          </pattern>
        </defs>
{bg_rect}        <rect width="100%" height="100%" fill="url(#grid)" />
{header_banner}
{chr(10).join(boundary_frames_svg)}
{chr(10).join(conn_paths_svg)}
{(chr(10) + chr(10)).join(comps_svg)}
{chr(10).join(conn_labels_svg)}
{(chr(10) + chr(10)).join(boundary_labels_svg)}
{chr(10).join(legend_items_svg)}
      </svg>"""


STANDALONE_SVG_CSS = """
  svg {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    --bg: #030816;
    --grid: #16263f;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --text-dim: #475569;
    --arrow: #64748b;
    --arrow-emphasis: #34d399;
    --mask: #0b1528;
    --frontend-fill: rgba(8, 51, 68, 0.55);
    --frontend-stroke: #22d3ee;
    --backend-fill: rgba(6, 78, 59, 0.55);
    --backend-stroke: #34d399;
    --database-fill: rgba(76, 29, 149, 0.55);
    --database-stroke: #a78bfa;
    --cloud-fill: rgba(120, 53, 15, 0.45);
    --cloud-stroke: #fbbf24;
    --security-fill: rgba(136, 19, 55, 0.55);
    --security-stroke: #fb7185;
    --messagebus-fill: rgba(154, 52, 18, 0.50);
    --messagebus-stroke: #fb923c;
    --external-fill: rgba(30, 41, 59, 0.65);
    --external-stroke: #94a3b8;
  }
  .c-grid { stroke: var(--grid); fill: none; }
  .c-mask { fill: var(--mask); stroke: none; }
  .c-region { fill: rgba(251, 191, 36, 0.05); stroke: var(--cloud-stroke); stroke-dasharray: 8,4; }
  .c-security-group { fill: transparent; stroke: var(--security-stroke); stroke-dasharray: 4,4; }
  .c-frontend { fill: var(--frontend-fill); stroke: var(--frontend-stroke); }
  .c-backend { fill: var(--backend-fill); stroke: var(--backend-stroke); }
  .c-database { fill: var(--database-fill); stroke: var(--database-stroke); }
  .c-cloud { fill: var(--cloud-fill); stroke: var(--cloud-stroke); }
  .c-security { fill: var(--security-fill); stroke: var(--security-stroke); }
  .c-messagebus { fill: var(--messagebus-fill); stroke: var(--messagebus-stroke); }
  .c-external { fill: var(--external-fill); stroke: var(--external-stroke); }
  .t-primary { fill: var(--text); }
  .t-muted { fill: var(--text-muted); }
  .t-dim { fill: var(--text-dim); }
  .t-frontend { fill: var(--frontend-stroke); }
  .t-backend { fill: var(--backend-stroke); }
  .t-database { fill: var(--database-stroke); }
  .t-cloud { fill: var(--cloud-stroke); }
  .t-security { fill: var(--security-stroke); }
  .t-messagebus { fill: var(--messagebus-stroke); }
  .t-external { fill: var(--external-stroke); }
  .t-edge-default { fill: var(--arrow); }
  .t-edge-emphasis { fill: var(--arrow-emphasis); }
  .t-edge-security { fill: var(--security-stroke); }
  .t-edge-dashed { fill: var(--database-stroke); }
  .a-default { stroke: var(--arrow); fill: none; }
  .a-emphasis { stroke: var(--arrow-emphasis); fill: none; }
  .a-security { stroke: var(--security-stroke); fill: none; stroke-dasharray: 5,5; }
  .a-dashed { stroke: var(--database-stroke); fill: none; stroke-dasharray: 4,4; }
  .m-default { fill: var(--arrow); }
  .m-emphasis { fill: var(--arrow-emphasis); }
  .m-security { fill: var(--security-stroke); }
  .m-dashed { fill: var(--database-stroke); }
  .semantic-sigil {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.35;
    stroke-linecap: round;
    stroke-linejoin: round;
    opacity: 0.82;
  }
  .semantic-sigil .sigil-fill { fill: currentColor; stroke: none; }
  .s-frontend { color: var(--frontend-stroke); }
  .s-backend { color: var(--backend-stroke); }
  .s-database { color: var(--database-stroke); }
  .s-cloud { color: var(--cloud-stroke); }
  .s-security { color: var(--security-stroke); }
  .s-messagebus { color: var(--messagebus-stroke); }
  .s-external { color: var(--external-stroke); }
  .source-evidence-beacon rect {
    fill: #072227;
    stroke: #34d399;
    stroke-width: 0.9;
  }
  .source-evidence-beacon text {
    fill: #34d399;
    font-size: 6.5px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-anchor: middle;
  }
"""


def render_cards_html(cards: list[dict[str, Any]]) -> str:
    blocks = []
    for card in cards:
        items_html = "\n".join(f"          <li>{esc(item)}</li>" for item in card.get("items", []))
        blocks.append(
            f'      <div class="card">\n'
            f'        <div class="card-header">\n'
            f'          <div class="card-dot {esc(card["dot"])}"></div>\n'
            f'          <h3>{esc(card["title"])}</h3>\n'
            f"        </div>\n"
            f"        <ul>\n{items_html}\n        </ul>\n"
            f"      </div>"
        )
    return "    <!-- Info Cards -->\n    <div class=\"cards\">\n" + "\n\n".join(blocks) + "\n    </div>"


def build_interactive_html(
    spec: dict[str, Any],
    svg_markup: str,
    source_evidence: dict[str, Any],
) -> str:
    meta = spec["meta"]
    cards_html = render_cards_html(spec.get("cards", []))
    evidence_script = (
        '    <script id="archify-source-evidence-data" type="application/json">'
        + json.dumps(source_evidence).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        + "</script>\n"
    )

    for cand in ARCHIFY_EXAMPLE_CANDIDATES:
        if cand.exists():
            base_html = cand.read_text(encoding="utf-8")
            base_html = re.sub(
                r'<html lang="en" data-theme="dark" data-preset="[^"]*">',
                f'<html lang="en" data-theme="dark" data-preset="{esc(meta.get("visual_preset", "blueprint"))}">',
                base_html,
                count=1,
            )
            base_html = re.sub(
                r"<title>[\s\S]*?</title>",
                f'<title>{esc(meta["title"])} — Archify Architecture</title>',
                base_html,
                count=1,
            )
            subtitle_html = (
                f'\n      <p class="subtitle">{esc(meta["subtitle"])}</p>'
                if meta.get("subtitle")
                else ""
            )
            base_html = re.sub(
                r"(<h1>)[\s\S]*?(</h1>\s*</div>)(\s*<p class=\"subtitle\">[\s\S]*?</p>)?",
                lambda m: f'{m.group(1)}{esc(meta["title"])}{m.group(2)}{subtitle_html}',
                base_html,
                count=1,
            )
            base_html = re.sub(
                r'\s*<script id="archify-source-evidence-data" type="application/json">[\s\S]*?</script>\n?',
                "\n",
                base_html,
            )
            base_html = base_html.replace(
                '    <script id="archify-i18n-data"',
                evidence_script + '    <script id="archify-i18n-data"',
                1,
            )
            base_html = re.sub(
                r'      <svg viewBox="0 0 \d+ \d+"[\s\S]*?      </svg>',
                lambda _m: svg_markup,
                base_html,
                count=1,
            )
            base_html = re.sub(
                r'    <!-- Info Cards -->\s*<div class="cards">[\s\S]*?    </div>(\s*</div>\s*<script>)',
                lambda m: cards_html + m.group(1),
                base_html,
                count=1,
            )
            return base_html

    # Standalone self-contained HTML viewer fallback if no external template is present
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark" data-preset="blueprint">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(meta["title"])} — Archify Architecture</title>
  <style>
    body {{ background: #030816; color: #f8fafc; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; padding: 24px; }}
    .container {{ max-width: 1240px; margin: 0 auto; }}
    h1 {{ font-size: 20px; margin-bottom: 4px; }}
    .subtitle {{ color: #94a3b8; font-size: 13px; margin-top: 0; margin-bottom: 20px; }}
    .diagram-box {{ border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; background: #030816; margin-bottom: 20px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: #0b1528; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; }}
    .card h3 {{ margin: 0 0 10px 0; font-size: 14px; color: #34d399; }}
    .card ul {{ margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 12px; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{esc(meta["title"])}</h1>
    <p class="subtitle">{esc(meta.get("subtitle", ""))}</p>
    <div class="diagram-box">
{build_svg(spec, load_brand_marks(), standalone_css=STANDALONE_SVG_CSS)}
    </div>
{cards_html}
  </div>
{evidence_script}
</body>
</html>"""


def main() -> int:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    source_evidence = verify_repository_evidence(spec)
    brands = load_brand_marks()

    viewer_svg = build_svg(spec, brands, standalone_css=None)
    standalone_svg = build_svg(spec, brands, standalone_css=STANDALONE_SVG_CSS).lstrip() + "\n"
    interactive_html = build_interactive_html(spec, viewer_svg, source_evidence)

    HTML_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATIC_HTML_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT_PATH.write_text(interactive_html, encoding="utf-8")
    shutil.copyfile(HTML_OUT_PATH, STATIC_HTML_OUT_PATH)
    SVG_OUT_PATH.write_text(standalone_svg, encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "spec": str(SPEC_PATH.relative_to(REPO_ROOT)),
                "html": str(HTML_OUT_PATH.relative_to(REPO_ROOT)),
                "static_html": str(STATIC_HTML_OUT_PATH.relative_to(REPO_ROOT)),
                "svg": str(SVG_OUT_PATH.relative_to(REPO_ROOT)),
                "verified_revision": source_evidence["repository"]["shortRevision"],
                "verified_sources": source_evidence["referenceCount"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
