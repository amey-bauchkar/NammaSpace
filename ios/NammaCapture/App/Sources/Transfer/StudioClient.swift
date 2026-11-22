import CryptoKit
import Foundation
import NammaCaptureCore
import Security

/// Sends a finished .nammacap to Namma Space Studio's phone listener (src/namma/studio/phone_capture.py):
///   POST /api/pair {code} -> token;  POST /api/nammacap {bytes, sha256, title} -> upload_id, part_size, parts
///   PUT /api/nammacap/<id>/part/<n>;  GET /api/nammacap/<id> (resume);  POST /api/nammacap/<id>/finish -> project
///
/// Trust: the Studio uses a self-signed certificate made on the user's computer. The app only talks to private-network
/// addresses, accepts the certificate the first time (trust on first use) and remembers its SHA-256 fingerprint for that
/// host; a different certificate later is refused until the user explicitly forgets the old one.
enum StudioError: LocalizedError {
    case badLink(String)
    case notLocal(String)
    case certificateChanged(String)
    case server(Int, String, String)
    case transport(String)
    var errorDescription: String? {
        switch self {
        case .badLink(let s): return "That is not a Studio link: \(s)"
        case .notLocal(let h): return "\(h) is not on your local network. Namma Capture only sends scans to a computer on the same Wi-Fi or hotspot."
        case .certificateChanged(let h): return "The Studio at \(h) presented a different certificate than last time. If you restarted the Studio on a new network this is expected: tap Forget certificate, then pair again."
        case .server(let status, let code, let msg): return "Studio refused (\(status) \(code)): \(msg)"
        case .transport(let s): return "Network error: \(s)"
        }
    }
}

struct StudioTarget: Codable, Equatable {
    var baseURL: URL
    var code: String

    /// Accepts the Studio's QR/link ("https://192.168.1.5:8443/#c=123456") or a bare URL plus a code typed separately.
    static func parse(link: String, code: String?) throws -> StudioTarget {
        let trimmed = link.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var comps = URLComponents(string: trimmed.contains("://") ? trimmed : "https://\(trimmed)"),
              comps.scheme == "https", let host = comps.host, !host.isEmpty else { throw StudioError.badLink(trimmed) }
        var c = code?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if let frag = comps.fragment, frag.hasPrefix("c=") { c = c.isEmpty ? String(frag.dropFirst(2)) : c }
        comps.fragment = nil; comps.path = "/"; comps.query = nil
        guard c.count == 6, c.allSatisfy(\.isNumber) else { throw StudioError.badLink("the 6-digit pairing code is missing") }
        guard isPrivate(host) else { throw StudioError.notLocal(host) }
        guard let u = comps.url else { throw StudioError.badLink(trimmed) }
        return StudioTarget(baseURL: u, code: c)
    }

    static func isPrivate(_ host: String) -> Bool {
        if host == "localhost" || host.hasSuffix(".local") { return true }
        let p = host.split(separator: ".").compactMap { Int($0) }
        guard p.count == 4, p.allSatisfy({ (0...255).contains($0) }) else { return false }
        return p[0] == 10 || (p[0] == 172 && (16...31).contains(p[1])) || (p[0] == 192 && p[1] == 168) || p[0] == 127
            || (p[0] == 169 && p[1] == 254)
    }
}

final class StudioClient: NSObject, URLSessionDelegate, @unchecked Sendable {
    let target: StudioTarget
    private var session: URLSession!
    private var token: String?
    private(set) var presentedFingerprint: String?
    static let pinKey = "studio.pins"

    init(target: StudioTarget) {
        self.target = target
        super.init()
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 60
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }

    static func forgetCertificate(host: String) {
        var pins = UserDefaults.standard.dictionary(forKey: pinKey) as? [String: String] ?? [:]
        pins[host] = nil
        UserDefaults.standard.set(pins, forKey: pinKey)
    }

