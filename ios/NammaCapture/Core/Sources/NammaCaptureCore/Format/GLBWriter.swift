import Foundation

/// Minimal glTF 2.0 binary (.glb) writer for one triangle mesh: POSITION (float32 VEC3), optional COLOR_0
/// (normalized uint8 RGBA per vertex) and indices (uint32). Coordinates are written as given: ARKit world (metres,
/// +Y up) matches glTF, so no axis change is applied. Read by Namma Space's mesh_import.mesh_triangles and by
/// standard glTF viewers. A coloured mesh gets an unlit material (colours come from photos and already contain the
/// room's light), so viewers show the photo colours instead of re-lighting them.
public enum GLBWriter {
    public enum Error: Swift.Error, CustomStringConvertible {
        case invalid(String)
        public var description: String { if case .invalid(let s) = self { return s }; return "" }
    }

    /// `positions`: x,y,z per vertex; `indices`: 3 per triangle; `colors`: r,g,b,a per vertex (optional).
    public static func data(positions: [Float], indices: [UInt32], colors: [UInt8]? = nil,
                            generator: String = "Namma Capture") throws -> Data {
        guard positions.count % 3 == 0, indices.count % 3 == 0, !indices.isEmpty else {
            throw Error.invalid("mesh needs whole vertices and triangles")
        }
        let vcount = positions.count / 3
        guard positions.allSatisfy(\.isFinite) else { throw Error.invalid("non-finite vertex") }
        guard let maxIndex = indices.max(), Int(maxIndex) < vcount else { throw Error.invalid("index out of range") }
        if let colors, colors.count != vcount * 4 { throw Error.invalid("colors need 4 bytes per vertex") }
        var minV = [Float](repeating: .greatestFiniteMagnitude, count: 3), maxV = [Float](repeating: -.greatestFiniteMagnitude, count: 3)
        for i in 0..<vcount { for k in 0..<3 { let v = positions[i * 3 + k]; minV[k] = min(minV[k], v); maxV[k] = max(maxV[k], v) } }

        let posBytes = positions.count * 4, idxBytes = indices.count * 4, colBytes = colors?.count ?? 0
        var bin = Data(capacity: posBytes + idxBytes + colBytes)
        positions.withUnsafeBufferPointer { p in for v in p { var le = v.bitPattern.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } }
        indices.withUnsafeBufferPointer { p in for v in p { var le = v.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } }
        if let colors { bin.append(contentsOf: colors) }            // offset is a multiple of 4: positions and indices are 4-byte
        while bin.count % 4 != 0 { bin.append(0) }

        var attributes: [String: Any] = ["POSITION": 0]
        var views: [[String: Any]] = [["buffer": 0, "byteOffset": 0, "byteLength": posBytes, "target": 34962],
                                      ["buffer": 0, "byteOffset": posBytes, "byteLength": idxBytes, "target": 34963]]
        var accessors: [[String: Any]] = [["bufferView": 0, "componentType": 5126, "count": vcount, "type": "VEC3",
                                           "min": minV.map(Double.init), "max": maxV.map(Double.init)],
                                          ["bufferView": 1, "componentType": 5125, "count": indices.count, "type": "SCALAR"]]
        var primitive: [String: Any] = ["indices": 1, "mode": 4]
        var json: [String: Any] = [
            "asset": ["version": "2.0", "generator": generator],
            "scene": 0, "scenes": [["nodes": [0]]],
            "nodes": [["mesh": 0, "name": "arkit_scene_mesh"]],
        ]
        if colors != nil {
            views.append(["buffer": 0, "byteOffset": posBytes + idxBytes, "byteLength": colBytes, "target": 34962])
            accessors.append(["bufferView": 2, "componentType": 5121, "normalized": true, "count": vcount, "type": "VEC4"])
            attributes["COLOR_0"] = 2
            primitive["material"] = 0
            json["materials"] = [["name": "photo_vertex_colors",
                                  "pbrMetallicRoughness": ["baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 1],
                                  "extensions": ["KHR_materials_unlit": [String: Any]()]]]
            json["extensionsUsed"] = ["KHR_materials_unlit"]
        }
        primitive["attributes"] = attributes
        json["meshes"] = [["name": "arkit_scene_mesh", "primitives": [primitive]]]
        json["buffers"] = [["byteLength": bin.count]]
        json["bufferViews"] = views
        json["accessors"] = accessors
        var js = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        while js.count % 4 != 0 { js.append(0x20) }

        var out = Data()
        func u32(_ v: Int) { var le = UInt32(v).littleEndian; withUnsafeBytes(of: &le) { out.append(contentsOf: $0) } }
        out.append(contentsOf: Array("glTF".utf8)); u32(2); u32(12 + 8 + js.count + 8 + bin.count)
        u32(js.count); out.append(contentsOf: Array("JSON".utf8)); out.append(js)
        u32(bin.count); out.append(contentsOf: [0x42, 0x49, 0x4E, 0x00]); out.append(bin)   // "BIN\0"
        return out
    }

    /// Read back positions and indices from a GLB this writer produced (used to colour a saved mesh).
    public static func read(_ d: Data) throws -> (positions: [Float], indices: [UInt32]) {
        guard d.count >= 28, Array(d.prefix(4)) == Array("glTF".utf8) else { throw Error.invalid("not a GLB") }
        func u32(_ o: Int) -> Int { Int(d.subdata(in: o..<(o + 4)).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }) }
        let jl = u32(12)
        guard let doc = try JSONSerialization.jsonObject(with: d.subdata(in: 20..<(20 + jl))) as? [String: Any],
              let acc = doc["accessors"] as? [[String: Any]], let bvs = doc["bufferViews"] as? [[String: Any]],
              let meshes = doc["meshes"] as? [[String: Any]], let prims = meshes.first?["primitives"] as? [[String: Any]],
              let attrs = prims.first?["attributes"] as? [String: Any], let pi = attrs["POSITION"] as? Int,
              let ii = prims.first?["indices"] as? Int else { throw Error.invalid("unexpected GLB layout") }
        let binStart = 20 + jl + 8
        func view(_ a: Int) -> (Int, Int) {
            let bv = bvs[acc[a]["bufferView"] as! Int]
            return (binStart + (bv["byteOffset"] as? Int ?? 0), acc[a]["count"] as! Int)
        }
        let (po, pc) = view(pi), (io, ic) = view(ii)
        guard po + pc * 12 <= d.count, io + ic * 4 <= d.count else { throw Error.invalid("GLB buffer too short") }
        var pos = [Float](repeating: 0, count: pc * 3), idx = [UInt32](repeating: 0, count: ic)
        d.withUnsafeBytes { raw in
            for k in 0..<(pc * 3) { pos[k] = Float(bitPattern: UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: po + k * 4, as: UInt32.self))) }
            for k in 0..<ic { idx[k] = UInt32(littleEndian: raw.loadUnaligned(fromByteOffset: io + k * 4, as: UInt32.self)) }
        }
        return (pos, idx)
    }
}

