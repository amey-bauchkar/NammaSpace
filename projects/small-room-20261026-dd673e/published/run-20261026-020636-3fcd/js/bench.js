// EXP-005 frame-time benchmark (index.html?bench=1[&seconds=20]).
// Drives a scripted walk + look-around with requestAnimationFrame and records every frame
// interval. A run is VALID only if the page stayed visible and frames were actually delivered
// (the Phase 8 attempt had 0 frames because the browser pane was hidden).
import { forwardFromAngles } from './frames.js';

const pct = (arr, p) => {
  const s = [...arr].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] : null;
};

export function runBenchmark(app, seconds = 20, timing = {}) {
  const { viewer } = app;
  const c = viewer.controller;
  const start = viewer.release_start_pose || { position: [1.0, 1.6, 1.0] };
  const intervals = [];
  let hiddenAt = null;
  const onVis = () => { if (document.visibilityState !== 'visible' && hiddenAt === null) hiddenAt = performance.now(); };
  document.addEventListener('visibilitychange', onVis);
  cancelAnimationFrame(viewer.raf);              // take over the render loop
  return new Promise((resolve) => {
    const t0 = performance.now();
    let last = t0;
    const p0 = [...start.position];
    const step = (now) => {
      const t = (now - t0) / 1000;
      intervals.push(now - last);
      last = now;
      // scripted path: slow circle of radius 1 m while turning (deterministic, collision off)
      c.collision = false;
      c.yaw = t * 0.6;
      c.pitch = -0.15 + 0.1 * Math.sin(t);
      viewer.camera.position.set(p0[0] + Math.cos(t * 0.3), p0[1], p0[2] + Math.sin(t * 0.3));
      c.apply();
      viewer.render();
      if (t < seconds) requestAnimationFrame(step);
      else {
        document.removeEventListener('visibilitychange', onVis);
        c.collision = true;
        const frames = intervals.slice(1);
        const visible = hiddenAt === null && document.visibilityState === 'visible';
        const expectedMin = seconds * 10; // fewer than 10 fps worth of callbacks = throttled / hidden
        const info = viewer.renderer.info.render;
        const gl = viewer.renderer.getContext();
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        const res = {
          valid: visible && frames.length >= expectedMin,
          invalid_reason: !visible ? 'page not visible during run' : frames.length < expectedMin ? `only ${frames.length} frames delivered` : null,
          seconds, frames: frames.length,
          frame_ms: { median: pct(frames, 50), p95: pct(frames, 95), max: Math.max(...frames), mean: frames.reduce((a, b) => a + b, 0) / frames.length },
          over_33ms_share: frames.filter((f) => f > 33.4).length / frames.length,
          triangles_last_frame: info.triangles, draw_calls_last_frame: info.calls,
          canvas_px: [viewer.renderer.domElement.width, viewer.renderer.domElement.height],
          device_pixel_ratio: window.devicePixelRatio,
          gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
          js_heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 2 ** 20) : null,
          user_agent: navigator.userAgent, timing, at: new Date().toISOString(),
          forward_check: forwardFromAngles(c.yaw, c.pitch).map((v) => +v.toFixed(3)),
        };
        res.release_id = app.release.manifest.release_id;
        res.page_url = location.href;
        window.__nammaBench = res;
        // best effort: the local dev server (scripts/serve.py --record) stores the result; static hosts ignore it
        fetch('/__bench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(res) }).catch(() => {});
        viewer.start();
        resolve(res);
      }
    };
    requestAnimationFrame(step);
  });
}
