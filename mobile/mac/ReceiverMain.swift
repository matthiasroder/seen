import Foundation
import CryptoKit
import CoreImage
import ImageIO
import UniformTypeIdentifiers

@main struct ReceiverMain {
    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            if args.contains("--help") || args.isEmpty {
                print("Seen local receiver\nUsage: SeenReceiver --bind PRIVATE_IPV4 [--port 8766] [--database PATH] [--state PATH]\nScan the generated pairing.png with your iPhone camera. Keep this process running while syncing.\nNo discovery, cloud, HTTP, or website requests. Transfer uses authenticated AES-GCM over local TCP.")
                return
            }
            func option(_ name: String) -> String? {
                guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }; return args[index + 1]
            }
            guard let address = option("--bind"), Pairing.privateAddress(address), let port = UInt16(option("--port") ?? "8766"), port > 0 else { throw SeenError.invalid("Choose your Mac's private IPv4 address with --bind. No public or wildcard listening address is allowed.") }
            let root = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.deletingLastPathComponent().deletingLastPathComponent()
            let database = option("--database").map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("data/seen.sqlite")
            let state = option("--state").map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("mobile-state", isDirectory: true)
            try FileManager.default.createDirectory(at: state, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let configURL = state.appendingPathComponent("pairing.json")
            let pairing: Pairing
            if FileManager.default.fileExists(atPath: configURL.path) {
                pairing = try JSONDecoder().decode(Pairing.self, from: Data(contentsOf: configURL))
                try pairing.validate()
                try require(pairing.host == address && pairing.port == port, "This pairing belongs to a different address. Use a new --state directory and re-pair explicitly.")
            } else {
                let key = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0).base64EncodedString() }
                pairing = Pairing(host: address, port: port, key: key)
                try JSONEncoder().encode(pairing).write(to: configURL, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
            }
            let receiver = try SeenReceiver(pairing: pairing, database: database); try receiver.start()
            let filter = CIFilter(name: "CIQRCodeGenerator")!
            filter.setValue(Data(pairing.url.absoluteString.utf8), forKey: "inputMessage")
            filter.setValue("M", forKey: "inputCorrectionLevel")
            guard let code = filter.outputImage else { throw SeenError.invalid("Could not render pairing QR.") }
            let padded = code.composited(over: CIImage(color: .white).cropped(to: code.extent.insetBy(dx: -4, dy: -4)))
            let image = padded.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
            guard let bitmap = CIContext().createCGImage(image, from: image.extent) else { throw SeenError.invalid("Could not render pairing QR.") }
            let imageURL = state.appendingPathComponent("pairing.png")
            guard let destination = CGImageDestinationCreateWithURL(imageURL as CFURL, UTType.png.identifier as CFString, 1, nil) else { throw SeenError.invalid("Cannot create pairing QR.") }
            CGImageDestinationAddImage(destination, bitmap, nil)
            try require(CGImageDestinationFinalize(destination), "Cannot save pairing QR.")
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: imageURL.path)
            print("Seen receiver listening on \(address):\(port).\nDatabase: \(database.path)\nPrivate pairing QR: \(imageURL.path)\nTreat the QR like a password. No captures or pairing key are written to this log.")
            withExtendedLifetime(receiver) { RunLoop.current.run() }
        } catch { fputs("Seen: \(error.localizedDescription)\n", stderr); exit(1) }
    }
}
