import XCTest
@testable import LegalEyes

final class MatterTypeCatalogTests: XCTestCase {
    func testBundledCatalogLoadsAndIdsAreUnique() {
        let c = MatterTypeCatalog.bundled
        XCTAssertFalse(c.matterTypes.isEmpty)
        XCTAssertEqual(Set(c.matterTypes.map(\.id)).count, c.matterTypes.count)
        for t in c.matterTypes { XCTAssertTrue(c.categories.contains(t.category), t.id) }
    }
}
