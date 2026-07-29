# Generates the Zeus architecture diagram (light + dark) as SVG.
# Design intent: the hero is the CLOSED LOOP, not the pipeline. A reader who
# looks for 5 seconds should see that settlement feeds back into calibration
# through a filter — that is what makes this a system rather than a script.

THEMES = {
 "light": dict(bg="#FFFFFF", panel="#F6F7F9", stroke="#D2D6DC", text="#111418",
               muted="#5B6470", accent="#1F6FEB", accent_soft="#DBEAFE",
               gate="#B45309", gate_soft="#FEF3C7", edge="#8A929E"),
 "dark":  dict(bg="#0D1117", panel="#161B22", stroke="#30363D", text="#E6EDF3",
               muted="#9198A1", accent="#58A6FF", accent_soft="#0D2C57",
               gate="#E3B341", gate_soft="#3A2E10", edge="#6E7681"),
}

W, H = 1240, 600
FS = "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
FM = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def box(x, y, w, h, title, lines, t, accent=False, small=False):
    fill = t["accent_soft"] if accent else t["panel"]
    stk  = t["accent"] if accent else t["stroke"]
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stk}" stroke-width="1.25"/>']
    ty = y + (20 if small else 24)
    o.append(f'<text x="{x+14}" y="{ty}" font-family="{FS}" font-size="{12 if small else 13}" font-weight="650" '
             f'letter-spacing="0.4" fill="{t["text"]}">{esc(title)}</text>')
    ly = ty + (15 if small else 18)
    for ln in lines:
        o.append(f'<text x="{x+14}" y="{ly}" font-family="{FM}" font-size="10.5" fill="{t["muted"]}">{esc(ln)}</text>')
        ly += 14
    return "".join(o)

def arrow(x1, y1, x2, y2, t, dashed=False):
    d = ' stroke-dasharray="4 3"' if dashed else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["edge"]}" '
            f'stroke-width="1.4"{d} marker-end="url(#ah)"/>')

