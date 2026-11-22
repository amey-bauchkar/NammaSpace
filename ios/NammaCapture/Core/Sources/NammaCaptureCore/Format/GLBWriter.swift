import Foundation

/// Minimal glTF 2.0 binary (.glb) writer for one untextured triangle mesh: POSITION (float32 VEC3) + indices (uint32).
/// Used for ARKit's LiDAR scene-reconstruction mesh. Coordinates are written as given: ARKit world (metres, +Y up),
/// which matches glTF's convention, so no axis change is applied. Read by Namma Space's mesh_import.mesh_triangles.
public enum GLBWriter {
    public enum Error: Swift.Error, CustomStringConvertible {
        case invalid(String)
        public var description: String { if case .invalid(let s) = self { return s }; return "" }
    }

    /// `positions`: x,y,z per vertex; `indices`: 3 per triangle.
    public static func data(positions: [Float], indices: [UInt32], generator: String = "Namma Capture") throws -> Data {
        guard positions.count % 3 == 0, indices.count % 3 == 0, !indices.isEmpty else {
            throw Error.invalid("mesh needs whole vertices and triangles")
        }
        let vcount = positions.count / 3
        guard positions.allSatisfy(\.isFinite) else { throw Error.invalid("non-finite vertex") }
        guard let maxIndex = indices.max(), Int(maxIndex) < vcount else { throw Error.invalid("index out of range") }
        var minV = [Float](repeating: .greatestFiniteMagnitude, count: 3), maxV = [Float](repeating: -.greatestFiniteMagnitude, count: 3)
        for i in 0..<vcount { for k in 0..<3 { let v = positions[i * 3 + k]; minV[k] = min(minV[k], v); maxV[k] = max(maxV[k], v) } }

        let posBytes = positions.count * 4, idxBytes = indices.count * 4
        var bin = Data(capacity: posBytes + idxBytes)
        positions.withUnsafeBufferPointer { p in for v in p { var le = v.bitPattern.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } }
        indices.withUnsafeBufferPointer { p in for v in p { var le = v.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } }
        while bin.count % 4 != 0 { bin.append(0) }

        let json: [String: Any] = [
            "asset": ["version": "2.0", "generator": generator],
            "scene": 0, "scenes": [["nodes": [0]]],
            "nodes": [["mesh": 0, "name": "arkit_scene_mesh"]],
            "meshes": [["name": "arkit_scene_mesh", "primitives": [["attributes": ["POSITION": 0], "indices": 1, "mode": 4]]]],
            "buffers": [["byteLength": bin.count]],
            "bufferViews": [["buffer": 0, "byteOffset": 0, "byteLength": posBytes, "target": 34962],
                            ["buffer": 0, "byteOffset": posBytes, "byteLength": idxBytes, "target": 34963]],
            "accessors": [["bufferView": 0, "componentType": 5126, "count": vcount, "type": "VEC3",
                           "min": minV.map(Double.init), "max": maxV.map(Double.init)],
                          ["bufferView": 1, "componentType": 5125, "count": indices.count, "type": "SCALAR"]],
        ]
        var js = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        while js.count % 4 != 0 { js.append(0x20) }

        var out = Data()
        func u32(_ v: Int) { var le = UInt32(v).littleEndian; withUnsafeBytes(of: &le) { out.append(contentsOf: $0) } }
        out.append(contentsOf: Array("glTF".utf8)); u32(2); u32(12 + 8 + js.count + 8 + bin.count)
        u32(js.count); out.append(contentsOf: Array("JSON".utf8)); out.append(js)
        u32(bin.count); out.append(contentsOf: [0x42, 0x49, 0x4E, 0x00]); out.append(bin)   // "BIN\0"
        return out
    }
}
