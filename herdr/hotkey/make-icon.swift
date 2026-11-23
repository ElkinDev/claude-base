// make-icon.swift: draws HerdrHotkey.icns, the icon of the macOS key app, so the file in the repository can be made
// again. An original drawing (a terminal prompt on a rounded square), since Herdr ships no icon of its own.
//
//   xcrun --sdk macosx swiftc -swift-version 5 -o /tmp/make-icon herdr/hotkey/make-icon.swift
//   /tmp/make-icon herdr/hotkey/HerdrHotkey.icns
import AppKit

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write("usage: make-icon <out.icns>\n".data(using: .utf8)!)
    exit(2)
}
let output = URL(fileURLWithPath: CommandLine.arguments[1])

func draw(_ size: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(size)
    // The macOS icon grid: the shape fills 824 of 1024 points, with a corner of about 185.
    let inset = s * 100 / 1024
    let shape = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let path = NSBezierPath(roundedRect: shape, xRadius: s * 185 / 1024, yRadius: s * 185 / 1024)
    NSGradient(starting: NSColor(calibratedRed: 0.20, green: 0.16, blue: 0.40, alpha: 1),
               ending: NSColor(calibratedRed: 0.08, green: 0.36, blue: 0.62, alpha: 1))!.draw(in: path, angle: -60)
    let text = ">_" as NSString
    let font = NSFont.monospacedSystemFont(ofSize: s * 0.36, weight: .bold)
    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.white]
    let box = text.size(withAttributes: attributes)
    text.draw(at: NSPoint(x: (s - box.width) / 2, y: (s - box.height) / 2), withAttributes: attributes)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let iconset = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("HerdrHotkey-\(getpid()).iconset")
try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
// Up to 512 pixels: Login Items and the permission prompts draw it small, and the 1024 one would double the file.
for base in [16, 32, 128, 256] {
    try draw(base).write(to: iconset.appendingPathComponent("icon_\(base)x\(base).png"))
    try draw(base * 2).write(to: iconset.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
let iconutil = Process()
iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconutil.arguments = ["-c", "icns", iconset.path, "-o", output.path]
try iconutil.run()
iconutil.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)
exit(iconutil.terminationStatus)
