import Foundation

// A small, memory-bounded ZIP writer/reader for .nammacap archives.
//
// Writer: entries are STORED (no compression: JPEG and float depth do not shrink meaningfully, and stored entries can
// be streamed and verified cheaply). Every entry gets ZIP64 extra fields so archives and entries may exceed 4 GiB.
// Data is copied in fixed-size chunks; nothing larger than one chunk is held in memory.
//
// Reader: parses the end-of-central-directory (ZIP64 aware) and central directory, and streams stored entries.
// It refuses anything it does not fully understand (compression, encryption, data descriptors with mismatches,
// overlapping entries) rather than guessing.

public enum ZipError: Error, CustomStringConvertible, Equatable {
    case io(String)
    case notAZip(String)
    case unsupported(String)
    case corrupt(String)
    case badPath(String)

    public var description: String {
        switch self {
        case .io(let s): return "I/O error: \(s)"
        case .notAZip(let s): return "not a ZIP archive: \(s)"
        case .unsupported(let s): return "unsupported ZIP feature: \(s)"
        case .corrupt(let s): return "corrupt archive: \(s)"
        case .badPath(let s): return "unsafe member path: \(s)"
        }
    }
}

public enum CRC32 {
    static let table: [UInt32] = (0..<256).map { i -> UInt32 in
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1) }
        return c
    }
    public static func update(_ crc: UInt32, _ buf: UnsafeRawBufferPointer) -> UInt32 {
        var c = ~crc
        for b in buf { c = table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
        return ~c
    }
    public static func checksum(_ data: Data) -> UInt32 { data.withUnsafeBytes { update(0, $0) } }
}

/// Member path rules (shared with the validator): relative, "/"-separated, no "." / ".." / empty components,
/// no backslashes, no drive letters, no control characters, ASCII printable only, <= 200 chars.
public enum ArchivePath {
    public static func isSafe(_ p: String) -> Bool {
        guard !p.isEmpty, p.count <= 200, !p.hasPrefix("/"), !p.contains("\\"), !p.contains(":") else { return false }
        for u in p.unicodeScalars where u.value < 0x21 || u.value > 0x7E { return false }
        for comp in p.split(separator: "/", omittingEmptySubsequences: false) {
            if comp.isEmpty || comp == "." || comp == ".." { return false }
        }
        return true
    }
}

private extension Data {
    mutating func le16(_ v: UInt16) { Swift.withUnsafeBytes(of: v.littleEndian) { append(contentsOf: $0) } }
    mutating func le32(_ v: UInt32) { Swift.withUnsafeBytes(of: v.littleEndian) { append(contentsOf: $0) } }
    mutating func le64(_ v: UInt64) { Swift.withUnsafeBytes(of: v.littleEndian) { append(contentsOf: $0) } }
}

/// Writes a ZIP to a file handle sequentially.
public final class ZipWriter {
    struct CentralEntry { let name: Data; let crc: UInt32; let size: UInt64; let offset: UInt64 }

    private let handle: FileHandle
    private var offset: UInt64 = 0
    private var entries: [CentralEntry] = []
    private var names = Set<String>()
    public static let chunkSize = 1 << 20
    private var finished = false

