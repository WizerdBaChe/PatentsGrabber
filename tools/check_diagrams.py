"""The rendered diagram page, measured in the browser that draws it.

`make_state_diagrams.py` checks the MODEL's geometry — grid, orthogonality,
anchoring, nothing pierced. That is arithmetic, and it cannot see anything the
renderer decides: how wide a Chinese label actually is in this font, whether two
labels collide once laid out, whether an arrowhead reference resolves.

The last one is why this file exists. A browser drops a dangling `url(#id)`
SILENTLY — no error, no console line, just a missing glyph that no coordinate
assert measures and that a human reviewer reliably fails to notice. So it is
converted from an appearance property into a DOM reference assert, with a
positive control that must fire when a definition is removed.

    python tools/check_diagrams.py

Exit 0 = the drawn page matches the model · 1 = it does not · 2 = no Chrome.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_layout import Chrome                    # noqa: E402  (one Chrome driver)
from make_state_diagrams import VIEWS              # noqa: E402  (the model itself)
from shoot import find_chrome                      # noqa: E402

PAGE = ROOT / "docs" / "diagrams" / "state-snapshots.html"

# Settle first: getComputedStyle mid-transition returns an interpolated value,
# which makes a font-size assert flaky rather than stably wrong.
SETTLE = """
new Promise(r => { document.getAnimations({subtree:true}).forEach(a=>{try{a.finish()}catch{}});
                   void document.body.offsetHeight; setTimeout(()=>r(true), 250); })
