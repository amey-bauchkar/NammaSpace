# Fidelity programme checkout differences

The supplied handoff and repository copy have the same SHA-256. The checkout
starts at 79cca81, later than the audited 0c15121. The trainer's PLY export and
summary still match the described data flow. Studio resolves project options in
splat_large.splat_options. The expected tools/splat-env/Scripts/python.exe is
absent. Windows reports Intel(R) Graphics. The corridor archive is present.

Python 3.14 resolves an installed site-packages/tests package ahead of this
checkout's original namespace directory. Five baseline test modules could not
collect. A local test package and compatibility path restore both fixture
import styles without changing assertions.

## Texture fidelity correction

The old blend filter marked observed dark, saturated, and bright texels invalid.
It could copy colours from unrelated or distant geometry and add random grain.
The blender now preserves every supported direct observation, infers only
unseen samples with a nearby coplanar reference, and records unsupported source
texels separately. For atlases with missing observations the exported GLB
includes a lossless provenance mask linked from image extras. This is an
intentional correction to the photo texture path.