def build(theme):
    t = THEMES[theme]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="Zeus architecture: forecast to probability to edge to size to execution to settlement, with a filtered feedback loop into calibration">',
         f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
         f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{t["edge"]}"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>']

    o.append(f'<text x="40" y="46" font-family="{FS}" font-size="19" font-weight="700" fill="{t["text"]}">'
             f'Zeus — one cycle</text>')
    o.append(f'<text x="40" y="68" font-family="{FS}" font-size="12.5" fill="{t["muted"]}">'
             f'Daily high/low temperature markets, 54 cities. The cycle repeats; every stage re-decides on new data.</text>')

    # --- sources -----------------------------------------------------------
    sx, sy = 40, 100
    o.append(f'<text x="{sx}" y="{sy-8}" font-family="{FS}" font-size="10.5" font-weight="650" letter-spacing="1.1" fill="{t["muted"]}">SOURCES</text>')
    o.append(box(sx, sy,      196, 56, "NWP ensembles", ["ECMWF · ICON · NOAA", "UKMO · GEM"], t, small=True))
    o.append(box(sx, sy+68,   196, 56, "Official stations", ["Hong Kong Observatory", "Taiwan CWA · METAR"], t, small=True))
    o.append(box(sx, sy+136,  196, 56, "Venue", ["Polymarket WebSocket", "book · fills · settlement"], t, small=True))

    # --- pipeline ----------------------------------------------------------
    px, py, pw, ph, gap = 276, 100, 216, 124, 22
    o.append(f'<text x="{px}" y="{py-8}" font-family="{FS}" font-size="10.5" font-weight="650" letter-spacing="1.1" fill="{t["muted"]}">DECIDE</text>')
    stages = [
      ("1  FORECAST", ["empirical-Bayes de-bias", "inverse-variance fusion", "Ledoit–Wolf shrinkage", "station localization"]),
      ("2  PROBABILITY", ["integrate over the", "PREIMAGE of each city's", "rounding rule", "condition on observed"]),
      ("3  EDGE", ["bootstrap lower bound", "Wilson selection calib.", "cost incl. taker fee", "Benjamini–Hochberg FDR"]),
      ("4  SIZE", ["fractional Kelly", "multiplicative damping", "coverage · width · lead", "missing input → zero"]),
    ]
    for i,(ti,ls) in enumerate(stages):
        x = px + i*(pw+gap)
        o.append(box(x, py, pw, ph, ti, ls, t, accent=(i==1)))
        if i: o.append(arrow(x-gap+2, py+ph/2, x-3, py+ph/2, t))
    o.append(arrow(sx+196+3, py+ph/2, px-3, py+ph/2, t))

    # --- act ---------------------------------------------------------------
    ay = py + ph + 66
    o.append(f'<text x="{px}" y="{ay-8}" font-family="{FS}" font-size="10.5" font-weight="650" letter-spacing="1.1" fill="{t["muted"]}">ACT</text>')
    o.append(box(px, ay, 336, 100, "5  EXECUTE", [
        "post-only maker rest → fill-or-kill taker cross on deadline",
        "idempotency-keyed intent written BEFORE the venue is called",
        "separate exit state machine · hourly chain reconciliation"], t))
    o.append(box(px+336+30, ay, 336, 100, "6  SETTLE & ATTRIBUTE", [
        "forecast-earned win · lucky win · foreseeable loss",
        "miscalibration loss · stale-data decision · unattributable",
        "the probability sized on is frozen at decision time"], t, accent=True))
    o.append(arrow(px+336+3, ay+50, px+336+27, ay+50, t))
    o.append(f'<line x1="{px+2*(pw+gap)+pw/2}" y1="{py+ph}" x2="{px+2*(pw+gap)+pw/2}" y2="{ay-14}" stroke="{t["edge"]}" stroke-width="1.4" marker-end="url(#ah)"/>')

    # --- feedback ----------------------------------------------------------
    gx, gy = 986, ay - 8
    o.append(box(gx, py, 214, ph, "CALIBRATION", [
        "per-source settled", "residuals · selection", "cells · sigma floors",
        "", "consumes ONLY settled", "outcomes"], t))

    fy = ay + 132
    x_from = px+336+30+336/2
    o.append(f'<path d="M {x_from} {ay+100} L {x_from} {fy} L {gx+107} {fy} L {gx+107} {py+ph+8}" '
             f'fill="none" stroke="{t["gate"]}" stroke-width="1.6" marker-end="url(#ah)"/>')
    o.append(f'<rect x="{gx+107-150}" y="{fy-17}" width="300" height="34" rx="17" fill="{t["gate_soft"]}" stroke="{t["gate"]}" stroke-width="1.25"/>')
    o.append(f'<text x="{gx+107}" y="{fy+4.5}" text-anchor="middle" font-family="{FS}" font-size="12" font-weight="650" fill="{t["gate"]}">'
             f'gate: skill outcomes only</text>')
    o.append(f'<text x="{x_from+16}" y="{fy-12}" font-family="{FS}" font-size="11.5" fill="{t["muted"]}">'
             f'luck is graded out before it can train the model</text>')

    o.append(f'<line x1="{gx-3}" y1="{py+ph/2}" x2="{px+3*(pw+gap)+pw+3}" y2="{py+ph/2}" stroke="{t["edge"]}" stroke-width="1.4" marker-end="url(#ah)" transform="translate(0,0)"/>')
    o.append(f'<text x="{40}" y="{H-24}" font-family="{FM}" font-size="10.5" fill="{t["muted"]}">'
             f'Every stage is re-evaluated each cycle: a resting order whose edge faded is pulled and decided again; a fresh forecast on a held market is itself new information.</text>')
    o.append('</svg>')
    return "".join(o)

for name in THEMES:
    open(f"architecture-{name}.svg","w").write(build(name))
print("ok")
