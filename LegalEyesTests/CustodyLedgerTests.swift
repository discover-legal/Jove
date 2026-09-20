import XCTest
@testable import LegalEyes

final class CustodyLedgerTests: XCTestCase {
    private func tempDir() -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    func testChainVerifiesAndPersists() {
        let dir = tempDir()
        let ledger = CustodyLedger(directory: dir)
        ledger.append(kind: "test", payload: Data("a".utf8), deviceId: "d1")
        ledger.append(kind: "test", payload: Data("b".utf8), deviceId: "d1")
        XCTAssertNil(ledger.verify())
        XCTAssertEqual(ledger.entries[1].previousHash, ledger.entries[0].chainHash)

        let reloaded = CustodyLedger(directory: dir)
        XCTAssertEqual(reloaded.entries.count, 2)
        XCTAssertNil(reloaded.verify())
    }

    func testTamperingIsDetected() {
        let dir = tempDir()
        let ledger = CustodyLedger(directory: dir)
        ledger.append(kind: "test", payload: Data("a".utf8), deviceId: "d1")
        ledger.append(kind: "test", payload: Data("b".utf8), deviceId: "d1")
        let file = dir.appendingPathComponent("custody-ledger.jsonl")
        var lines = try! String(contentsOf: file).split(separator: "\n").map(String.init)
        lines.swapAt(0, 1)
        try! lines.joined(separator: "\n").write(to: file, atomically: true, encoding: .utf8)
        XCTAssertEqual(CustodyLedger(directory: dir).verify(), 0)
    }
}