    public init(url: URL) throws {
        guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
            throw ZipError.io("cannot create \(url.lastPathComponent)")
        }
        do { handle = try FileHandle(forWritingTo: url) } catch { throw ZipError.io("\(error)") }
    }

    deinit { try? handle.close() }

    private func write(_ d: Data) throws {
        do { try handle.write(contentsOf: d) } catch { throw ZipError.io("\(error)") }
        offset += UInt64(d.count)
    }

    /// Add a member whose bytes come from `source` (read in chunks). Returns (bytes, sha256-hex) of the data written.
    /// `crc` and `size` must be known up front for a stored entry; we compute them in a first pass over the file.
    @discardableResult
    public func addFile(path: String, from source: URL) throws -> (bytes: Int64, sha256: String) {
        // pass 1: crc + size + sha (chunked)
        let (crc, size, sha) = try Self.scan(source)
        try addHeader(path: path, crc: crc, size: size)
        // pass 2: copy (chunked); re-hash to catch a file that changed between passes
        let h: FileHandle
        do { h = try FileHandle(forReadingFrom: source) } catch { throw ZipError.io("\(error)") }
        defer { try? h.close() }
        var crc2: UInt32 = 0, copied: UInt64 = 0
        while true {
            let chunk: Data
            do { chunk = try h.read(upToCount: Self.chunkSize) ?? Data() } catch { throw ZipError.io("\(error)") }
            if chunk.isEmpty { break }
            crc2 = chunk.withUnsafeBytes { CRC32.update(crc2, $0) }
            copied += UInt64(chunk.count)
            try write(chunk)
        }
        guard crc2 == crc, copied == size else {
            throw ZipError.io("\(path) changed while it was being archived")
        }
        return (Int64(size), sha)
    }

    @discardableResult
    public func addData(path: String, data: Data) throws -> (bytes: Int64, sha256: String) {
        let crc = CRC32.checksum(data)
        try addHeader(path: path, crc: crc, size: UInt64(data.count))
        try write(data)
        return (Int64(data.count), SHA256Hex.of(data))
    }

    private func addHeader(path: String, crc: UInt32, size: UInt64) throws {
        guard !finished else { throw ZipError.io("archive already finished") }
        guard ArchivePath.isSafe(path) else { throw ZipError.badPath(path) }
        guard names.insert(path).inserted else { throw ZipError.corrupt("duplicate member \(path)") }
        let name = Data(path.utf8)
        var h = Data()
        h.le32(0x04034b50)
        h.le16(45)                // version needed: 4.5 (ZIP64)
        h.le16(1 << 11)           // flags: UTF-8 names
        h.le16(0)                 // method: stored
        h.le16(0); h.le16(0x21)   // mod time/date: fixed (1980-01-01) for reproducible archives
        h.le32(crc)
        h.le32(0xFFFFFFFF); h.le32(0xFFFFFFFF)   // sizes in ZIP64 extra
        h.le16(UInt16(name.count))
        h.le16(20)                // extra length
        h.append(name)
        h.le16(0x0001); h.le16(16); h.le64(size); h.le64(size)
        entries.append(CentralEntry(name: name, crc: crc, size: size, offset: offset))
        try write(h)
    }

    /// Write the central directory and ZIP64 end records. The archive is only valid after this returns.
    public func finish() throws {
        guard !finished else { return }
        let cdStart = offset
        for e in entries {
            var c = Data()
            c.le32(0x02014b50)
            c.le16(0x032D)          // made by: Unix, 4.5
            c.le16(45); c.le16(1 << 11); c.le16(0); c.le16(0); c.le16(0x21)
            c.le32(e.crc)
            c.le32(0xFFFFFFFF); c.le32(0xFFFFFFFF)
            c.le16(UInt16(e.name.count))
            c.le16(28)              // extra: zip64 (sizes + offset)
            c.le16(0)               // comment
            c.le16(0)               // disk start
            c.le16(0)               // internal attrs
            c.le32(0o100644 << 16)  // external attrs: regular file rw-r--r--
            c.le32(0xFFFFFFFF)      // offset in zip64
            c.append(e.name)
            c.le16(0x0001); c.le16(24); c.le64(e.size); c.le64(e.size); c.le64(e.offset)
            try write(c)
        }
        let cdSize = offset - cdStart
        let z64eocd = offset
        var z = Data()
        z.le32(0x06064b50); z.le64(44); z.le16(0x032D); z.le16(45); z.le32(0); z.le32(0)
        z.le64(UInt64(entries.count)); z.le64(UInt64(entries.count)); z.le64(cdSize); z.le64(cdStart)
        z.le32(0x07064b50); z.le32(0); z.le64(z64eocd); z.le32(1)
        z.le32(0x06054b50); z.le16(0); z.le16(0)
        z.le16(0xFFFF); z.le16(0xFFFF); z.le32(0xFFFFFFFF); z.le32(0xFFFFFFFF); z.le16(0)
        try write(z)
        do { try handle.synchronize(); try handle.close() } catch { throw ZipError.io("\(error)") }
        finished = true
    }

    static func scan(_ url: URL) throws -> (UInt32, UInt64, String) {
        let h: FileHandle
        do { h = try FileHandle(forReadingFrom: url) } catch { throw ZipError.io("cannot read \(url.lastPathComponent): \(error)") }
        defer { try? h.close() }
        var crc: UInt32 = 0, size: UInt64 = 0
        var sha = SHA256Hex.Streaming()
        while true {
            let chunk: Data
            do { chunk = try h.read(upToCount: chunkSize) ?? Data() } catch { throw ZipError.io("\(error)") }
            if chunk.isEmpty { break }
            crc = chunk.withUnsafeBytes { CRC32.update(crc, $0) }
            sha.update(chunk)
            size += UInt64(chunk.count)
        }
        return (crc, size, sha.finalizeHex())
    }
}

