import AppKit
import ServiceManagement

struct WindowQuota: Decodable {
    let label: String
    let usedPercent: Double
    let resetsAt: Double?
    let display: String?
    var showsUsed: Bool { display == "used" }
    var remaining: Int { Int((100 - min(100, max(0, usedPercent))).rounded(.down)) }
    var shownPercent: Int { showsUsed ? Int(min(100, max(0, usedPercent)).rounded()) : remaining }
    var usable: Bool { usedPercent.isFinite && usedPercent >= 0 && (resetsAt == nil || resetsAt?.isFinite == true) }
}

struct ProviderSnapshot: Decodable {
    let ok: Bool
    let windows: [WindowQuota]?
    let error: String?
}

struct Snapshot: Decodable {
    let codex: ProviderSnapshot?
    let claude: ProviderSnapshot?
    let grok: ProviderSnapshot?
}

struct Reading {
    var headlineLabel: String? = nil
    var windows: [WindowQuota] = []
    var updatedAt: Date?
    var error: String?
    var refreshing = false
    var headline: WindowQuota? {
        if let headlineLabel {
            return windows.first { $0.label == headlineLabel && !$0.showsUsed }
        }
        let allowances = windows.filter { !$0.showsUsed }
        return (allowances.isEmpty ? windows : allowances).min { $0.remaining < $1.remaining }
    }
    var stale: Bool { error != nil || (updatedAt.map { Date().timeIntervalSince($0) > 600 } ?? false) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private var codex = Reading(headlineLabel: "5-hour window")
    private var claude = Reading(headlineLabel: "5-hour window")
    private var grok = Reading()
    private var refreshing = false
    private var timer: Timer?
    private static let providers = ["Codex", "Claude", "Grok"]
    private let hiddenKey = "HeadroomHiddenInMenuBar"
    private var hidden: Set<String> {
        get { Set(UserDefaults.standard.stringArray(forKey: hiddenKey) ?? []).intersection(Self.providers) }
        set { UserDefaults.standard.set(Self.providers.filter { newValue.contains($0) }, forKey: hiddenKey) }
    }
    private var codexLogo: NSImage?
    private var claudeLogo: NSImage?
    private var grokLogo: NSImage?
    private let refreshNote = Notification.Name("com.headroom.usage.refresh")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let checking = CommandLine.arguments.contains("--check")
        let bundleID = Bundle.main.bundleIdentifier ?? "com.headroom.usage"
        if !checking {
            let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            if others.contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
                DistributedNotificationCenter.default().post(name: refreshNote, object: nil)
                NSApp.terminate(nil)
                return
            }
            registerLoginItemIfNeeded()
        }
        codexLogo = logo("codex")
        claudeLogo = logo("claude")
        grokLogo = logo("grok")
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.imagePosition = .imageOnly
        menu.delegate = self
        item.menu = menu
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(externalRefresh), name: refreshNote, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(woke), name: NSWorkspace.didWakeNotification, object: nil)
        updateStatus()
        rebuildMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in self?.refresh() }
    }

    private func logo(_ name: String) -> NSImage? {
        guard let url = Bundle.main.url(forResource: name, withExtension: "png"),
              let data = try? Data(contentsOf: url),
              let rep = NSBitmapImageRep(data: data) else { return nil }
        rep.size = NSSize(width: 16, height: 16)
        let image = NSImage(size: NSSize(width: 16, height: 16))
        image.addRepresentation(rep)
        return image
    }

    private func registerLoginItemIfNeeded() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: "HeadroomDidRegisterLoginItem") else { return }
        do {
            try SMAppService.mainApp.register()
            defaults.set(true, forKey: "HeadroomDidRegisterLoginItem")
        } catch { }
    }

    @objc private func woke() { refresh() }
    @objc private func externalRefresh() { refresh() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        refresh()
        return false
    }
    func menuWillOpen(_ menu: NSMenu) { rebuildMenu() }

    private func described(_ reading: Reading, _ name: String) -> String {
        if let window = reading.headline {
            let which = reading.windows.count > 1 ? " in \(window.label)" : ""
            return "\(name) \(window.remaining)% remaining\(which)\(reading.stale ? ", saved reading" : "")"
        }
        return reading.refreshing ? "\(name) checking" : "\(name) unavailable"
    }

    private func glyph(_ reading: Reading) -> String {
        if let window = reading.headline { return "\(window.remaining)%\(reading.stale ? "!" : "")" }
        return reading.refreshing ? "…" : "—"
    }

    private func updateStatus() {
        let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.black]
        let hidden = self.hidden
        let groups: [(NSImage?, String)] = [("Codex", codexLogo, glyph(codex)), ("Claude", claudeLogo, glyph(claude)), ("Grok", grokLogo, glyph(grok))]
            .filter { !hidden.contains($0.0) }.map { ($0.1, $0.2) }
        func width(_ text: String) -> CGFloat { ceil((text as NSString).size(withAttributes: attributes).width) }
        let mark: CGFloat = 15
        let gap: CGFloat = 3
        let group: CGFloat = 8
        let height: CGFloat = 18
        let total = groups.reduce(CGFloat(0)) { $0 + mark + gap + width($1.1) } + group * CGFloat(groups.count - 1)
        let image = NSImage(size: NSSize(width: total, height: height))
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(x: 0, y: 0, width: total, height: height).fill()
        NSGraphicsContext.current?.imageInterpolation = .high
        var x: CGFloat = 0
        for (index, (logo, text)) in groups.enumerated() {
            if index > 0 { x += group }
            logo?.draw(in: NSRect(x: x, y: (height - mark) / 2, width: mark, height: mark),
                       from: .zero, operation: .sourceOver, fraction: 1)
            x += mark + gap
            let size = (text as NSString).size(withAttributes: attributes)
            (text as NSString).draw(at: NSPoint(x: x, y: (height - size.height) / 2), withAttributes: attributes)
            x += size.width
        }
        image.unlockFocus()
        image.isTemplate = true
        item.button?.image = image
        let label = "\(described(codex, "Codex")), \(described(claude, "Claude")), \(described(grok, "Grok"))"
        item.button?.toolTip = label
        item.button?.setAccessibilityLabel(label)
    }

    /// Menu rows are disabled, and a disabled row will not recolor a template image.
    /// Paint the mark with the menu's own label color so it stays visible on a dark menu.
    private func menuImage(_ source: NSImage?) -> NSImage? {
        guard let source else { return nil }
        let image = NSImage(size: source.size)
        var color = NSColor.white
        NSApp.effectiveAppearance.performAsCurrentDrawingAppearance {
            color = NSColor.labelColor.usingColorSpace(.deviceRGB) ?? .white
        }
        image.lockFocus()
        let rect = NSRect(origin: .zero, size: source.size)
        source.draw(in: rect)
        color.set()
        rect.fill(using: .sourceAtop)
        image.unlockFocus()
        return image
    }

    @discardableResult
    private func line(_ title: String, mark: NSImage? = nil, tip: String? = nil) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        entry.image = menuImage(mark)
        entry.toolTip = tip
        entry.isEnabled = false
        menu.addItem(entry)
        return entry
    }

    @discardableResult
    private func action(_ title: String, _ selector: Selector, key: String = "") -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        entry.target = self
        menu.addItem(entry)
        return entry
    }

    private func resetSummary(_ resetsAt: Double) -> (short: String, exact: String) {
        let reset = Date(timeIntervalSince1970: resetsAt)
        let minutes = max(0, Int(ceil(reset.timeIntervalSinceNow / 60)))
        let short: String
        if minutes == 0 { short = "due" }
        else if minutes < 60 { short = "\(minutes)m" }
        else if minutes < 1440 { short = "\(minutes / 60)h \(minutes % 60)m" }
        else {
            let hours = (minutes % 1440) / 60
            short = hours == 0 ? "\(minutes / 1440)d" : "\(minutes / 1440)d \(hours)h"
        }
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return (short, formatter.string(from: reset))
    }

    private func windowLine(_ window: WindowQuota, includeLabel: Bool) -> (title: String, tip: String) {
        var parts: [String] = []
        if includeLabel { parts.append(window.label.replacingOccurrences(of: " window", with: "")) }
        parts.append(window.showsUsed ? "\(window.shownPercent)% used" : "\(window.shownPercent)%")
        var tip = window.showsUsed ? "Used \(window.shownPercent)% of the weekly pool" : "\(window.remaining)% left"
        if let resetsAt = window.resetsAt {
            let reset = resetSummary(resetsAt)
            parts.append(reset.short)
            tip = "\(window.remaining)% left, resets \(reset.exact)"
        }
        return (parts.joined(separator: " · "), tip)
    }

    private func addProvider(_ title: String, _ reading: Reading, mark: NSImage?) {
        if reading.windows.isEmpty {
            line(title, mark: mark)
            line(reading.refreshing ? "Checking…" : "Unavailable")
        } else if reading.windows.count == 1, let window = reading.windows.first {
            let row = windowLine(window, includeLabel: false)
            line("\(title) · \(row.title)", mark: mark, tip: row.tip)
        } else {
            line(title, mark: mark)
            for window in reading.windows {
                let row = windowLine(window, includeLabel: true)
                line(row.title, tip: row.tip)
            }
        }
        if let error = reading.error {
            let message = NSMenuItem(title: error.count > 72 ? String(error.prefix(69)) + "…" : error, action: nil, keyEquivalent: "")
            message.isEnabled = false
            message.toolTip = error
            menu.addItem(message)
        }
    }

    private func rebuildMenu() {
        menu.removeAllItems()
        addProvider("Codex", codex, mark: codexLogo)
        menu.addItem(.separator())
        addProvider("Claude", claude, mark: claudeLogo)
        menu.addItem(.separator())
        addProvider("Grok", grok, mark: grokLogo)
        if codex.error != nil || claude.error != nil || grok.error != nil,
           let updated = [codex.updatedAt, claude.updatedAt, grok.updatedAt].compactMap({ $0 }).max() {
            let formatter = DateFormatter()
            formatter.timeStyle = .short
            line("Saved \(formatter.string(from: updated))")
        }
        menu.addItem(.separator())
        action(refreshing ? "Refreshing…" : "Refresh", #selector(refreshClicked), key: "r").isEnabled = !refreshing
        let showMenu = NSMenu()
        showMenu.autoenablesItems = false
        let hiddenNow = hidden
        for name in Self.providers {
            let entry = NSMenuItem(title: name, action: #selector(toggleShown(_:)), keyEquivalent: "")
            entry.target = self
            entry.representedObject = name
            entry.state = hiddenNow.contains(name) ? .off : .on
            // Keep at least one provider in the menu bar so the item never disappears.
            entry.isEnabled = hiddenNow.contains(name) || hiddenNow.count < Self.providers.count - 1
            showMenu.addItem(entry)
        }
        let show = NSMenuItem(title: "Show in Menu Bar", action: nil, keyEquivalent: "")
        show.submenu = showMenu
        menu.addItem(show)
        action("Open Codex usage…", #selector(openCodex))
        action("Open Claude usage…", #selector(openClaude))
        action("Open Grok usage…", #selector(openGrok))
        let login = action("Open at Login", #selector(toggleLogin))
        switch SMAppService.mainApp.status {
        case .enabled: login.state = .on
        case .requiresApproval:
            login.title = "Open at Login — allow in Settings"
        default: login.state = .off
        }
        menu.addItem(.separator())
        action("Quit", #selector(quit), key: "q")
    }

    @objc private func refreshClicked() { refresh() }
    @objc private func openCodex() { NSWorkspace.shared.open(URL(string: "https://chatgpt.com/codex")!) }
    @objc private func openClaude() { NSWorkspace.shared.open(URL(string: "https://claude.ai/settings/usage")!) }
    @objc private func openGrok() { NSWorkspace.shared.open(URL(string: "https://grok.com/dashboard")!) }
    @objc private func toggleLogin() {
        let service = SMAppService.mainApp
        if service.status == .requiresApproval {
            openLoginSettings()
            return
        }
        do {
            if service.status == .enabled { try service.unregister() }
            else {
                try service.register()
                UserDefaults.standard.set(true, forKey: "HeadroomDidRegisterLoginItem")
            }
        } catch { openLoginSettings() }
        rebuildMenu()
    }
    private func openLoginSettings() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension")!)
    }
    @objc private func quit() { NSApp.terminate(nil) }
    @objc private func toggleShown(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        var set = hidden
        if set.contains(name) { set.remove(name) }
        else if set.count < Self.providers.count - 1 { set.insert(name) }
        hidden = set
        updateStatus()
        rebuildMenu()
    }

    private func apply(_ snapshot: ProviderSnapshot?, to reading: inout Reading, fallback: String) {
        let windows = snapshot?.windows?.filter { $0.usable && !$0.label.isEmpty } ?? []
        if snapshot?.ok == true, !windows.isEmpty {
            reading.windows = windows
            reading.updatedAt = Date()
            reading.error = nil
        } else {
            reading.error = snapshot?.error ?? fallback
        }
    }

    private func refresh() {
        guard !refreshing else { return }
        refreshing = true
        codex.refreshing = true
        claude.refreshing = true
        grok.refreshing = true
        updateStatus()
        rebuildMenu()
        let resources = Bundle.main.resourceURL!
        let pinned = Bundle.main.object(forInfoDictionaryKey: "HeadroomPythonPath") as? String ?? "/usr/bin/python3"
        let python = FileManager.default.isExecutableFile(atPath: pinned) ? pinned : "/usr/bin/python3"
        let codexBin = Bundle.main.object(forInfoDictionaryKey: "HeadroomCodexPath") as? String ?? ""
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = ["-B", resources.appendingPathComponent("usage.py").path]
            process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
            var environment = ProcessInfo.processInfo.environment
            if !codexBin.isEmpty { environment["HEADROOM_CODEX_BIN"] = codexBin }
            process.environment = environment
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            var result: Snapshot?
            do {
                try process.run()
                DispatchQueue.global().asyncAfter(deadline: .now() + 32) {
                    if process.isRunning { process.terminate() }
                }
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if data.count <= 65536 { result = try? JSONDecoder().decode(Snapshot.self, from: data) }
            } catch { }
            let snapshot = result
            DispatchQueue.main.async {
                guard let self else { return }
                self.apply(snapshot?.codex, to: &self.codex, fallback: "Could not read Codex usage. Check codex login and refresh.")
                self.apply(snapshot?.claude, to: &self.claude, fallback: "Could not read Claude usage. Check your Claude Code login and refresh.")
                self.apply(snapshot?.grok, to: &self.grok, fallback: "Could not read Grok usage. Check your local Cursor login.")
                self.refreshing = false
                self.codex.refreshing = false
                self.claude.refreshing = false
                self.grok.refreshing = false
                self.updateStatus()
                self.rebuildMenu()
                if CommandLine.arguments.contains("--check") { self.finishCheck() }
            }
        }
    }

    private func finishCheck() {
        var report: [String: Any] = [
            "label": item.button?.accessibilityLabel() ?? "",
            "items": menu.items.filter { !$0.isSeparatorItem }.map { $0.title },
            "logos": codexLogo != nil && claudeLogo != nil && grokLogo != nil,
            "menuBarOnly": NSApp.activationPolicy() == .accessory,
            "connected": [codex, claude, grok].allSatisfy { $0.error == nil && $0.headline != nil }
        ]
        if let remaining = codex.headline?.remaining { report["codex"] = remaining }
        if let remaining = claude.headline?.remaining { report["claude"] = remaining }
        if let remaining = grok.headline?.remaining { report["grok"] = remaining }
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        }
        exit(report["connected"] as? Bool == true ? 0 : 1)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
