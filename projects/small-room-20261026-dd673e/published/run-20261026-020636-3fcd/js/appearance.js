// Replaceable appearance boundary (ARCH-001 Track B, ARCH-010, ARCH-020).
//
//   loadAppearance(scene IF-03, assets, deps) -> THREE.Object3D in the SITE frame
//
// The IF-03 `to_site` matrix is applied here EXACTLY ONCE, on a wrapper group.
// Gaussian-splat loading is deliberately not implemented: the splat plugin
// (Spark vs three.js native) is unresolved until the EXP-005 re-run.

export class AppearanceUnsupported extends Error {
  constructor(message) {
    super(message);
    this.kind = 'appearance_unsupported';
  }
}

export async function loadAppearance(scene, assets, { THREE, GLTFLoader }) {
  if (scene.representation === 'gaussian_splat') {
    throw new AppearanceUnsupported('Gaussian-splat appearance is not supported yet: the splat renderer plugin is unresolved (ARCH-010, EXP-005).');
  }
  if (scene.representation !== 'textured_mesh') {
    throw new AppearanceUnsupported(`Appearance representation "${scene.representation}" is not supported by this viewer.`);
  }
  const lod0 = assets.find((a) => (a.asset.lod_level ?? 0) === 0) || assets[0];
  if (!['glb', 'gltf'].includes(lod0.asset.format)) {
    throw new AppearanceUnsupported(`Mesh format "${lod0.asset.format}" is not supported (expected glb / gltf).`);
  }
  const gltf = await new GLTFLoader().parseAsync(lod0.bytes, '');
  const root = new THREE.Group();
  root.name = 'appearance:to_site';
  const m = scene.to_site.matrix_row_major;
  root.matrixAutoUpdate = false;
  root.matrix.set(...m); // Matrix4.set takes row-major arguments
  root.add(gltf.scene);
  root.updateMatrixWorld(true);
  root.userData = { sceneId: scene.scene_id, isTestAsset: scene.is_test_asset === true, toSiteApplied: 1 };
  return root;
}
