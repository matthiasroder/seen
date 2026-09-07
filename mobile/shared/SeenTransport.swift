import Foundation
import Network
import Darwin

final class SeenChannel {
    let connection: NWConnection
    private let queue = DispatchQueue(label: "seen.connection." + UUID().uuidString)
    init(_ connection: NWConnection) { self.connection = connection }
    deinit { connection.cancel() }
    func start() throws {
        let ready = DispatchSemaphore(value: 0)
        let lock = NSLock(); var completed = false; var failure: Error?
        connection.stateUpdateHandler = { state in
            lock.lock(); defer { lock.unlock() }
            if completed { return }
            switch state {
            case .ready: completed = true; ready.signal()
            case .failed(let error): failure = error; completed = true; ready.signal()
            case .cancelled: failure = SeenError.invalid("Transfer cancelled."); completed = true; ready.signal()
            default: break
            }
        }
        connection.start(queue: queue)
        guard ready.wait(timeout: .now() + 5) == .success else { connection.cancel(); throw SeenError.invalid("Mac is unavailable. Capture stays queued.") }
        if let failure { throw failure }
    }
    func send(_ bytes: Data) throws {
        try require(bytes.count <= Wire.maxFrame, "Transfer frame too large.")
        var length = UInt32(bytes.count).bigEndian
        var framed = withUnsafeBytes(of: &length) { Data($0) }; framed.append(bytes)
        let done = DispatchSemaphore(value: 0); var failure: Error?
        connection.send(content: framed, completion: .contentProcessed { error in failure = error; done.signal() })
        guard done.wait(timeout: .now() + 5) == .success else { connection.cancel(); throw SeenError.invalid("Transfer send timed out.") }
        if let failure { throw failure }
    }
    private func read(_ size: Int) throws -> Data {
        var result = Data()
        while result.count < size {
            let done = DispatchSemaphore(value: 0); var received: Data?; var failure: Error?; var ended = false
            connection.receive(minimumIncompleteLength: 1, maximumLength: size - result.count) { data, _, complete, error in
                received = data; ended = complete; failure = error; done.signal()
            }
            guard done.wait(timeout: .now() + 5) == .success else { connection.cancel(); throw SeenError.invalid("Transfer receive timed out.") }
            if let failure { throw failure }
            if let received { result.append(received) }
            if ended && result.count < size || received?.isEmpty != false { throw SeenError.invalid("Transfer ended before acknowledgement.") }
        }
        return result
    }
    func receive() throws -> Data {
        let header = try read(4)
        let count = header.reduce(0) { ($0 << 8) | Int($1) }
        try require(count >= 28 && count <= Wire.maxFrame, "Invalid encrypted frame length.")
        return try read(count)
    }
    func request(_ message: [String: Any], pairing: Pairing) throws -> [String: Any] {
        let id = UUID().uuidString.lowercased()
        var envelope = message; envelope["requestId"] = id
        try send(Wire.seal(envelope, pairing, direction: "request"))
        let response = try Wire.open(receive(), pairing, direction: "response")
        try require(response["requestId"] as? String == id, "Reply does not match this request.")
        try require(response["ok"] as? Bool == true, response["error"] as? String ?? "Mac rejected the capture.")
        guard let value = response["value"] as? [String: Any] else { throw SeenError.invalid("Invalid Mac acknowledgement.") }
        return value
    }
}