/// Central-directory reader for archives this module (or any standard tool) wrote with STORED entries.
public final class ZipReader {
    public struct Entry: Equatable {
        public let path: String
        public let crc: UInt32
        public let size: UInt64
        public let compressedSize: UInt64
        public let method: UInt16
        public let flags: UInt16
        public let localHeaderOffset: UInt64
        public var dataOffset: UInt64 = 0
    }

    public let url: URL
    public let fileSize: UInt64
    public private(set) var entries: [Entry] = []
    private let handle: FileHandle

    public init(url: URL) throws {
        self.url = url
        do { handle = try FileHandle(forReadingFrom: url) } catch { throw ZipError.io("cannot open: \(error)") }
        do { fileSize = try handle.seekToEnd() } catch { throw ZipError.io("\(error)") }
        try parse()
    }
    deinit { try? handle.close() }

    private func read(at off: UInt64, count: Int) throws -> Data {
        guard off + UInt64(count) <= fileSize else { throw ZipError.corrupt("read past end of file (truncated?)") }
        do {
            try handle.seek(toOffset: off)
            let d = try handle.read(upToCount: count) ?? Data()
            guard d.count == count else { throw ZipError.corrupt("short read") }
            return d
        } catch let e as ZipError { throw e } catch { throw ZipError.io("\(error)") }
    }

