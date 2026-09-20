import Foundation
import CryptoKit

/// Append-only, hash-chained record of every artifact the app produces.
/// Each entry hashes the payload and the previous entry, so reordering or
/// editing any item breaks the chain. Persisted as JSON lines in the app
/// container; slice 2 will move this behind an encrypted store.
final class CustodyLedger {

    struct Entry: Codable, Identifiable {
        let id: UUID
        let sequence: Int
        let timestamp: Date
        let kind: String
        let byteCount: Int
        let sha256: String        // hash of the payload
        let deviceId: String
        let previousHash: String  // chainHash of prior entry, "genesis" for first
        let chainHash: String     // sha256(previousHash + sha256 + sequence + timestamp)
    }

    private(set) var entries: [Entry] = []
    private let fileURL: URL
    private let iso = ISO8601DateFormatter()

    init(directory: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]) {
        fileURL = directory.appendingPathComponent("custody-ledger.jsonl")
        load()
    }

    @discardableResult
    func append(kind: String, payload: Data, deviceId: String, now: Date = Date()) -> Entry {
        let payloadHash = Self.hex(SHA256.hash(data: payload))
        let prev = entries.last?.chainHash ?? "genesis"
        let seq = entries.count
        let chainInput = "\(prev)|\(payloadHash)|\(seq)|\(iso.string(from: now))"
        let chainHash = Self.hex(SHA256.hash(data: Data(chainInput.utf8)))
        let entry = Entry(id: UUID(), sequence: seq, timestamp: now, kind: kind,
                          byteCount: payload.count, sha256: payloadHash, deviceId: deviceId,
                          previousHash: prev, chainHash: chainHash)
        entries.append(entry)
        persist(entry)
        return entry
    }

    /// Recomputes every chain hash. Returns the position (0-based) of the first
    /// entry that breaks the chain, or nil if the ledger is intact.
    func verify() -> Int? {
        var prev = "genesis"
        for (position, e) in entries.enumerated() {
            let input = "\(prev)|\(e.sha256)|\(e.sequence)|\(iso.string(from: e.timestamp))"
            let expected = Self.hex(SHA256.hash(data: Data(input.utf8)))
            if e.sequence != position || e.previousHash != prev || e.chainHash != expected {
                return position
            }
            prev = e.chainHash
        }
        return nil
    }

    // MARK: - Persistence

    private func persist(_ entry: Entry) {
        guard let line = try? JSONEncoder.ledger.encode(entry) else { return }
        try? FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        if let handle = try? FileHandle(forWritingTo: fileURL) {
            handle.seekToEndOfFile()
            handle.write(line + Data("\n".utf8))
            try? handle.close()
        } else {
            try? (line + Data("\n".utf8)).write(to: fileURL)
        }
    }

    private func load() {
        guard let text = try? String(contentsOf: fileURL) else { return }
        entries = text.split(separator: "\n").compactMap {
            try? JSONDecoder.ledger.decode(Entry.self, from: Data($0.utf8))
        }
    }

    private static func hex(_ digest: SHA256.Digest) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }
}

private extension JSONEncoder {
    static let ledger: JSONEncoder = { let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; return e }()
}
private extension JSONDecoder {
    static let ledger: JSONDecoder = { let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d }()
}
