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
    let inset = CGFloat(size) * 0.045
    let corner = CGFloat(size) * 0.24
    let path = NSBezierPath(roundedRect: rect.insetBy(dx: inset, dy: inset), xRadius: corner, yRadius: corner)

    let gradient = NSGradient(colors: [
        NSColor(calibratedRed: 0.10, green: 0.45, blue: 0.98, alpha: 1),
        NSColor(calibratedRed: 0.00, green: 0.68, blue: 0.58, alpha: 1)
    ])!
    gradient.draw(in: path, angle: -35)

    NSColor.white.withAlphaComponent(0.26).setStroke()
    path.lineWidth = max(1, CGFloat(size) * 0.02)
    path.stroke()

    let panel = NSRect(x: CGFloat(size) * 0.20, y: CGFloat(size) * 0.23, width: CGFloat(size) * 0.60, height: CGFloat(size) * 0.54)
    let panelPath = NSBezierPath(roundedRect: panel, xRadius: CGFloat(size) * 0.055, yRadius: CGFloat(size) * 0.055)
    NSColor.white.withAlphaComponent(0.94).setFill()
    panelPath.fill()

    NSColor(calibratedRed: 0.10, green: 0.45, blue: 0.98, alpha: 0.18).setFill()
    for index in 0..<3 {
        let y = panel.minY + CGFloat(size) * (0.12 + CGFloat(index) * 0.12)
        let row = NSBezierPath(roundedRect: NSRect(x: panel.minX + CGFloat(size) * 0.07, y: y, width: panel.width - CGFloat(size) * 0.14, height: CGFloat(size) * 0.035), xRadius: CGFloat(size) * 0.018, yRadius: CGFloat(size) * 0.018)
        row.fill()
    }

    let graph = NSBezierPath()
    graph.move(to: NSPoint(x: CGFloat(size) * 0.28, y: CGFloat(size) * 0.58))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.40, y: CGFloat(size) * 0.50))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.50, y: CGFloat(size) * 0.56))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.62, y: CGFloat(size) * 0.42))
    graph.line(to: NSPoint(x: CGFloat(size) * 0.72, y: CGFloat(size) * 0.48))
    NSColor(calibratedRed: 0.05, green: 0.40, blue: 0.95, alpha: 1).setStroke()
    graph.lineWidth = max(2, CGFloat(size) * 0.042)
    graph.lineCapStyle = .round
    graph.lineJoinStyle = .round
    graph.stroke()

    let dotRadius = CGFloat(size) * 0.035
    for point in [
        NSPoint(x: CGFloat(size) * 0.28, y: CGFloat(size) * 0.58),
        NSPoint(x: CGFloat(size) * 0.40, y: CGFloat(size) * 0.50),
        NSPoint(x: CGFloat(size) * 0.50, y: CGFloat(size) * 0.56),
        NSPoint(x: CGFloat(size) * 0.62, y: CGFloat(size) * 0.42),
        NSPoint(x: CGFloat(size) * 0.72, y: CGFloat(size) * 0.48)
    ] {
        let dotRect = NSRect(x: point.x - dotRadius, y: point.y - dotRadius, width: dotRadius * 2, height: dotRadius * 2)
        NSColor(calibratedRed: 0.00, green: 0.70, blue: 0.55, alpha: 1).setFill()
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
