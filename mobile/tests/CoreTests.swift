import Foundation
import CryptoKit
import Network

// Synthetic data only. All databases and pairing material live in a fresh temp directory.
@main struct CoreTests {
    static var checks = 0
    static func check(_ test: @autoclosure () throws -> Bool, _ label: String) throws {
        try require(try test(), "FAIL: " + label); checks += 1
    }
    static func rejects(_ label: String, _ action: () throws -> Void) throws {
        do { try action() } catch { checks += 1; return }
        throw SeenError.invalid("FAIL: accepted " + label)
    }
    static func fixture(_ payload: Data, id: String = UUID().uuidString.lowercased()) -> [String: Any] {
        ["id": id, "hash": digest(payload), "bytes": payload.count,
         "chunks": (payload.count + seenChunkBytes - 1) / seenChunkBytes,
         "url": "https://example.invalid/feed?q=one#two", "topUrl": "https://example.invalid/feed?q=one#two",
         "title": "Österreich 🐘\0end", "capturedAt": 1_788_205_500_000,
         "tabId": 7, "frameId": 0, "documentId": "safari-ios/synthetic-device/document"]
    }
    static func part(_ bytes: Data, _ index: Int) -> String {
        bytes.subdata(in: (index * seenChunkBytes)..<min(bytes.count, (index + 1) * seenChunkBytes)).base64EncodedString()
    }
    static func insert(_ store: SeenStore, _ metadata: [String: Any], _ bytes: Data) throws {
        let id = metadata["id"] as! String
        let start = try store.handle(["op": "begin", "snapshot": metadata])
        if start["saved"] as? Bool == true { return }
        for index in (start["nextIndex"] as! Int)..<(metadata["chunks"] as! Int) {
            _ = try store.handle(["op": "chunk", "snapshotId": id, "index": index, "data": part(bytes,index)])
        }
        _ = try store.handle(["op": "commit", "snapshotId": id])
    }
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("seen-mobile-test-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let currentPath = root.appendingPathComponent("current.sqlite")
        do {
            let current = try SeenDB(currentPath)
            try current.run("PRAGMA user_version=3")
        }
        let current = try SeenStore(path: currentPath)
        try check(try current.db.run("PRAGMA user_version").first?["user_version"] as? Int == 3, "current Mac schema remains version 3")
        let futurePath = root.appendingPathComponent("future.sqlite")
        do {
            let future = try SeenDB(futurePath)
            try future.run("PRAGMA user_version=4")
        }
        try rejects("future database version") { _ = try SeenStore(path: futurePath) }
        let path = root.appendingPathComponent("phone.sqlite")
        var phone: SeenStore? = try SeenStore(path: path)
        let bytes = try jsonData(["html": "<!doctype html><!--hidden--><html><body><script>const x=1</script><textarea>old</textarea>" + String(repeating: "ä🐘", count: 24000) + "</body></html>", "shadowRoots": [["path": [0,1], "html": "<b>shadow</b>"]], "formState": [["path": [0,2], "value": "live value"]]])
        let item = fixture(bytes), id = item["id"] as! String
        let hash = item["hash"] as! String
        _ = try phone!.handle(["op": "begin", "snapshot": item])
        try rejects("incomplete commit") { _ = try phone!.handle(["op": "commit", "snapshotId": id]) }
        try rejects("out of order chunk") { _ = try phone!.handle(["op": "chunk", "snapshotId": id, "index": 1, "data": part(bytes,1)]) }
        _ = try phone!.handle(["op": "chunk", "snapshotId": id, "index": 0, "data": part(bytes,0)])
        let device = try phone!.deviceID()
        phone = nil // Close and reopen: resume is durable, not process memory.
        phone = try SeenStore(path: path)
        try check(try phone!.deviceID() == device, "stable device identity")
        try check(try phone!.handle(["op": "begin", "snapshot": item])["nextIndex"] as? Int == 1, "resume index survives restart")
        try insert(phone!, item, bytes)
        try check(try phone!.existing(id)?["title"] as? String == item["title"] as? String, "metadata preserves Unicode and NUL")
        try check(try phone!.db.run("SELECT payload FROM doms").first?["payload"] as? String == String(data: bytes, encoding: .utf8), "complete DOM bytes unchanged")
        try check(try phone!.db.run("SELECT html FROM documents").first?["html"] as? String == object(bytes)["html"] as? String, "desktop-compatible documents view")
        try check(try phone!.handle(["op": "begin", "snapshot": item])["saved"] as? Bool == true, "idempotent begin")
        try check(try phone!.handle(["op": "commit", "snapshotId": id])["hash"] as? String == hash, "idempotent commit")
        var collision = item; collision["title"] = "changed"
        try rejects("metadata collision") { _ = try phone!.handle(["op": "begin", "snapshot": collision]) }
        let second = fixture(bytes)
        try insert(phone!, second, bytes)
        try check(try phone!.db.run("SELECT count(*) AS n FROM doms").first?["n"] as? Int == 1, "DOM deduplication")
        try rejects("wrong acknowledgement") { try phone!.acknowledge(id, String(repeating: "0", count: 64)) }
        try phone!.acknowledge(second["id"] as! String,hash)
        try check(try phone!.existing(id) != nil, "ack preserves other snapshots sharing DOM")
        try rejects("archive read through transfer protocol") { _ = try phone!.handle(["op": "get", "snapshotId": id]) }
        try rejects("archive clear through transfer protocol") { _ = try phone!.handle(["op": "clear"]) }
        var corrupt = fixture(bytes); corrupt["hash"] = String(repeating: "0", count: 64)
        try rejects("corrupt DOM") { try insert(phone!, corrupt, bytes) }
        try check(try phone!.existing(corrupt["id"] as! String) == nil, "corrupt DOM never committed")

        let key = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0).base64EncodedString() }
        let lan = Pairing(host: "192.168.1.27", port: 8766, key: key)
        try check(try Pairing(url: lan.url).key == key, "pairing QR URL roundtrip")
        for bad in ["example.com", "8.8.8.8", "0.0.0.0", "127.0.0.1", "192.168.01.1", "169.254.1.2", "172.32.0.1"] {
            try check(!Pairing.privateAddress(bad), "reject nonprivate/ambiguous endpoint " + bad)
        }
        try check(Pairing.privateAddress("10.0.0.1") && Pairing.privateAddress("172.16.0.1"), "RFC1918 endpoints")
        try rejects("duplicate pairing fields") { _ = try Pairing(url: URL(string: lan.url.absoluteString + "&host=10.0.0.1")!) }
        let encrypted = try Wire.seal(["op": "test", "content": "synthetic-private-content"],lan,direction: "request")
        try check(try Wire.open(encrypted,lan,direction: "request")["content"] as? String == "synthetic-private-content", "authenticated wire roundtrip")
        try check(encrypted.range(of: Data("synthetic-private-content".utf8)) == nil, "no plaintext payload on wire")
        var damaged = encrypted; damaged[damaged.count - 1] ^= 1
        try rejects("tampered ciphertext") { _ = try Wire.open(damaged,lan,direction: "request") }
        try rejects("reflected response") { _ = try Wire.open(encrypted,lan,direction: "response") }
        let wrong = Pairing(host: lan.host,port: lan.port,key: Data(repeating: 0, count: 32).base64EncodedString())
        try rejects("wrong pairing key") { _ = try Wire.open(encrypted,wrong,direction: "request") }

        // Real socket integration, loopback only. The production CLI never accepts loopback.
        let macPath = root.appendingPathComponent("mac.sqlite")
        var receiver: SeenReceiver?
        var pair: Pairing!
        for _ in 0..<10 {
            pair = Pairing(host: "127.0.0.1", port: UInt16.random(in: 30000...59000), key: key)
            do { receiver = try SeenReceiver(pairing: pair,database: macPath,loopback: true); try receiver!.start(); break }
            catch { receiver?.stop(); receiver = nil }
        }
        try require(receiver != nil, "Cannot start loopback test receiver.")
        defer { receiver?.stop() }
        let wrongLoopback = Pairing(host: pair.host, port: pair.port, key: wrong.key)
        try rejects("unauthenticated network writer") {
            let client = SeenChannel(NWConnection(host: .init(pair.host),port: .init(rawValue: pair.port)!,using: .tcp))
            try client.start(); defer { client.connection.cancel() }
            _ = try client.request(["op": "begin", "snapshot": item],pairing: wrongLoopback)
        }
        try check(!FileManager.default.fileExists(atPath: macPath.path), "authentication before database creation")
        let mac = try SeenStore(path: macPath)
        _ = try mac.handle(["op": "begin", "snapshot": item])
        _ = try mac.handle(["op": "chunk", "snapshotId": id, "index": 0, "data": part(bytes,0)])
        try check(try syncOne(store: phone!,pairing: pair,lockURL: root.appendingPathComponent("delivery.lock"),loopback: true), "network resume and commit")
        try check(try mac.existing(id)?["hash"] as? String == hash, "Mac acknowledged correct digest")
        try check(try phone!.existing(id) == nil, "phone deletes only after acknowledgement")
        try check(try mac.db.run("SELECT payload FROM doms").first?["payload"] as? String == String(data: bytes,encoding: .utf8), "network DOM byte identity")
        try insert(phone!, item, bytes) // Simulate death after Mac commit but before phone ack.
        try check(try syncOne(store: phone!,pairing: pair,lockURL: root.appendingPathComponent("delivery.lock"),loopback: true), "lost ack safely retried")
        try check(try mac.db.run("SELECT count(*) AS n FROM snapshots").first?["n"] as? Int == 1, "retry does not duplicate archive")
        try insert(phone!, item, bytes)
        receiver?.stop()
        try rejects("offline delivery") { _ = try syncOne(store: phone!,pairing: pair,lockURL: root.appendingPathComponent("delivery.lock"),loopback: true) }
        try check(try phone!.existing(id) != nil, "offline capture retained")
        print("PASS: \(checks) mobile storage, crypto and loopback checks (synthetic data only).")
    }
}
