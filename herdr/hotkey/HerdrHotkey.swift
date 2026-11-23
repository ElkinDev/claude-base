// HerdrHotkey: the global Ctrl+Option+Cmd+N key that opens Herdr on macOS, the twin of the Windows Start Menu
// shortcut that setup-hotkey.ps1 makes. setup-hotkey.sh compiles it into a small background app and a LaunchAgent
// starts it at every sign-in.
//
// Usage: HerdrHotkey <launcher> [folder]
//   <launcher>  the script the key runs with /bin/sh (launch-herdr.sh, copied into the app)
//   [folder]    passed on to the launcher: where Herdr opens; the home folder when absent
//
// The key is registered with RegisterEventHotKey, the Carbon call macOS keeps for exactly this. It needs no
// Accessibility permission, because it never reads keystrokes: the system tells it when its own combination is
// pressed, in whatever app is in front. Ctrl+Option+Cmd+N because no macOS or Finder shortcut uses it (Apple's
// keyboard shortcut list, support.apple.com/en-us/102650); Ctrl+Cmd+N is Finder's New Folder with Selection.
import Cocoa
import Carbon.HIToolbox

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    FileHandle.standardError.write("usage: HerdrHotkey <launcher> [folder]\n".data(using: .utf8)!)
    exit(2)
}
let launcher = arguments[1]
let folder: String? = arguments.count >= 3 ? arguments[2] : nil

func say(_ line: String) {
    // One line per event in the LaunchAgent's log, so a key that does nothing can be traced.
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardError.write("\(stamp) \(line)\n".data(using: .utf8)!)
}

func openHerdr() {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/sh")
    process.arguments = [launcher] + (folder.map { [$0] } ?? [])
    do {
        try process.run()
        say("key pressed: started \(launcher)")
    } catch {
        say("key pressed: could not start \(launcher): \(error)")
    }
}

var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
let handlerStatus = InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
    openHerdr()
    return noErr
}, 1, &eventType, nil, nil)
guard handlerStatus == noErr else {
    say("could not install the key handler (OSStatus \(handlerStatus))")
    exit(1)
}

var hotKeyRef: EventHotKeyRef?
let hotKeyID = EventHotKeyID(signature: OSType(0x4852_4452), id: 1)  // 'HRDR'
let modifiers = UInt32(controlKey | optionKey | cmdKey)
let registerStatus = RegisterEventHotKey(UInt32(kVK_ANSI_N), modifiers, hotKeyID, GetApplicationEventTarget(), 0,
                                         &hotKeyRef)
guard registerStatus == noErr else {
    // eventHotKeyExistsErr (-9878): another app already holds the combination.
    say("could not register Ctrl+Option+Cmd+N (OSStatus \(registerStatus)); another app may hold it")
    exit(1)
}
say("registered Ctrl+Option+Cmd+N -> \(launcher)")

// An accessory app, the policy menu bar utilities use: no Dock icon and no window, while the window server still
// hands it the key. A background-only app (.prohibited) registered the key and was never told it was pressed.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
