import Foundation
import NammaCaptureCore

// swift run -c release nammacap-bake <views.json> <mesh.glb> <out.glb>
// views.json: [{"image": path, "T": 4x4 row-major camera->world (ARKit axes), "K": {fx,fy,cx,cy,width,height},
//               "sharpness": number, "depth": {"path": float32 LE metres file, "width", "height", "K": {...}} | null}]
// Bakes the same photo texture as the app's glTF export (TextureBaker), for meshes made on the computer
// (e.g. LiDAR depth fused with Namma Space's lidar_fusion).
let a = CommandLine.arguments
guard a.count == 4 else { FileHandle.standardError.write(Data("usage: nammacap-bake views.json mesh.glb out.glb\n".utf8)); exit(64) }
func intr(_ d: [String: Any]) -> Intrinsics {
    Intrinsics(fx: d["fx"] as! Double, fy: d["fy"] as! Double, cx: d["cx"] as! Double, cy: d["cy"] as! Double,
               width: d["width"] as! Int, height: d["height"] as! Int)
}
let docs = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[1]))) as! [[String: Any]]
let views: [TextureBaker.View] = docs.map { v in
    let pose = Pose(rowMajor: v["T"] as! [[Double]])!
    var depth: (width: Int, height: Int, values: [Float], intrinsics: Intrinsics, pose: Pose)? = nil
    if let d = v["depth"] as? [String: Any], let data = try? Data(contentsOf: URL(fileURLWithPath: d["path"] as! String)) {
        let n = data.count / 4
        let vals = data.withUnsafeBytes { r in (0..<n).map { Float(bitPattern: UInt32(littleEndian: r.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) } }
        depth = (d["width"] as! Int, d["height"] as! Int, vals, intr(d["K"] as! [String: Any]), pose)
    }
    return .init(base: .init(imageURL: URL(fileURLWithPath: v["image"] as! String), pose: pose,
                             intrinsics: intr(v["K"] as! [String: Any]), depth: depth),
                 sharpness: v["sharpness"] as? Double ?? 0)
}
let (p, i) = try GLBWriter.read(Data(contentsOf: URL(fileURLWithPath: a[2])))
let t0 = Date()
var last = -1
let r = try TextureBaker.bake(positions: p, indices: i, views: views) { f in
    let pct = Int(f * 100); if pct / 10 != last / 10 { last = pct; print("\(pct)%") }
}
try r.glb.write(to: URL(fileURLWithPath: a[3]))
print(String(format: "baked %d/%d triangles from %d photos, %d atlases, texel %.2f mm, %.1f MB, %.0f s",
             r.trianglesTextured, r.trianglesTotal, r.viewsUsed, r.atlases, r.texelSizeM * 1000, Double(r.glb.count) / 1e6,
             Date().timeIntervalSince(t0)))
