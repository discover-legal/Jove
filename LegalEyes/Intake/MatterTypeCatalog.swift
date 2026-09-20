import Foundation

/// Matter types and their per-matter custom fields.
/// Source of truth is `Resources/MatterTypes.json` (edit that file to change
/// defaults). The firm API `/v1/config` may supply a newer catalog at runtime,
/// which is cached and takes precedence when its `version` is higher.
struct MatterTypeCatalog: Codable, Equatable {
    struct Field: Codable, Equatable, Identifiable {
        enum Kind: String, Codable { case text, date, currency, number, bool, choice }
        let key: String
        let label: String
        let type: Kind
        var options: [String]? = nil
        var required: Bool? = nil
        var id: String { key }
    }

    struct MatterType: Codable, Equatable, Identifiable {
        let id: String
        let label: String
        let category: String
        var fields: [Field]? = nil
    }

    let version: Int
    let categories: [String]
    let matterTypes: [MatterType]

    func types(in category: String) -> [MatterType] {
        matterTypes.filter { $0.category == category }
    }

    subscript(id: String) -> MatterType? { matterTypes.first { $0.id == id } }

    // MARK: - Loading

    static let bundled: MatterTypeCatalog = {
        guard let url = Bundle.main.url(forResource: "MatterTypes", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalog = try? JSONDecoder().decode(MatterTypeCatalog.self, from: data)
        else { fatalError("MatterTypes.json missing or invalid") }
        return catalog
    }()

    private static var cacheURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("matter-types-remote.json")
    }

    /// Bundled defaults unless a newer remote catalog has been cached.
    static func current() -> MatterTypeCatalog {
        if let data = try? Data(contentsOf: cacheURL),
           let remote = try? JSONDecoder().decode(MatterTypeCatalog.self, from: data),
           remote.version > bundled.version {
            return remote
        }
        return bundled
    }

    /// Called by the config fetch after `/v1/config` returns a catalog.
    static func cache(remote: MatterTypeCatalog) throws {
        let data = try JSONEncoder().encode(remote)
        try FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try data.write(to: cacheURL, options: .atomic)
    }
}