final class SeenReceiver {
    let listener: NWListener
    private let pairing: Pairing
    private let database: URL
    private let gate = DispatchSemaphore(value: 4)
    init(pairing: Pairing, database: URL, loopback: Bool = false) throws {
        try pairing.validate(loopback: loopback)
        self.pairing = pairing; self.database = database
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: NWEndpoint.Host(pairing.host), port: NWEndpoint.Port(rawValue: pairing.port)!)
        listener = try NWListener(using: parameters)
    }
    func start() throws {
        let done = DispatchSemaphore(value: 0); var failure: Error?
        listener.stateUpdateHandler = { state in
            switch state { case .ready: done.signal(); case .failed(let error): failure = error; done.signal(); default: break }
        }
        listener.newConnectionHandler = { [self] connection in
            guard gate.wait(timeout: .now()) == .success else { connection.cancel(); return }
            DispatchQueue.global(qos: .utility).async { [self] in
                defer { gate.signal(); connection.cancel() }
                do {
                    let channel = SeenChannel(connection); try channel.start()
                    // Authenticate before opening or changing the database.
                    let deadline = Date().addingTimeInterval(30)
                    var store: SeenStore?
                    while Date() < deadline {
                        let message = try Wire.open(channel.receive(), pairing, direction: "request")
                        let requestId = try identifier(message["requestId"])
                        var reply: [String: Any] = ["requestId": requestId]
                        do {
                            if message["op"] as? String == "begin" {
                                let metadata = message["snapshot"] as? [String: Any]
                                try require((metadata?["documentId"] as? String)?.hasPrefix("safari-ios/") == true, "Not an iPhone capture.")
                            }
                            // Only begin/chunk/commit are exposed: never read, clear, or delete.
                            if store == nil { store = try SeenStore(path: database) }
                            reply["value"] = try store!.handle(message); reply["ok"] = true
                        } catch { reply["ok"] = false; reply["error"] = error.localizedDescription }
                        try channel.send(Wire.seal(reply, pairing, direction: "response"))
                    }
                } catch { /* Disconnect without logging any payload, address, or pairing material. */ }
            }
        }
        listener.start(queue: DispatchQueue(label: "seen.listener"))
        guard done.wait(timeout: .now() + 5) == .success else { listener.cancel(); throw SeenError.invalid("Cannot start the local receiver.") }
        if let failure { throw failure }
    }
    func stop() { listener.cancel() }
}

func syncOne(store: SeenStore, pairing: Pairing, lockURL: URL, loopback: Bool = false) throws -> Bool {
    try pairing.validate(loopback: loopback)
    let descriptor = Darwin.open(lockURL.path, O_CREAT | O_RDWR, 0o600)
    try require(descriptor >= 0, "Cannot lock the delivery queue.")
    defer { Darwin.close(descriptor) }
    guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { return false }
    defer { flock(descriptor, LOCK_UN) }
    guard let item = try store.next() else { return false }
    let parameters = NWParameters.tcp; parameters.prohibitedInterfaceTypes = [.cellular]
    let channel = SeenChannel(NWConnection(host: NWEndpoint.Host(pairing.host), port: NWEndpoint.Port(rawValue: pairing.port)!, using: parameters))
    try channel.start(); defer { channel.connection.cancel() }
    let deadline = Date().addingTimeInterval(20)
    var reply = try channel.request(["op": "begin", "snapshot": item], pairing: pairing)
    let id = item["id"] as! String, hash = item["hash"] as! String
    if reply["saved"] as? Bool != true {
        let next = try number(reply["nextIndex"] ?? 0)
        try require(next <= (item["chunks"] as! Int), "Mac returned an invalid resume offset.")
        for index in next..<(item["chunks"] as! Int) {
            try require(Date() < deadline, "Transfer time limit reached. Capture stays queued.")
            _ = try channel.request(["op": "chunk", "snapshotId": id, "index": index, "data": try store.chunk(id, index).base64EncodedString()], pairing: pairing)
        }
        reply = try channel.request(["op": "commit", "snapshotId": id], pairing: pairing)
    }
    try require(reply["saved"] as? Bool == true && reply["snapshotId"] as? String == id && reply["hash"] as? String == hash, "Mac did not acknowledge this complete capture.")
    try store.acknowledge(id, hash)
    try store.set("lastSync", ISO8601DateFormatter().string(from: Date())); try store.set("error", "")
    return true
}

#if os(iOS)
enum PhoneStore {
    static func directory() throws -> URL {
        guard var url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: seenGroup)?.appendingPathComponent("Library/Application Support/Seen", isDirectory: true) else { throw SeenError.invalid("Seen app group is not signed correctly.") }
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        var values = URLResourceValues(); values.isExcludedFromBackup = true; try url.setResourceValues(values)
        return url
    }
    static func open() throws -> SeenStore { try SeenStore(path: directory().appendingPathComponent("queue.sqlite")) }
    static func sync(_ store: SeenStore) throws -> Bool {
        guard let raw = try store.setting("pairing") else { return false }
        let pairing = try JSONDecoder().decode(Pairing.self, from: Data(raw.utf8))
        return try syncOne(store: store, pairing: pairing, lockURL: directory().appendingPathComponent("delivery.lock"))
    }
}
#endif
