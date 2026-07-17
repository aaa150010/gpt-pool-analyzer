import Cocoa

let output = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.removeItem(at: output)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

let entries: [(String, Int)] = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024)
]

for (name, size) in entries {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()

    let rect = NSRect(x: 0, y: 0, width: size, height: size)
    let corner = CGFloat(size) * 0.22
    let path = NSBezierPath(roundedRect: rect.insetBy(dx: CGFloat(size) * 0.035, dy: CGFloat(size) * 0.035), xRadius: corner, yRadius: corner)

    let gradient = NSGradient(colors: [
        NSColor(calibratedRed: 0.02, green: 0.64, blue: 0.74, alpha: 1),
        NSColor(calibratedRed: 0.06, green: 0.33, blue: 0.90, alpha: 1)
    ])!
    gradient.draw(in: path, angle: 45)

    NSColor.white.withAlphaComponent(0.16).setStroke()
    path.lineWidth = max(1, CGFloat(size) * 0.018)
    path.stroke()

    let title = "GPT"
    let titleSize = CGFloat(size) * 0.23
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.boldSystemFont(ofSize: titleSize),
        .foregroundColor: NSColor.white
    ]
    let textSize = title.size(withAttributes: attrs)
    title.draw(at: NSPoint(x: (CGFloat(size) - textSize.width) / 2, y: CGFloat(size) * 0.66), withAttributes: attrs)

    let lensCenter = NSPoint(x: CGFloat(size) * 0.38, y: CGFloat(size) * 0.36)
    let lensRadius = CGFloat(size) * 0.13
    let lensRect = NSRect(
        x: lensCenter.x - lensRadius,
        y: lensCenter.y - lensRadius,
        width: lensRadius * 2,
        height: lensRadius * 2
    )
    let lens = NSBezierPath(ovalIn: lensRect)
    NSColor.white.withAlphaComponent(0.92).setStroke()
    lens.lineWidth = max(2, CGFloat(size) * 0.035)
    lens.stroke()

    let handle = NSBezierPath()
    handle.move(to: NSPoint(x: lensCenter.x + lensRadius * 0.72, y: lensCenter.y - lensRadius * 0.72))
    handle.line(to: NSPoint(x: CGFloat(size) * 0.58, y: CGFloat(size) * 0.16))
    handle.lineWidth = max(2, CGFloat(size) * 0.04)
    handle.lineCapStyle = .round
    handle.stroke()

    let graph = NSBezierPath()
    graph.move(to: NSPoint(x: CGFloat(size) * 0.52, y: CGFloat(size) * 0.34))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.62, y: CGFloat(size) * 0.44))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.70, y: CGFloat(size) * 0.38))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.82, y: CGFloat(size) * 0.53))
    NSColor.white.setStroke()
    graph.lineWidth = max(2, CGFloat(size) * 0.042)
    graph.lineCapStyle = .round
    graph.lineJoinStyle = .round
    graph.stroke()

    let dotRadius = CGFloat(size) * 0.035
    for point in [
        NSPoint(x: CGFloat(size) * 0.52, y: CGFloat(size) * 0.34),
        NSPoint(x: CGFloat(size) * 0.62, y: CGFloat(size) * 0.44),
        NSPoint(x: CGFloat(size) * 0.70, y: CGFloat(size) * 0.38),
        NSPoint(x: CGFloat(size) * 0.82, y: CGFloat(size) * 0.53)
    ] {
        let dotRect = NSRect(x: point.x - dotRadius, y: point.y - dotRadius, width: dotRadius * 2, height: dotRadius * 2)
        NSColor.white.setFill()
        NSBezierPath(ovalIn: dotRect).fill()
    }

    image.unlockFocus()

    guard let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let data = rep.representation(using: .png, properties: [:]) else {
        fatalError("Failed to create icon png")
    }
    try data.write(to: output.appendingPathComponent(name))
}
