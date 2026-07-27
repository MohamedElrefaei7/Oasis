//
//  SummonPanel.swift
//  Oasis
//
//  The floating query line the global hotkey summons, and the controller that
//  shows, positions and tears it down.
//
//  Per the sketch this panel is **only a query line**. It renders no results —
//  it hands the query to the main window (`AppSearchCoordinator`) and gets out
//  of the way. Everything hard about it is AppKit window configuration, and the
//  comments below say which line buys which behaviour, because every one of
//  them is load-bearing and none of them is obvious from the code.
//

import AppKit
import OSLog
import SwiftUI

/// The panel itself. Three overrides, and the first one is the whole ballgame.
final class SummonPanel: NSPanel {

    /// **The classic Spotlight-panel bug lives here.**
    ///
    /// `NSWindow.canBecomeKey` returns `false` for a borderless window, and a
    /// window that is not key never gets keyboard events — so the text field
    /// appears, shows a caret, and silently swallows every keystroke. There is
    /// no error and nothing in the log; it just doesn't type. Returning `true`
    /// is the entire fix, and it cannot be expressed any other way (the
    /// property is read-only and AppKit consults it directly).
    override var canBecomeKey: Bool { true }

    /// Deliberately **not** `true`. Key is what routes keystrokes; main is what
    /// marks the app's principal document window, and claiming it would make
    /// the panel — not the search results window — the thing macOS considers
    /// Oasis's focus. Key without main is exactly the split a launcher wants.
    override var canBecomeMain: Bool { false }

    /// Escape, belt to the local monitor's braces.
    ///
    /// A SwiftUI `TextField` puts an `NSTextView` field editor in the responder
    /// chain, and that field editor handles `cancelOperation(_:)` itself
    /// (autocomplete dismissal), so Escape does not reliably reach the window.
    /// `SummonPanelController` installs a key-down monitor for that reason;
    /// this override catches the case where the field editor isn't first
    /// responder — an empty panel clicked on its background.
    var onCancel: (() -> Void)?

    override func cancelOperation(_ sender: Any?) {
        onCancel?()
    }
}

/// Shows, hides, positions and owns the panel.
@MainActor
final class SummonPanelController: NSObject, NSWindowDelegate {

    private static let log = Logger(subsystem: "com.oasis.app", category: "summon")

    /// Wide enough to read a sentence-long query without wrapping; Spotlight is
    /// 680pt at default width and there's no reason to disagree.
    private static let panelWidth: CGFloat = 680

    private let coordinator: AppSearchCoordinator

    private var panel: SummonPanel?
    private var hostingView: NSHostingView<SummonView>?
    private var escapeMonitor: Any?

    /// `orderOut` makes the panel resign key, which is also the click-away
    /// dismissal signal — without this flag the two paths re-enter each other.
    private var isDismissing = false

    init(coordinator: AppSearchCoordinator) {
        self.coordinator = coordinator
    }

    var isVisible: Bool { panel?.isVisible ?? false }

    // MARK: - Show / hide

    /// What the hotkey calls. Pressing it again while the panel is up puts it
    /// away, which is what every launcher on macOS does.
    func toggle() {
        if isVisible {
            Self.log.debug("summon toggled off")
            dismiss()
        } else {
            show()
        }
    }

    func show() {
        // Built fresh every time, not cached and re-shown. The panel carries
        // SwiftUI `@State` (the query) and `@FocusState`, and recreating is the
        // only way to guarantee both start clean — a reused panel shows the
        // last query and can come back with focus already spent.
        dismiss()

        let panel = SummonPanel(
            contentRect: NSRect(x: 0, y: 0, width: Self.panelWidth, height: 60),
            // `.nonactivatingPanel` is the one that matters: it lets the panel
            // take key focus **without activating Oasis**, so summoning from
            // another app doesn't yank that app's windows behind ours. Combined
            // with `canBecomeKey` above, that's "nonactivating but key".
            // (`.borderless` is a zero raw value, listed for intent.)
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )

        panel.isFloatingPanel = true
        // Window levels are system-wide, not per-app: `.floating` (3) draws
        // above every app's normal (0) windows, including the active one. This
        // is what makes the panel appear *over other apps* rather than only
        // over Oasis.
        panel.level = .floating
        panel.collectionBehavior = [
            // Follow the user to whatever Space they're on, and show over
            // fullscreen apps rather than forcing a Space switch. Spotlight's
            // exact behaviour, and the two flags that produce it.
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            // Not a window the user cycles to with ⌘` or sees in Exposé; it's
            // a transient prompt.
            .ignoresCycle,
        ]
        // A utility panel hides itself when its app deactivates. This one must
        // survive being summoned *from* another app, where Oasis is inactive by
        // definition — so no.
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.worksWhenModal = true
        // The rounded material shape is drawn by SwiftUI, so the window itself
        // must not paint a square opaque box behind it.
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.animationBehavior = .utilityWindow
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        panel.onCancel = { [weak self] in self?.dismiss() }

        let hostingView = NSHostingView(
            rootView: SummonView(
                controller: coordinator.controller,
                onSubmit: { [weak self] query in self?.submit(query) },
                onCancel: { [weak self] in self?.dismiss() },
                onLayoutChange: { [weak self] in self?.resizeToFit() }
            )
        )
        panel.contentView = hostingView
        self.hostingView = hostingView
        self.panel = panel

        resizeToFit()
        position(panel)

        // `orderFrontRegardless()`, not `makeKeyAndOrderFront(_:)`: the latter
        // will not raise a window belonging to an app that isn't active, which
        // is the only case this panel exists for. Order in front first, then
        // take key.
        panel.orderFrontRegardless()
        panel.makeKey()

        installEscapeMonitor()

        Self.log.notice("summon panel shown — key=\(panel.isKeyWindow, privacy: .public) level=\(panel.level.rawValue, privacy: .public) appActive=\(NSApp.isActive, privacy: .public)")
    }

