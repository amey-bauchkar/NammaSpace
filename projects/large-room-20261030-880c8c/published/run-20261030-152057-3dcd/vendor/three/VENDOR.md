# Vendored three.js (provisional engine, ARCH-009 / ARCH-027)

- Package: `three` **0.186.1** (REVISION 186), licence MIT (`LICENSE` in this folder).
- Source: a local copy already on this machine,
  `C:\Users\SEBIN\Desktop\foremark4\node_modules\three\` (copied 2026-10-10).
  Nothing was downloaded during Phase 11.
- Equivalent npm pin: `"three": "0.186.1"` (see `package.json`).

| File | Origin in the package | SHA-256 |
|---|---|---|
| `three.module.js` | `build/three.module.js` | `9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea` |
| `three.core.js` | `build/three.core.js` | `9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6` |
| `addons/loaders/GLTFLoader.js` | `examples/jsm/loaders/GLTFLoader.js` | `131c0f78c01d19368ae495caa65b3adaa10487810a36a05bb5901b769a35ac16` |
| `addons/utils/BufferGeometryUtils.js` | `examples/jsm/utils/BufferGeometryUtils.js` | `9fb63427ce6641fa14fd0baff9cc4d1b5f9c3d85fd084bf2e90e803c44ec1797` |
| `addons/utils/SkeletonUtils.js` | `examples/jsm/utils/SkeletonUtils.js` | `b1632a703206c3d830de9fcbe515696770d04b71a15ee6b50afa6d2c3298c86f` |
| `LICENSE` | `LICENSE` | `8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc` |

The page resolves `three` and `three/addons/` with an import map (`web/index.html`).
No Gaussian-splat plugin is vendored: ARCH-010 remains open.
