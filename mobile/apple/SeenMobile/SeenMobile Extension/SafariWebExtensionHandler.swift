import SafariServices

final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        guard let request = context.inputItems.first as? NSExtensionItem,
              let message = request.userInfo?[SFExtensionMessageKey] as? [String: Any] else {
            context.completeRequest(returningItems: nil); return
        }
        DispatchQueue.global(qos: .utility).async {
            var response: [String: Any]
            do {
                let store = try PhoneStore.open()
                var input = message
                let value: [String: Any]
                switch message["op"] as? String {
                case "status": value = try store.status()
                case "pause":
                    guard let paused = message["paused"] as? Bool else { throw SeenError.invalid("Invalid pause state.") }
                    try store.set("paused", paused ? "true" : "false"); value = try store.status()
                case "sync":
                    do { value = ["delivered": try PhoneStore.sync(store)] }
                    catch { try? store.set("error", error.localizedDescription); throw error }
                case "begin", "chunk", "commit":
                    try require(try store.setting("paused") != "true", "Capture paused.")
                    if message["op"] as? String == "begin", var metadata = message["snapshot"] as? [String: Any] {
                        metadata["documentId"] = "safari-ios/" + (try store.deviceID()) + "/" + (metadata["documentId"] as? String ?? "")
                        input["snapshot"] = metadata
                    }
                    value = try store.handle(input)
                default: throw SeenError.invalid("Unsupported companion request.")
                }
                response = ["ok": true, "value": value]
            } catch { response = ["ok": false, "error": error.localizedDescription] }
            let item = NSExtensionItem(); item.userInfo = [SFExtensionMessageKey: response]
            context.completeRequest(returningItems: [item])
        }
    }
}
