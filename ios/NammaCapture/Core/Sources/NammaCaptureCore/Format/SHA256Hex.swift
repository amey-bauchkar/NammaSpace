import CryptoKit
import Foundation

public enum SHA256Hex {
    public static func of(_ data: Data) -> String { hex(SHA256.hash(data: data)) }

    public static func ofFile(_ url: URL, chunk: Int = 1 << 20) throws -> (bytes: Int64, sha256: String) {
        let h = try FileHandle(forReadingFrom: url)
        defer { try? h.close() }
        var s = Streaming(), n: Int64 = 0
        while let d = try h.read(upToCount: chunk), !d.isEmpty {
            s.update(d); n += Int64(d.count)
        }
        return (n, s.finalizeHex())
    }

    static func hex<D: Sequence>(_ digest: D) -> String where D.Element == UInt8 {
        digest.map { String(format: "%02x", $0) }.joined()
    }

    public struct Streaming {
        var h = SHA256()
        public init() {}
        public mutating func update(_ d: Data) { h.update(data: d) }
        public func finalizeHex() -> String { SHA256Hex.hex(h.finalize()) }
    }
}
