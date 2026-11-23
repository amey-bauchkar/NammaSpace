"""Verify the provenance highlight on a package that actually has estimated surfaces."""
import argparse
import json
from pathlib import Path

from cdp import Chrome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with Chrome(headless=True) as c:
        c.navigate(args.url)
        c.wait_for("Boolean(window.__nammaScene && window.__nammaScene.ctx && window.__nammaScene.ctx.estimated)", timeout=240)
        inspect = "document.getElementById('inspect-estimates')"
        state = """(() => {
          const group = window.__nammaScene.ctx.estimated, materials = [];
          group.traverse(o => { if (o.isMesh) materials.push({colour: o.material.color.getHex(), vertex: o.material.vertexColors}); });
          return {visible: group.visible, materials, pressed: document.getElementById('inspect-estimates').getAttribute('aria-pressed'),
                  legend: !document.getElementById('estimate-legend').hidden};
        })()"""
        before = c.eval(state)
        c.eval(inspect + '.click()')
        highlighted = c.eval(state)
        c.pump(.5)
        c.screenshot(args.out / 'highlighted.png')
        c.eval(inspect + '.click()')
        restored = c.eval(state)
        checks = {
            'hidden_by_default': not before['visible'],
            'highlight_visible': highlighted['visible'] and highlighted['legend'] and highlighted['pressed'] == 'true',
            'orange_layer': bool(highlighted['materials']) and all(m == {'colour': 0xff8c00, 'vertex': False} for m in highlighted['materials']),
            'colours_restored': restored['materials'] == before['materials'] and restored['pressed'] == 'false' and not restored['legend'],
            'no_exceptions': not c.exceptions,
        }
        result = {'checks': checks, 'passed': all(checks.values()), 'before': before,
                  'highlighted': highlighted, 'restored': restored, 'exceptions': c.exceptions,
                  'window': 'headless', 'url': args.url}
        (args.out / 'estimated_surfaces_e2e.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'checks': checks, 'passed': result['passed']}))
        return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
