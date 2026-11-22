import Foundation

/// One JSON configuration for everything the app writes. Non-finite numbers are refused (the Namma Space reader
/// rejects NaN/Infinity, and so would any strict JSON parser), so a NaN never silently reaches the archive.
public enum NammaJSON {
    public static func encoder(pretty: Bool = false) -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = pretty ? [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes] : [.sortedKeys, .withoutEscapingSlashes]
        e.nonConformingFloatEncodingStrategy = .throw
        return e
    }

    public static func decoder() -> JSONDecoder { JSONDecoder() }

    /// One line of a .jsonl file (no embedded newlines; JSONEncoder never emits raw newlines without .prettyPrinted).
    public static func line<T: Encodable>(_ value: T) throws -> Data {
        var d = try encoder().encode(value)
        d.append(0x0A)
        return d
    }
}

/// Replace non-finite values with nil so they are written as JSON null (used for optional sensor readings).
@inlinable public func finiteOrNil(_ v: Double?) -> Double? {
    guard let v, v.isFinite else { return nil }
    return v
}

@inlinable public func finiteOrZero(_ v: Double) -> Double { v.isFinite ? v : 0 }

public enum ISOTime {
    nonisolated(unsafe) private static let fmt: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let lock = NSLock()
    public static func string(_ date: Date = Date()) -> String {
        lock.lock(); defer { lock.unlock() }
        return fmt.string(from: date)
    }
}