extension GLBWriter {
    /// One textured mesh: un-shared vertices (positions + TEXCOORD_0), one primitive per texture atlas (its triangle
    /// indices), each with an unlit material whose base colour is that atlas (JPEG bytes embedded in the GLB).
    /// UV origin is the image's top-left corner (glTF convention).
    public static func texturedData(positions: [Float], uvs: [Float], primitives: [[UInt32]], atlases: [Data],
                                    generator: String = "Namma Capture") throws -> Data {
        let vcount = positions.count / 3
        guard positions.count % 3 == 0, uvs.count == vcount * 2, primitives.count == atlases.count, !atlases.isEmpty,
              positions.allSatisfy(\.isFinite), uvs.allSatisfy(\.isFinite) else {
            throw Error.invalid("textured mesh: inconsistent buffers")
        }
        var minV = [Float](repeating: .greatestFiniteMagnitude, count: 3), maxV = [Float](repeating: -.greatestFiniteMagnitude, count: 3)
        for i in 0..<vcount { for k in 0..<3 { let v = positions[i * 3 + k]; minV[k] = min(minV[k], v); maxV[k] = max(maxV[k], v) } }
        var bin = Data()
        func align() { while bin.count % 4 != 0 { bin.append(0) } }
        func floats(_ a: [Float]) { a.withUnsafeBufferPointer { p in for v in p { var le = v.bitPattern.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } } }
        var views: [[String: Any]] = [], accessors: [[String: Any]] = []
        var o = bin.count; floats(positions)
        views.append(["buffer": 0, "byteOffset": o, "byteLength": positions.count * 4, "target": 34962])
        accessors.append(["bufferView": 0, "componentType": 5126, "count": vcount, "type": "VEC3",
                          "min": minV.map(Double.init), "max": maxV.map(Double.init)])
        o = bin.count; floats(uvs)
        views.append(["buffer": 0, "byteOffset": o, "byteLength": uvs.count * 4, "target": 34962])
        accessors.append(["bufferView": 1, "componentType": 5126, "count": vcount, "type": "VEC2"])
        var prims: [[String: Any]] = [], materials: [[String: Any]] = [], images: [[String: Any]] = [], textures: [[String: Any]] = []
        for (k, idx) in primitives.enumerated() where !idx.isEmpty {
            guard idx.count % 3 == 0, (idx.max() ?? 0) < UInt32(vcount) else { throw Error.invalid("textured mesh: bad indices") }
            o = bin.count
            idx.withUnsafeBufferPointer { p in for v in p { var le = v.littleEndian; withUnsafeBytes(of: &le) { bin.append(contentsOf: $0) } } }
            views.append(["buffer": 0, "byteOffset": o, "byteLength": idx.count * 4, "target": 34963])
            accessors.append(["bufferView": views.count - 1, "componentType": 5125, "count": idx.count, "type": "SCALAR"])
            let ia = accessors.count - 1
            align(); o = bin.count; bin.append(atlases[k]); align()
            views.append(["buffer": 0, "byteOffset": o, "byteLength": atlases[k].count])
            images.append(["bufferView": views.count - 1, "mimeType": "image/jpeg", "name": "atlas_\(k)"])
            textures.append(["source": images.count - 1, "sampler": 0])
            materials.append(["name": "photo_atlas_\(k)",
                              "pbrMetallicRoughness": ["baseColorTexture": ["index": textures.count - 1],
                                                       "metallicFactor": 0, "roughnessFactor": 1],
                              "extensions": ["KHR_materials_unlit": [String: Any]()]])
            prims.append(["attributes": ["POSITION": 0, "TEXCOORD_0": 1], "indices": ia, "material": materials.count - 1, "mode": 4])
        }
        align()
        let json: [String: Any] = [
            "asset": ["version": "2.0", "generator": generator], "scene": 0, "scenes": [["nodes": [0]]],
            "nodes": [["mesh": 0, "name": "arkit_scene_mesh"]],
            "meshes": [["name": "arkit_scene_mesh_textured", "primitives": prims]],
            "materials": materials, "images": images, "textures": textures,
            "samplers": [["magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071]],
            "extensionsUsed": ["KHR_materials_unlit"],
            "buffers": [["byteLength": bin.count]], "bufferViews": views, "accessors": accessors,
        ]
        var js = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        while js.count % 4 != 0 { js.append(0x20) }
        var out = Data()
        func u32(_ v: Int) { var le = UInt32(v).littleEndian; withUnsafeBytes(of: &le) { out.append(contentsOf: $0) } }
        out.append(contentsOf: Array("glTF".utf8)); u32(2); u32(12 + 8 + js.count + 8 + bin.count)
        u32(js.count); out.append(contentsOf: Array("JSON".utf8)); out.append(js)
        u32(bin.count); out.append(contentsOf: [0x42, 0x49, 0x4E, 0x00]); out.append(bin)
        return out
    }
}
