// Build-time only. Render Seen's vector eye with macOS frameworks; no runtime dependency.
import AppKit

let project = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let space = CGColorSpaceCreateDeviceRGB()
let green = CGColor(red: 49/255, green: 91/255, blue: 70/255, alpha: 1)
let ivory = CGColor(red: 1, green: 254/255, blue: 250/255, alpha: 1)
let mobile = CommandLine.arguments.contains("--mobile")

func context(_ size: Int) -> CGContext {
    return CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
        bytesPerRow: size * 4, space: space,
        bitmapInfo: mobile ? CGImageAlphaInfo.noneSkipLast.rawValue : CGImageAlphaInfo.premultipliedLast.rawValue)!
}

for size in (mobile ? [1024] : [16, 24, 32, 48, 128]) {
    let drawing = context(size * 4)
    let scale = CGFloat(size * 4) / 128
    drawing.scaleBy(x: scale, y: scale)
    drawing.setFillColor(green)
    if mobile { drawing.fill(CGRect(x: 0, y: 0, width: 128, height: 128)) }
    else {
        drawing.addPath(CGPath(roundedRect: CGRect(x: 2, y: 2, width: 124, height: 124),
            cornerWidth: 30, cornerHeight: 30, transform: nil))
        drawing.fillPath()
    }

    let eye = CGMutablePath()
    eye.move(to: CGPoint(x: 23, y: 64))
    eye.addCurve(to: CGPoint(x: 105, y: 64), control1: CGPoint(x: 46, y: 97), control2: CGPoint(x: 82, y: 97))
    eye.addCurve(to: CGPoint(x: 23, y: 64), control1: CGPoint(x: 82, y: 31), control2: CGPoint(x: 46, y: 31))
    eye.closeSubpath()
    drawing.setStrokeColor(ivory)
    drawing.setLineWidth(8)
    drawing.setLineJoin(.round)
    drawing.addPath(eye)
    drawing.strokePath()
    drawing.setFillColor(ivory)
    drawing.fillEllipse(in: CGRect(x: 53, y: 53, width: 22, height: 22))

    let output = context(size)
    output.interpolationQuality = .high
    output.draw(drawing.makeImage()!, in: CGRect(x: 0, y: 0, width: size, height: size))
    let png = NSBitmapImageRep(cgImage: output.makeImage()!).representation(using: .png, properties: [:])!
    let file = project.appendingPathComponent(mobile ? "mobile/apple/SeenMobile/SeenMobile/Assets.xcassets/AppIcon.appiconset/Seen.png" : "extension/icon-\(size).png")
    try png.write(to: file)
    print("\(file.path): \(png.count) bytes")
}