    private func parse() throws {
        guard fileSize >= 22 else { throw ZipError.notAZip("file too small (\(fileSize) bytes)") }
        let tailLen = Int(min(fileSize, 22 + 65535 + 20))
        let tail = try read(at: fileSize - UInt64(tailLen), count: tailLen)
        var eocd: Int? = nil
        var i = tail.count - 22
        while i >= 0 {
            if tail.u32(i) == 0x06054b50 { eocd = i; break }
            i -= 1
        }
        guard let e = eocd else { throw ZipError.notAZip("no end-of-central-directory record (incomplete or truncated)") }
        var count = UInt64(tail.u16(e + 10))
        var cdSize = UInt64(tail.u32(e + 12))
        var cdOff = UInt64(tail.u32(e + 16))
        if count == 0xFFFF || cdSize == 0xFFFFFFFF || cdOff == 0xFFFFFFFF {
            guard e >= 20, tail.u32(e - 20) == 0x07064b50 else { throw ZipError.corrupt("ZIP64 locator missing") }
            let z64off = tail.u64(e - 20 + 8)
            let z = try read(at: z64off, count: 56)
            guard z.u32(0) == 0x06064b50 else { throw ZipError.corrupt("ZIP64 end record missing") }
            count = z.u64(32); cdSize = z.u64(40); cdOff = z.u64(48)
        }
        guard cdOff + cdSize <= fileSize, count < 10_000_000 else { throw ZipError.corrupt("central directory out of range") }
        let cd = try read(at: cdOff, count: Int(cdSize))
        var p = 0
        var out: [Entry] = []
        for _ in 0..<count {
            guard p + 46 <= cd.count, cd.u32(p) == 0x02014b50 else { throw ZipError.corrupt("bad central directory entry") }
            let flags = cd.u16(p + 8), method = cd.u16(p + 10)
            let crc = cd.u32(p + 16)
            var csize = UInt64(cd.u32(p + 20)), usize = UInt64(cd.u32(p + 24))
            let nlen = Int(cd.u16(p + 28)), xlen = Int(cd.u16(p + 30)), clen = Int(cd.u16(p + 32))
            var lho = UInt64(cd.u32(p + 42))
            guard p + 46 + nlen + xlen + clen <= cd.count else { throw ZipError.corrupt("central directory entry overruns") }
            guard let name = String(data: cd.subdata(in: (p + 46)..<(p + 46 + nlen)), encoding: .utf8) else {
                throw ZipError.badPath("non-UTF-8 name")
            }
            // ZIP64 extra
            var x = p + 46 + nlen
            let xend = x + xlen
            while x + 4 <= xend {
                let id = cd.u16(x), len = Int(cd.u16(x + 2))
                if id == 0x0001 {
                    var q = x + 4
                    if usize == 0xFFFFFFFF { usize = cd.u64(q); q += 8 }
                    if csize == 0xFFFFFFFF { csize = cd.u64(q); q += 8 }
                    if lho == 0xFFFFFFFF { lho = cd.u64(q); q += 8 }
                }
                x += 4 + len
            }
            out.append(Entry(path: name, crc: crc, size: usize, compressedSize: csize, method: method, flags: flags,
                             localHeaderOffset: lho))
            p += 46 + nlen + xlen + clen
        }
        // resolve data offsets from local headers
        for k in out.indices {
            let lh = try read(at: out[k].localHeaderOffset, count: 30)
            guard lh.u32(0) == 0x04034b50 else { throw ZipError.corrupt("bad local header for \(out[k].path)") }
            let n = UInt64(lh.u16(26)), xl = UInt64(lh.u16(28))
            out[k].dataOffset = out[k].localHeaderOffset + 30 + n + xl
            guard out[k].dataOffset + out[k].compressedSize <= fileSize else {
                throw ZipError.corrupt("\(out[k].path) extends past end of file (truncated)")
            }
        }
        entries = out
    }

    /// Stream a stored entry's bytes in chunks; verifies CRC-32 and returns (bytes, sha256-hex).
    public func hashEntry(_ e: Entry) throws -> (bytes: Int64, sha256: String) {
        guard e.method == 0 else { throw ZipError.unsupported("compression method \(e.method) for \(e.path)") }
        guard e.flags & 1 == 0 else { throw ZipError.unsupported("encrypted member \(e.path)") }
        var remaining = e.size, off = e.dataOffset, crc: UInt32 = 0
        var sha = SHA256Hex.Streaming()
        while remaining > 0 {
            let n = Int(min(UInt64(ZipWriter.chunkSize), remaining))
            let d = try read(at: off, count: n)
            crc = d.withUnsafeBytes { CRC32.update(crc, $0) }
            sha.update(d)
            off += UInt64(n); remaining -= UInt64(n)
        }
        guard crc == e.crc else { throw ZipError.corrupt("CRC mismatch in \(e.path)") }
        return (Int64(e.size), sha.finalizeHex())
    }

    /// Whole small entry in memory (manifest, jsonl). Refuses entries above `limit`.
    public func data(_ e: Entry, limit: UInt64 = 256 << 20) throws -> Data {
        guard e.method == 0 else { throw ZipError.unsupported("compression method \(e.method) for \(e.path)") }
        guard e.size <= limit else { throw ZipError.unsupported("\(e.path) is too large to load (\(e.size) bytes)") }
        let d = try read(at: e.dataOffset, count: Int(e.size))
        guard CRC32.checksum(d) == e.crc else { throw ZipError.corrupt("CRC mismatch in \(e.path)") }
        return d
    }
}

private extension Data {
    func u16(_ i: Int) -> UInt16 { UInt16(self[startIndex + i]) | UInt16(self[startIndex + i + 1]) << 8 }
    func u32(_ i: Int) -> UInt32 { UInt32(u16(i)) | UInt32(u16(i + 2)) << 16 }
    func u64(_ i: Int) -> UInt64 { UInt64(u32(i)) | UInt64(u32(i + 4)) << 32 }
}
