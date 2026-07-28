//
//  AppSearchCoordinator.swift
//  Oasis
//
//  The hand-off seam between the summon panel and the main window.
//
//  Steps 2–6 kept `SearchViewModel` inside `ContentView` as `@State`, which
//  made it **window-scoped** — it died with the window and there was no
//  instance at all while the app sat resident with no window open. The summon
//  panel has to write a query that the main window then renders, and the panel
//  can fire when the window doesn't exist, so the search state is hoisted here
//  and owned by the `AppDelegate` for the life of the process. The panel and
//  the window are two views onto this one object.
//

import AppKit
import Observation
import OSLog
import SwiftUI

@MainActor
@Observable
final class AppSearchCoordinator {

    /// The `Window` scene's id — the handle `openWindow` reopens a closed main
    /// window by.
    static let mainWindowID = "main"

    let controller: ServerController

    /// App-level, not window-level. Survives the window being closed, so a
    /// query typed into the panel is still there when the window comes back.
    let search: SearchViewModel

    /// The one `/api/status` reader in the process.
    ///
    /// It was already shared between the statistics panel and the roots Reindex
    /// re-scans, on the grounds that those two must never disagree. Settings ▸
    /// Folders is the third reader of the same list *and the only writer* — it
    /// is what removes a root — so it hoisted from `ContentView`'s `@State` to
    /// here. Two instances would let the folder list the user just edited and
    /// the folder list on the main window drift apart, and the main window is
    /// often on screen behind Settings while it happens.
    let status: StatusViewModel

    /// A query accepted while the server was still warming.
    ///
    /// Part D's whole point is that the hotkey works with no window open, and
    /// that includes the first thirty seconds after launch when the models are
    /// still loading. Rather than refuse the query (a dead panel) we hold it
    /// and run it the moment `ContentView` reaches its ready branch.
    private(set) var pendingQuery: String?

    /// SwiftUI's window opener, captured from a view because it can only be
    /// read out of the environment. `@ObservationIgnored` — nothing observes
    /// it, and `OpenWindowAction` isn't `Equatable` anyway.
    @ObservationIgnored private var openWindow: OpenWindowAction?

    private static let log = Logger(subsystem: "com.oasis.app", category: "summon")

    init(controller: ServerController) {
        self.controller = controller
        self.search = SearchViewModel(controller: controller)
        self.status = StatusViewModel(controller: controller)
    }

    // MARK: - Window plumbing

    /// Called from both `ContentView` and the menu-bar label, whichever appears
    /// first. Idempotent: the action is a value, and a later one is equivalent.
    func registerOpenWindow(_ action: OpenWindowAction) {
        openWindow = action
    }

    /// Bring Oasis to the front, reopening the main window if it was closed.
    ///
    /// Three steps, and all three are needed: `openWindow` reconstitutes the
    /// `Window` scene if the user closed it (a `WindowGroup` would spawn a
    /// *second* window instead, which is why the scene is a `Window`),
    /// `NSApp.activate()` moves Oasis in front of whatever app the user was in,
    /// and `makeKeyAndOrderFront` puts the window in front of Oasis's own
    /// windows. The trailing hop lets SwiftUI actually build the window before
    /// we go looking for it.
    func showMainWindow() {
        openWindow?(id: Self.mainWindowID)
        Self.activateIgnoringOtherApps()

        DispatchQueue.main.async {
            guard let window = Self.mainWindow() else {
                Self.log.error("no main window to front — openWindow was \(self.openWindow == nil ? "never registered" : "registered", privacy: .public)")
                return
            }
            window.makeKeyAndOrderFront(nil)
        }
    }

    /// The main window, if one exists.
    ///
    /// Identified by exclusion rather than by identifier: SwiftUI does not
    /// promise what it puts in `NSWindow.identifier` for a `Window` scene, but
    /// it is the only window in the app that can become *main* — the summon
    /// panel refuses (`canBecomeMain` is `false`) and the menu-bar extra's
    /// status-item window isn't a candidate either.
    private static func mainWindow() -> NSWindow? {
        NSApp.windows.first { $0.canBecomeMain && !($0 is SummonPanel) }
    }

    /// `NSApp.activate(ignoringOtherApps: true)`, called from a deprecated
    /// context so the (correct, general) deprecation warning stays quiet here.
    ///
    /// macOS 14 replaced it with *cooperative* activation: `NSApp.activate()`
    /// asks, and the system declines when Oasis isn't the app the user is
    /// working in. That is the right default for almost everything and exactly
    /// wrong for a summon — the user pressed ⌘⌥O in another app and hit Return,
    /// and the entire promise of the feature is that Oasis comes forward.
    ///
    /// Measured, 2026-07-27: with the cooperative call, Enter in the panel ran
    /// the search and the main window came back on screen, but **Finder stayed
    /// frontmost** — the results appeared behind the app the user had left.
    @available(macOS, deprecated: 14.0, message: "Deliberate: cooperative activation cannot front a summoned window.")
    private static func activateIgnoringOtherApps() {
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - The hand-off

    /// Enter in the summon panel. Everything Part C asks for, in order.
    ///
    /// The empty guard matters because the panel is a *query line* and Enter on
    /// an empty one should be a no-op dismissal, not a front-and-center window
    /// showing nothing.
    func submitFromSummon(_ raw: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            Self.log.debug("summon submitted empty — dismissing without searching")
            return
        }

        // Set the query first so the field is already populated whichever
        // branch `ContentView` renders — results, or the warming screen the
        // query is waiting behind.
        search.query = trimmed
        showMainWindow()

        if case .ready = controller.state {
            Self.log.notice("summon hand-off: searching immediately")
            search.submit()
        } else {
            // Not ready: the window shows step 1's warming/failed screen, and
            // the query rides along until it can actually run. No dead panel,
            // no request against a server that would 503.
            Self.log.notice("summon hand-off while \(String(describing: self.controller.state), privacy: .public) — holding the query")
            pendingQuery = trimmed
        }
    }

    /// Run a held query. Called by `ContentView` when it reaches `.ready`.
    func runPendingQueryIfNeeded() {
        guard let pending = pendingQuery else { return }
        pendingQuery = nil
        Self.log.notice("server ready — running the query held from the summon panel")
        search.query = pending
        search.submit()
    }
}