    func dismiss() {
        guard let panel else { return }
        isDismissing = true
        defer { isDismissing = false }

        removeEscapeMonitor()
        panel.delegate = nil
        panel.orderOut(nil)
        panel.contentView = nil
        self.panel = nil
        self.hostingView = nil
    }

    // MARK: - Submit

    private func submit(_ query: String) {
        // **Hand off first, dismiss second, and the order is not cosmetic.**
        //
        // `NSApp.activate()` on macOS 14+ is cooperative: an app only gets to
        // pull itself in front of the app the user is actually using if it can
        // point at a user action that justifies it. While this panel is key,
        // Oasis has that claim — the user just typed into us. Dismissing first
        // surrenders it, and `activate()` then silently does nothing: measured,
        // 2026-07-27, the search ran and the results rendered behind Finder
        // with Finder still frontmost.
        coordinator.submitFromSummon(query)
        dismiss()
    }

    // MARK: - Dismissal signals

    /// Click-away. Anything that takes key focus from the panel — a click in
    /// another app, ⌘Tab, the menu bar — dismisses it. A summon panel that
    /// lingers after you've looked away is a bug, not a feature.
    func windowDidResignKey(_ notification: Notification) {
        guard !isDismissing else { return }
        Self.log.debug("summon panel resigned key — dismissing")
        dismiss()
    }

    /// Escape.
    ///
    /// A local monitor rather than SwiftUI's `.onExitCommand`, because the
    /// field editor backing a focused `TextField` consumes Escape first (it
    /// treats it as "cancel completion"). The monitor sees the event before the
    /// responder chain does, so it works with the field focused — which is the
    /// only state the panel is ever in.
    private func installEscapeMonitor() {
        removeEscapeMonitor()
        escapeMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.keyCode == 53 else { return event }  // 53 = Escape
            guard event.window === self.panel else { return event }
            MainActor.assumeIsolated { self.dismiss() }
            return nil  // swallow it; nothing downstream should see this Escape
        }
    }

    private func removeEscapeMonitor() {
        if let escapeMonitor {
            NSEvent.removeMonitor(escapeMonitor)
        }
        escapeMonitor = nil
    }

    // MARK: - Geometry

    /// Height follows the content: one row normally, a second row while the
    /// server is warming. Called again from the view when that changes, so a
    /// panel left open across the ready transition shrinks instead of leaving a
    /// band of empty material.
    private func resizeToFit() {
        guard let panel, let hostingView else { return }
        let height = hostingView.fittingSize.height
        guard height > 0, abs(panel.frame.height - height) > 0.5 else { return }

        let top = panel.frame.maxY
        var frame = panel.frame
        frame.size = NSSize(width: Self.panelWidth, height: height)
        // Grow downward from a fixed top edge, so the query line doesn't jump
        // under the cursor when the status row appears or disappears.
        frame.origin.y = top - height
        panel.setFrame(frame, display: true)
    }

    /// Upper third of the screen the mouse is on — where a summoned panel is
    /// expected, and high enough that it isn't hidden behind the pointer.
    private func position(_ panel: NSPanel) {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSApp.keyWindow?.screen
            ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }

        let size = panel.frame.size
        panel.setFrameOrigin(
            NSPoint(
                x: (visible.midX - size.width / 2).rounded(),
                y: (visible.maxY - visible.height * 0.28 - size.height).rounded()
            )
        )
    }
}