    // MARK: TLS trust (trust on first use, pinned afterwards)

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let host = challenge.protectionSpace.host
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              host == target.baseURL.host, StudioTarget.isPrivate(host),
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let fp = SHA256.hash(data: SecCertificateCopyData(leaf) as Data).map { String(format: "%02x", $0) }.joined()
        presentedFingerprint = fp
        var pins = UserDefaults.standard.dictionary(forKey: Self.pinKey) as? [String: String] ?? [:]
        if let pinned = pins[host], pinned != fp {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        pins[host] = fp
        UserDefaults.standard.set(pins, forKey: Self.pinKey)
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    // MARK: requests

    private func request(_ method: String, _ path: String, json: [String: Any]? = nil, body: Data? = nil) async throws -> (Int, [String: Any]) {
        var r = URLRequest(url: target.baseURL.appendingPathComponent(path))
        r.httpMethod = method
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try JSONSerialization.data(withJSONObject: json)
        } else if let body {
            r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
            r.httpBody = body
        }
        do {
            let (data, resp) = try await session.data(for: r)
            let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
            return (status, obj)
        } catch let e as URLError where e.code == .cancelled || e.code == .serverCertificateUntrusted
                    || e.code == .secureConnectionFailed {
            if let host = target.baseURL.host,
               let pinned = (UserDefaults.standard.dictionary(forKey: Self.pinKey) as? [String: String])?[host],
               let fp = presentedFingerprint, pinned != fp {
                throw StudioError.certificateChanged(host)
            }
            throw StudioError.transport(e.localizedDescription)
        } catch {
            throw StudioError.transport(error.localizedDescription)
        }
    }

    private func check(_ r: (Int, [String: Any]), _ ok: Set<Int>) throws -> [String: Any] {
        guard ok.contains(r.0) else {
            throw StudioError.server(r.0, r.1["error"] as? String ?? "?", r.1["message"] as? String ?? "no message")
        }
        return r.1
    }

    func pair() async throws {
        let b = try check(try await request("POST", "api/pair", json: ["code": target.code]), [200])
        guard let t = b["token"] as? String else { throw StudioError.server(200, "NO-TOKEN", "pairing returned no token") }
        token = t
    }

    struct UploadResult { let projectId: String; let framesImported: Int; let warnings: [String] }

    /// Upload with per-part retries; parts already on the server (after a dropped connection) are skipped.
    func upload(archive: URL, title: String, progress: @escaping @Sendable (Double, String) -> Void) async throws -> UploadResult {
        if token == nil { try await pair() }
        progress(0, "Checking the file")
        let (size, sha) = try SHA256Hex.ofFile(archive)
        let start = try check(try await request("POST", "api/nammacap", json: ["bytes": Int(size), "sha256": sha, "title": title]), [201])
        guard let uid = start["upload_id"] as? String, let partSize = start["part_size"] as? Int, let parts = start["parts"] as? Int
        else { throw StudioError.server(201, "BAD-RESPONSE", "upload start response incomplete") }

        var attemptRound = 0
        while true {
            let status = try check(try await request("GET", "api/nammacap/\(uid)"), [200])
            let have = Set((status["received"] as? [Int]) ?? [])
            let missing = (0..<parts).filter { !have.contains($0) }
            if missing.isEmpty { break }
            attemptRound += 1
            if attemptRound > 4 { throw StudioError.transport("upload kept failing after several retries") }
            let fh = try FileHandle(forReadingFrom: archive)
            defer { try? fh.close() }
            for n in missing {
                try fh.seek(toOffset: UInt64(n * partSize))
                let chunk = try fh.read(upToCount: partSize) ?? Data()
                var lastError: Error?
                for attempt in 0..<3 {
                    do {
                        _ = try check(try await request("PUT", "api/nammacap/\(uid)/part/\(n)", body: chunk), [201])
                        lastError = nil
                        break
                    } catch {
                        lastError = error
                        try? await Task.sleep(nanoseconds: UInt64(500_000_000 * (1 << attempt)))
                    }
                }
                if let e = lastError, case StudioError.server = e { throw e }
                progress(Double(have.count + missing.firstIndex(of: n)! + 1) / Double(parts) * 0.9,
                         "Sending part \(n + 1) of \(parts)")
            }
        }
        progress(0.92, "Studio is checking the scan")
        let fin = try await request("POST", "api/nammacap/\(uid)/finish", json: [:])
        if fin.0 == 422, (fin.1["error"] as? String) == "NAMMACAP-CHECKSUM" {
            throw StudioError.server(422, "NAMMACAP-CHECKSUM", "The file arrived damaged. Tap Send again to re-send it.")
        }
        let res = try check(fin, [201])
        progress(1, "Imported")
        return UploadResult(projectId: res["project_id"] as? String ?? "?", framesImported: res["frames_imported"] as? Int ?? 0,
                            warnings: res["warnings"] as? [String] ?? [])
    }
}
