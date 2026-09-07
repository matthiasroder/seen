import Foundation
import CryptoKit
import SQLite3

enum SeenError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case let .invalid(text) = self { return text }; return nil }
}
func require(_ condition: Bool, _ message: String) throws { if !condition { throw SeenError.invalid(message) } }
func jsonData(_ object: Any) throws -> Data { try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]) }
func object(_ data: Data) throws -> [String: Any] {
    guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw SeenError.invalid("Invalid message.") }
    return result
}
func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func number(_ value: Any?, minimum: Int = 0) throws -> Int {
    guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite,
          n.doubleValue.rounded() == n.doubleValue, n.doubleValue >= Double(minimum), n.doubleValue < 9_007_199_254_740_992 else { throw SeenError.invalid("Invalid integer.") }
    return n.intValue
}
func identifier(_ value: Any?) throws -> String {
    guard let value = value as? String, UUID(uuidString: value) != nil else { throw SeenError.invalid("Invalid capture identifier.") }
    return value.lowercased()
}
let seenChunkBytes = 65536
let seenGroup = "group.com.matthiasroder.SeenMobile"

final class SeenDB {
    private var db: OpaquePointer?
    init(_ path: URL) throws {
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        guard sqlite3_open_v2(path.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else { throw SeenError.invalid("Cannot open local Seen database.") }
        sqlite3_busy_timeout(db, 15000)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
    }
    deinit { sqlite3_close(db) }
    private func statement(_ sql: String, _ values: [Any]) throws -> OpaquePointer {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else { throw SeenError.invalid("SQLite prepare failed: " + String(cString: sqlite3_errmsg(db))) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for (offset, value) in values.enumerated() {
            let index = Int32(offset + 1)
            if let data = value as? Data {
                _ = data.withUnsafeBytes { sqlite3_bind_blob(statement, index, $0.baseAddress, Int32(data.count), transient) }
            } else if let text = value as? String { sqlite3_bind_text(statement, index, text, Int32(text.utf8.count), transient) }
            else if let value = value as? Int { sqlite3_bind_int64(statement, index, Int64(value)) }
            else { sqlite3_finalize(statement); throw SeenError.invalid("Unsupported database value.") }
        }
        return statement
    }
    @discardableResult func run(_ sql: String, _ values: [Any] = []) throws -> [[String: Any]] {
        let stmt = try statement(sql, values); defer { sqlite3_finalize(stmt) }
        var result: [[String: Any]] = []
        while true {
            let status = sqlite3_step(stmt)
            if status == SQLITE_DONE { return result }
            guard status == SQLITE_ROW else { throw SeenError.invalid("SQLite operation failed: " + String(cString: sqlite3_errmsg(db))) }
            var row: [String: Any] = [:]
            for column in 0..<sqlite3_column_count(stmt) {
                let name = String(cString: sqlite3_column_name(stmt, column))
                switch sqlite3_column_type(stmt, column) {
                case SQLITE_INTEGER: row[name] = Int(sqlite3_column_int64(stmt, column))
                case SQLITE_TEXT:
                    row[name] = String(decoding: UnsafeBufferPointer(start: sqlite3_column_text(stmt, column), count: Int(sqlite3_column_bytes(stmt, column))), as: UTF8.self)
                case SQLITE_BLOB: row[name] = Data(bytes: sqlite3_column_blob(stmt, column), count: Int(sqlite3_column_bytes(stmt, column)))
                default: break
                }
            }
            result.append(row)
        }
    }
    func transaction<T>(_ work: () throws -> T) throws -> T {
        try run("BEGIN IMMEDIATE")
        do { let result = try work(); try run("COMMIT"); return result }
        catch { _ = try? run("ROLLBACK"); throw error }
    }
}

final class SeenStore {
    let db: SeenDB
    init(path: URL) throws {
        db = try SeenDB(path)
        let version = try db.run("PRAGMA user_version").first?["user_version"] as? Int ?? -1
        try require((0...3).contains(version), "Unsupported Seen database version.")
        try db.run("PRAGMA foreign_keys=ON"); try db.run("PRAGMA journal_mode=WAL"); try db.run("PRAGMA synchronous=FULL")
        for sql in [
            "CREATE TABLE IF NOT EXISTS doms(hash TEXT PRIMARY KEY,payload TEXT NOT NULL CHECK(json_valid(payload)),bytes INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,url TEXT NOT NULL,top_url TEXT NOT NULL,title TEXT NOT NULL,captured_at INTEGER NOT NULL,tab_id INTEGER NOT NULL,frame_id INTEGER NOT NULL,document_id TEXT NOT NULL,dom_hash TEXT NOT NULL REFERENCES doms(hash))",
            "CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(captured_at DESC)",
            "CREATE INDEX IF NOT EXISTS snapshots_url ON snapshots(url)",
            "CREATE VIEW IF NOT EXISTS documents AS SELECT hash,bytes,json_extract(payload,'$.html') AS html,json_extract(payload,'$.shadowRoots') AS shadow_roots_json,json_extract(payload,'$.formState') AS form_state_json FROM doms",
            "CREATE TABLE IF NOT EXISTS mobile_uploads(id TEXT PRIMARY KEY,metadata TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS mobile_parts(id TEXT NOT NULL,part_index INTEGER NOT NULL,data BLOB NOT NULL,PRIMARY KEY(id,part_index))",
            "CREATE TABLE IF NOT EXISTS mobile_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL)"
        ] { try db.run(sql) }
        if version == 0 { try db.run("PRAGMA user_version=1") }
    }
    func setting(_ name: String) throws -> String? { try db.run("SELECT value FROM mobile_settings WHERE name=?", [name]).first?["value"] as? String }
    func set(_ name: String, _ value: String) throws { try db.run("INSERT OR REPLACE INTO mobile_settings VALUES (?,?)", [name, value]) }
    func deviceID() throws -> String {
        try db.transaction {
            if let value = try setting("device") { return value }
            let value = UUID().uuidString.lowercased(); try set("device", value); return value
        }
    }
    func status() throws -> [String: Any] {
        ["paused": try setting("paused") == "true", "paired": try setting("pairing") != nil,
         "queued": try db.run("SELECT count(*) AS n FROM snapshots").first?["n"] ?? 0,
         "incomplete": try db.run("SELECT count(*) AS n FROM mobile_uploads").first?["n"] ?? 0,
         "bytes": try db.run("SELECT coalesce(sum(bytes),0) AS n FROM doms").first?["n"] ?? 0,
         "lastError": try setting("error") ?? "", "lastSync": try setting("lastSync") ?? ""]
    }
    func validate(_ item: [String: Any]) throws -> String {
        let id = try identifier(item["id"])
        guard let hash = item["hash"] as? String, hash.count == 64, hash.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw SeenError.invalid("Invalid capture digest.") }
        let bytes = try number(item["bytes"], minimum: 1), chunks = try number(item["chunks"], minimum: 1)
        try require(chunks == (bytes + seenChunkBytes - 1) / seenChunkBytes, "Invalid chunk count.")
        for name in ["url", "topUrl", "title", "documentId"] { try require(item[name] is String, "Invalid capture metadata.") }
        for name in ["capturedAt", "tabId", "frameId"] { _ = try number(item[name]) }
        return id
    }
    func metadata(_ row: [String: Any]) -> [String: Any] {
        ["id": row["id"]!, "url": row["url"]!, "topUrl": row["top_url"]!, "title": row["title"]!,
         "capturedAt": row["captured_at"]!, "tabId": row["tab_id"]!, "frameId": row["frame_id"]!,
         "documentId": row["document_id"]!, "hash": row["dom_hash"]!, "bytes": row["bytes"]!,
         "chunks": ((row["bytes"] as! Int) + seenChunkBytes - 1) / seenChunkBytes]
    }
    func existing(_ id: String) throws -> [String: Any]? {
        try db.run("SELECT s.*,d.bytes FROM snapshots s JOIN doms d ON d.hash=s.dom_hash WHERE s.id=?", [id]).first.map(metadata)
    }
    func handle(_ message: [String: Any]) throws -> [String: Any] {
        switch message["op"] as? String {
        case "begin":
            guard let item = message["snapshot"] as? [String: Any] else { throw SeenError.invalid("Missing capture metadata.") }
            let id = try validate(item)
            return try db.transaction {
                if let previous = try existing(id) {
                    for (key, value) in previous { try require(String(describing: item[key] ?? "") == String(describing: value), "Capture identifier collision.") }
                    return ["saved": true, "snapshotId": id, "hash": previous["hash"]!]
                }
                let encoded = String(decoding: try jsonData(item), as: UTF8.self)
                if let previous = try db.run("SELECT metadata FROM mobile_uploads WHERE id=?", [id]).first?["metadata"] as? String {
                    try require(previous == encoded, "In-progress capture identifier collision.")
                    let next = try db.run("SELECT count(*) AS n FROM mobile_parts WHERE id=?", [id]).first?["n"] as? Int ?? 0
                    return ["saved": false, "nextIndex": next]
                }
                try db.run("INSERT INTO mobile_uploads VALUES (?,?)", [id, encoded])
                return ["saved": false, "nextIndex": 0]
            }
        case "chunk":
            let id = try identifier(message["snapshotId"]), index = try number(message["index"])
            guard let encoded = message["data"] as? String, let data = Data(base64Encoded: encoded) else { throw SeenError.invalid("Invalid capture chunk.") }
            return try db.transaction {
                guard let raw = try db.run("SELECT metadata FROM mobile_uploads WHERE id=?", [id]).first?["metadata"] as? String else { throw SeenError.invalid("Capture was not started.") }
                let item = try object(Data(raw.utf8)), count = try db.run("SELECT count(*) AS n FROM mobile_parts WHERE id=?", [id]).first?["n"] as? Int
                let bytes = try number(item["bytes"], minimum: 1), chunks = try number(item["chunks"], minimum: 1)
                try require(index == count && index < chunks && data.count == min(seenChunkBytes, bytes - index * seenChunkBytes), "Incorrect chunk order or length.")
                try db.run("INSERT INTO mobile_parts VALUES (?,?,?)", [id, index, data]); return ["received": index]
            }
        case "commit":
            let id = try identifier(message["snapshotId"])
            return try db.transaction {
                if let previous = try existing(id) { return ["saved": true, "snapshotId": id, "hash": previous["hash"]!] }
                guard let raw = try db.run("SELECT metadata FROM mobile_uploads WHERE id=?", [id]).first?["metadata"] as? String else { throw SeenError.invalid("Capture was not started.") }
                let item = try object(Data(raw.utf8))
                let parts = try db.run("SELECT data FROM mobile_parts WHERE id=? ORDER BY part_index", [id])
                var bytes = Data(); for part in parts { bytes.append(part["data"] as! Data) }
                try require(parts.count == (item["chunks"] as? Int) && bytes.count == (item["bytes"] as? Int) && digest(bytes) == (item["hash"] as? String), "Incomplete or corrupt capture.")
                guard let text = String(data: bytes, encoding: .utf8) else { throw SeenError.invalid("DOM is not UTF-8.") }
                let dom = try object(bytes)
                try require(dom["html"] is String && dom["shadowRoots"] is [Any] && dom["formState"] is [Any], "Invalid DOM payload.")
                try db.run("INSERT OR IGNORE INTO doms VALUES (?,?,?)", [item["hash"]!, text, bytes.count])
                try db.run("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)", [id,item["url"]!,item["topUrl"]!,item["title"]!,item["capturedAt"]!,item["tabId"]!,item["frameId"]!,item["documentId"]!,item["hash"]!])
                try db.run("DELETE FROM mobile_parts WHERE id=?", [id]); try db.run("DELETE FROM mobile_uploads WHERE id=?", [id])
                return ["saved": true, "snapshotId": id, "hash": item["hash"]!]
            }
        default: throw SeenError.invalid("Unsupported transfer operation.")
        }
    }
    func next() throws -> [String: Any]? {
        try db.run("SELECT s.*,d.bytes FROM snapshots s JOIN doms d ON d.hash=s.dom_hash ORDER BY captured_at,id LIMIT 1").first.map(metadata)
    }
    func chunk(_ id: String, _ index: Int) throws -> Data {
        guard let data = try db.run("SELECT substr(CAST(d.payload AS BLOB),?,?) AS data FROM snapshots s JOIN doms d ON d.hash=s.dom_hash WHERE s.id=?", [index * seenChunkBytes + 1,seenChunkBytes,id]).first?["data"] as? Data else { throw SeenError.invalid("Queued capture unavailable.") }
        return data
    }
    func acknowledge(_ id: String, _ hash: String) throws {
        try db.transaction {
            guard let item = try existing(id) else { return }
            try require(item["hash"] as? String == hash, "Acknowledgement digest mismatch.")
            try db.run("DELETE FROM snapshots WHERE id=?", [id])
            try db.run("DELETE FROM doms WHERE NOT EXISTS(SELECT 1 FROM snapshots WHERE dom_hash=doms.hash)")
        }
    }
}

struct Pairing: Codable {
    let host: String
    let port: UInt16
    let key: String
    var symmetricKey: SymmetricKey { SymmetricKey(data: Data(base64Encoded: key)!) }
    static func privateAddress(_ host: String, loopback: Bool = false) -> Bool {
        let pieces = host.split(separator: ".", omittingEmptySubsequences: false)
        guard pieces.count == 4, pieces.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) && UInt8($0) != nil && String(Int($0)!) == $0 }) else { return false }
        let octets = pieces.map { Int($0)! }
        return octets[0] == 10 || (octets[0] == 172 && (16...31).contains(octets[1])) || (octets[0] == 192 && octets[1] == 168) || (loopback && host == "127.0.0.1")
    }
    func validate(loopback: Bool = false) throws {
        try require(Self.privateAddress(host, loopback: loopback) && port > 0 && Data(base64Encoded: key)?.count == 32, "Pairing needs a private IPv4 address, port, and 256-bit pairing key.")
    }
    init(host: String, port: UInt16, key: String) { self.host = host; self.port = port; self.key = key }
    init(url: URL) throws {
        let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        try require(parts?.scheme == "seen-pair" && parts?.host == "pair", "Not a Seen pairing code.")
        let items = parts?.queryItems ?? []
        try require(Set(items.map(\.name)).count == items.count, "Ambiguous pairing code.")
        func value(_ name: String) -> String { items.first { $0.name == name }?.value ?? "" }
        guard let port = UInt16(value("port")) else { throw SeenError.invalid("Invalid pairing port.") }
        self.init(host: value("host"), port: port, key: value("key")); try validate()
    }
    var url: URL {
        var parts = URLComponents(); parts.scheme = "seen-pair"; parts.host = "pair"
        parts.queryItems = [URLQueryItem(name: "host", value: host), URLQueryItem(name: "port", value: String(port)), URLQueryItem(name: "key", value: key)]
        return parts.url!
    }
}

enum Wire {
    static let maxFrame = 1_000_000
    static func seal(_ object: [String: Any], _ pairing: Pairing, direction: String) throws -> Data {
        let data = try jsonData(object)
        let box = try AES.GCM.seal(data, using: pairing.symmetricKey, authenticating: Data(("seen-v1:" + direction).utf8))
        guard let combined = box.combined else { throw SeenError.invalid("Encryption failed.") }
        try require(combined.count <= maxFrame, "Transfer message is too large.")
        return combined
    }
    static func open(_ data: Data, _ pairing: Pairing, direction: String) throws -> [String: Any] {
        try require(data.count <= maxFrame, "Transfer message is too large.")
        let plaintext = try AES.GCM.open(AES.GCM.SealedBox(combined: data), using: pairing.symmetricKey, authenticating: Data(("seen-v1:" + direction).utf8))
        return try object(plaintext)
    }
}