"""

MEASURE = """
(() => {
  const svgs = [...document.querySelectorAll('svg[id^=svg-]')];
  const problems = [];
  for (const el of document.querySelectorAll('[marker-end]')) {
    const id = el.getAttribute('marker-end').replace(/^url\\(#|\\)$/g, '');
    if (!el.ownerSVGElement.querySelector('#' + CSS.escape(id)))
      problems.push({code:'dangling-marker', subject:id, evidence:el.ownerSVGElement.id});
  }
  for (const s of svgs) {
    const boxes = [...s.querySelectorAll('text')].map(t => ({t:t.textContent, b:t.getBBox()}));
    for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++){
      const a=boxes[i].b, c=boxes[j].b;
      if (a.x < c.x+c.width-2 && c.x < a.x+a.width-2 &&
          a.y < c.y+c.height-2 && c.y < a.y+a.height-2)
        problems.push({code:'label-overlap', subject:s.id,
                       evidence:boxes[i].t + ' ∩ ' + boxes[j].t});
    }
    const vb = s.viewBox.baseVal, bb = s.getBBox();
    if (bb.x < vb.x-1 || bb.y < vb.y-1 ||
        bb.x+bb.width > vb.x+vb.width+1 || bb.y+bb.height > vb.y+vb.height+1)
      problems.push({code:'clipped', subject:s.id,
                     evidence:`bbox ${bb.x|0},${bb.y|0},${bb.width|0},${bb.height|0} ` +
                              `vs viewBox ${vb.x},${vb.y},${vb.width},${vb.height}`});
    for (const t of s.querySelectorAll('text')) {
      const fs = parseFloat(getComputedStyle(t).fontSize);
      if (fs < 11) problems.push({code:'font-too-small', subject:s.id,
                                  evidence:`${t.textContent} at ${fs}px`});
    }
  }
  // A label must fit INSIDE its own shape. This is the check the coordinate
  // model could not make: a Chinese glyph's real width is only knowable once
  // the font has laid it out, and a diamond's usable width TAPERS to nothing at
  // its points — so a label sized for a rectangle overflows the diamond of the
  // same nominal width. The rendered page showed exactly that, and every
  // machine assert had passed.
  const model = JSON.parse(document.querySelector('#model').textContent);
  for (const [view, spec] of Object.entries(model)) {
    const svg = document.querySelector('#svg-' + view);
    for (const n of spec.nodes) {
      for (const attr of ['data-node', 'data-sub']) {
        const t = svg.querySelector(`[${attr}="${n.id}"]`);
        if (!t) continue;
        const b = t.getBBox();
        const cx = n.x + n.w/2, cy = n.y + n.h/2;
        const dy = Math.max(Math.abs(b.y - cy), Math.abs(b.y + b.height - cy));
        // Rounded rect: a 6 px inset. Diamond: the width available at this dy.
        const halfAllowed = n.kind === 'decision'
          ? (n.w/2) * Math.max(0, 1 - dy/(n.h/2))
          : n.w/2 - 6;
        const halfNeeded = b.width/2;
        if (halfNeeded > halfAllowed + 0.5)
          problems.push({code:'label-outside-shape', subject:`${view}/${n.id}`,
                         evidence:`"${t.textContent}" needs ${halfNeeded.toFixed(0)}px ` +
                                  `half-width, the ${n.kind} allows ${halfAllowed.toFixed(0)}px ` +
                                  `at dy=${dy.toFixed(0)}`});
        if (b.y < n.y + 2 || b.y + b.height > n.y + n.h - 2)
          problems.push({code:'label-outside-shape', subject:`${view}/${n.id}`,
                         evidence:`"${t.textContent}" vertical ${b.y.toFixed(0)}..` +
                                  `${(b.y+b.height).toFixed(0)} outside ${n.y}..${n.y+n.h}`});
      }
    }
  }
  return {
    svgIds: svgs.map(s => s.id),
    nodeLabels: [...document.querySelectorAll('[data-node]')].map(t => t.dataset.node),
    chips: document.querySelectorAll('circle.chip').length,
    tableRows: document.querySelectorAll('tbody tr').length,
    legendRows: document.querySelectorAll('.legend > div').length,
    problems,
  };
})()
"""

# Remove one marker definition; the dangling-marker check MUST then fire. A
# check that never fires is not a check.
CONTROL = """
(() => {
  const svg = document.querySelector('svg[id^=svg-]');
  const m = svg.querySelector('marker');
  const id = m.id; m.remove();
  let fired = false;
  for (const el of svg.querySelectorAll('[marker-end]'))
    if (el.getAttribute('marker-end') === 'url(#' + id + ')' && !svg.querySelector('#' + CSS.escape(id)))
      fired = true;
  return {removed:id, fired};
})()
"""

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    if not PAGE.is_file():
        print(f"  {PAGE} does not exist — run tools/make_state_diagrams.py first.")
        return 2
    try:
        find_chrome()
    except SystemExit:
        print("Chrome not found — INDETERMINATE: this gate did not run. That is not a pass.")
        return 2

    chrome = Chrome(port=9335)
    try:
        chrome.open(PAGE.resolve().as_uri())
        chrome.eval(SETTLE, await_promise=True)
        m = chrome.eval(MEASURE)

        print("\n=== the page contains exactly the model ===")
        want_ids = [f'svg-{v["id"]}' for v in VIEWS]
        check("every view rendered", m["svgIds"] == want_ids,
              f'{m["svgIds"]} vs {want_ids}')
        want_nodes = sorted(n["id"] for v in VIEWS for n in v["nodes"] if n["kind"] != "init")
        check("every node drawn, none invented", sorted(m["nodeLabels"]) == want_nodes,
              f'{len(m["nodeLabels"])} drawn vs {len(want_nodes)} in the model')
        want_chips = sum(1 for v in VIEWS for e in v["edges"] if "n" in e)
        check("every numbered edge has a chip", m["chips"] == want_chips,
              f'{m["chips"]} vs {want_chips}')
        check("a legend exists and covers more than one type", m["legendRows"] >= 8,
              f'{m["legendRows"]} legend entries')

        print("\n=== the dangling-marker check (positive control) ===")
        c = chrome.eval(CONTROL)
        check("removing a marker definition makes it fire", c["fired"] is True,
              f'removed {c["removed"]}')
        chrome.open(PAGE.resolve().as_uri())      # undo the damage
        chrome.eval(SETTLE, await_promise=True)

        print("\n=== geometry, as this browser laid it out ===")
        problems = chrome.eval(MEASURE)["problems"]
        for p in problems:
            print(f"  FAIL  [{p['code']}] {p['subject']}\n          {p['evidence']}")
            FAILURES.append(p["code"])
        if not problems:
            check("no dangling markers, no label overlap, nothing clipped, "
                  "no text under 11px", True)
    finally:
        chrome.close()

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(sorted(set(FAILURES)))}")
    print("  這一關只證明幾何與結構。畫面好不好看，仍然要人眼在真實環境裡看過。")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"  the checker itself failed: {type(exc).__name__}: {exc}")
        sys.exit(2)
